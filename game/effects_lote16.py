"""Lote 16 - mão/deck/zonas e mana extra.

Ações cobertas:
- DISCARD_UP_TO_CARDS
- DESTROY_MINIONS_IN_DECK_BY_COST
- MOVE_SELF_TO_ZONE
- REPLACE_FRIENDLY_MINION_DEATH_WITH_SHUFFLE_INTO_DECK
- SPEND_EXTRA_MANA_ADD_COPIES_TO_DECK
- SPEND_EXTRA_MANA_BUFF_SELF
"""
from __future__ import annotations

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, gen_id
from .cards import get_card
from . import targeting


def _insert_cards(deck: list[str], cards: list[str], position: str):
    pos = (position or "MIDDLE").upper()
    if pos == "BOTTOM":
        deck.extend(cards)
    elif pos == "TOP":
        deck[0:0] = cards
    else:
        idx = len(deck) // 2
        deck[idx:idx] = cards


def _deck_entry_card_id(state, entry: str) -> str:
    mods = getattr(state, "deck_card_modifiers", {}) or {}
    if entry in mods:
        return mods[entry].get("card_id", entry)
    return entry


def _deck_entry_card(state, entry: str) -> dict | None:
    return get_card(_deck_entry_card_id(state, entry))


def _hand_card_matches(ch: CardInHand, filter_desc: dict | None) -> bool:
    filter_desc = filter_desc or {}
    card = get_card(ch.card_id) or {}
    typ = filter_desc.get("type")
    if typ and card.get("type") != typ:
        return False
    tribe = filter_desc.get("tribe")
    if tribe:
        from .cards import card_has_tribe
        if not card_has_tribe(card, tribe):
            return False
    tag = filter_desc.get("tag")
    if tag and tag not in (card.get("tags") or []):
        return False
    return True


def _eligible_hand_cards(player: PlayerState, filter_desc: dict | None) -> list[CardInHand]:
    return [c for c in player.hand if _hand_card_matches(c, filter_desc)]


def _recruit_first_minions_from_deck(state, owner: int, count: int) -> list[Minion]:
    from .effects import summon_minion_from_card
    p = state.players[owner]
    recruited: list[Minion] = []
    for entry in list(p.deck):
        if len(recruited) >= count:
            break
        card = _deck_entry_card(state, entry)
        if not card or card.get("type") != "MINION":
            continue
        p.deck.remove(entry)
        # Recrutamento ignora custo/mods do marker e invoca a carta base.
        getattr(state, "deck_card_modifiers", {}).pop(entry, None)
        m = summon_minion_from_card(state, owner, card["id"])
        if m:
            recruited.append(m)
            state.log_event({"type": "recruit", "player": owner,
                             "card_id": m.card_id, "minion": m.instance_id})
    return recruited


def resolve_discard_up_to_cards(state, source_owner: int, card_instance_ids: list[str],
                                amount: int, filter_desc: dict | None) -> int:
    """Descarta até amount cartas válidas e recruta a mesma quantidade de lacaios."""
    p = state.players[source_owner]
    allowed = {c.instance_id: c for c in _eligible_hand_cards(p, filter_desc)}
    selected: list[CardInHand] = []
    seen = set()
    for cid in card_instance_ids or []:
        if cid in seen:
            continue
        seen.add(cid)
        ch = allowed.get(cid)
        if ch is not None and ch in p.hand:
            selected.append(ch)
        if len(selected) >= amount:
            break

    discarded = 0
    for ch in selected:
        p.hand.remove(ch)
        discarded += 1
        state.log_event({"type": "discard", "player": source_owner,
                         "instance_id": ch.instance_id, "card_id": ch.card_id})

    if discarded:
        _recruit_first_minions_from_deck(state, source_owner, discarded)
    state.log_event({"type": "discard_up_to_cards_resolved",
                     "player": source_owner, "discarded": discarded})
    return discarded


def resolve_destroy_minions_in_deck_by_cost(state, source_owner: int, threshold: int,
                                            comparison: str = "LESS_THAN_OR_EQUAL") -> int:
    p = state.players[source_owner]
    comparison = (comparison or "LESS_THAN_OR_EQUAL").upper()
    destroyed: list[str] = []
    keep: list[str] = []
    for entry in p.deck:
        card = _deck_entry_card(state, entry)
        should_destroy = False
        if card and card.get("type") == "MINION":
            cost = int(card.get("cost", 0) or 0)
            if comparison in ("LESS_THAN_OR_EQUAL", "LTE", "<="):
                should_destroy = cost <= threshold
            elif comparison in ("LESS_THAN", "LT", "<"):
                should_destroy = cost < threshold
            elif comparison in ("EQUAL", "EQ", "=="):
                should_destroy = cost == threshold
            elif comparison in ("GREATER_THAN_OR_EQUAL", "GTE", ">="):
                should_destroy = cost >= threshold
            elif comparison in ("GREATER_THAN", "GT", ">"):
                should_destroy = cost > threshold
        if should_destroy:
            destroyed.append(entry)
            getattr(state, "deck_card_modifiers", {}).pop(entry, None)
        else:
            keep.append(entry)
    p.deck = keep
    for entry in destroyed:
        state.log_event({"type": "destroy_card_in_deck",
                         "player": source_owner,
                         "card_id": _deck_entry_card_id(state, entry)})
    state.log_event({"type": "destroy_minions_in_deck_by_cost",
                     "player": source_owner, "threshold": threshold,
                     "destroyed": len(destroyed)})
    return len(destroyed)


def apply_spend_extra_mana_buff_self(state, source_owner: int, source_minion: Minion | None,
                                     amount: int) -> int:
    if not source_minion:
        return 0
    p = state.players[source_owner]
    x = max(0, min(int(amount or 0), p.mana))
    if x <= 0:
        return 0
    p.mana -= x
    source_minion.attack += x
    source_minion.max_health += x
    source_minion.health += x
    state.log_event({"type": "spend_extra_mana_buff_self",
                     "player": source_owner, "minion": source_minion.instance_id,
                     "amount": x})
    return x


def apply_spend_extra_mana_add_copies_to_deck(state, source_owner: int,
                                              source_minion: Minion | None,
                                              amount: int,
                                              copy_multiplier: int = 2,
                                              position: str = "MIDDLE") -> int:
    if not source_minion:
        return 0
    p = state.players[source_owner]
    x = max(0, min(int(amount or 0), p.mana))
    if x <= 0:
        return 0
    p.mana -= x
    count = x * int(copy_multiplier or 1)
    copies = [source_minion.card_id] * count
    _insert_cards(p.deck, copies, position)
    state.log_event({"type": "spend_extra_mana_add_copies_to_deck",
                     "player": source_owner, "card_id": source_minion.card_id,
                     "spent": x, "copies": count, "position": position})
    return x


def find_death_replacement_shuffle_into_deck(state, minion: Minion, owner: int) -> str | None:
    """Retorna posição se a morte desse lacaio deve virar shuffle no deck.

    Lamboia Religioso: seus lacaios, em vez de morrerem, voltam para o meio
    do deck. A aura funciona enquanto houver fonte aliada não silenciada.
    """
    for src in list(state.players[owner].board):
        if src.silenced:
            continue
        for eff in src.effects or []:
            if eff.get("action") != "REPLACE_FRIENDLY_MINION_DEATH_WITH_SHUFFLE_INTO_DECK":
                continue
            # target é FRIENDLY_MINIONS no JSON; o dono precisa bater.
            return (eff.get("position") or "MIDDLE").upper()
    return None


def register_lote16_handlers(handler):
    @handler("DISCARD_UP_TO_CARDS")
    def _discard_up_to_cards(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        amount = int(eff.get("amount", 1) or 1)
        filter_desc = eff.get("filter") or {}
        candidates = _eligible_hand_cards(p, filter_desc)
        if not candidates:
            state.log_event({"type": "discard_up_to_cards_no_candidates", "player": source_owner})
            return

        if getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "discard_up_to_cards",
                "owner": source_owner,
                "amount": amount,
                "filter": filter_desc,
                "cards": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in candidates],
            }
            state.log_event({"type": "choice_required", "kind": "discard_up_to_cards",
                             "player": source_owner})
            return

        ids = ctx.get("card_ids") or ctx.get("discard_ids")
        if not isinstance(ids, list):
            ids = [c.instance_id for c in candidates[:amount]]
        resolve_discard_up_to_cards(state, source_owner, ids, amount, filter_desc)

    @handler("DESTROY_MINIONS_IN_DECK_BY_COST")
    def _destroy_minions_in_deck_by_cost(state, eff, source_owner, source_minion, ctx):
        threshold = eff.get("cost_threshold", 0)
        comparison = eff.get("comparison", "LESS_THAN_OR_EQUAL")
        if threshold == "X":
            if getattr(state, "manual_choices", False) and ctx.get("x") is None:
                state.pending_choice = {
                    "choice_id": gen_id("choice_"),
                    "kind": "choose_deck_destroy_threshold",
                    "owner": source_owner,
                    "max_x": 10,
                    "comparison": comparison,
                }
                state.log_event({"type": "choice_required",
                                 "kind": "choose_deck_destroy_threshold",
                                 "player": source_owner})
                return
            threshold = ctx.get("x", ctx.get("extra_mana", state.players[source_owner].mana))
        resolve_destroy_minions_in_deck_by_cost(state, source_owner, int(threshold or 0), comparison)

    @handler("MOVE_SELF_TO_ZONE")
    def _move_self_to_zone(state, eff, source_owner, source_minion, ctx):
        if not source_minion:
            return
        zone = (ctx.get("zone") or ctx.get("target_zone") or "HAND").upper()
        valid = [z.upper() for z in (eff.get("valid_zones") or [])]
        if valid and zone not in valid and not (zone == "DECK" and "DECK" in valid):
            state.log_event({"type": "move_self_to_zone_invalid",
                             "minion": source_minion.instance_id, "zone": zone})
            return
        owner = state.players[source_minion.owner]
        if source_minion not in owner.board:
            return

        if zone == "HAND":
            owner.board.remove(source_minion)
            if len(owner.hand) < MAX_HAND_SIZE:
                owner.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=source_minion.card_id))
            state.log_event({"type": "move_self_to_zone", "zone": "HAND",
                             "minion": source_minion.instance_id})
        elif zone == "DECK":
            owner.board.remove(source_minion)
            pos = (ctx.get("position") or "MIDDLE").upper()
            _insert_cards(owner.deck, [source_minion.card_id], pos)
            state.log_event({"type": "move_self_to_zone", "zone": "DECK",
                             "position": pos, "minion": source_minion.instance_id})
        elif zone == "GRAVEYARD":
            owner.board.remove(source_minion)
            state.graveyard.append({"card_id": source_minion.card_id,
                                    "owner": source_minion.owner,
                                    "name": source_minion.name})
            state.log_event({"type": "move_self_to_zone", "zone": "GRAVEYARD",
                             "minion": source_minion.instance_id})
        elif zone == "BOARD_POSITION":
            pos = int(ctx.get("position", len(owner.board) - 1) or 0)
            owner.board.remove(source_minion)
            pos = max(0, min(pos, len(owner.board)))
            owner.board.insert(pos, source_minion)
            state.log_event({"type": "move_self_to_zone", "zone": "BOARD_POSITION",
                             "position": pos, "minion": source_minion.instance_id})

    @handler("REPLACE_FRIENDLY_MINION_DEATH_WITH_SHUFFLE_INTO_DECK")
    def _replace_friendly_minion_death_with_shuffle_into_deck(state, eff, source_owner, source_minion, ctx):
        # A aplicação real é feita em engine.cleanup; o handler só registra.
        if source_minion and "DEATH_REPLACEMENT_SHUFFLE_AURA" not in source_minion.tags:
            source_minion.tags.append("DEATH_REPLACEMENT_SHUFFLE_AURA")

    @handler("SPEND_EXTRA_MANA_ADD_COPIES_TO_DECK")
    def _spend_extra_mana_add_copies_to_deck(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        max_x = p.mana
        multiplier = int(eff.get("copy_multiplier", 2) or 2)
        target = eff.get("target") or {}
        default_position = target.get("position", "MIDDLE")

        if getattr(state, "manual_choices", False) and ctx.get("x") is None:
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "spend_extra_mana_add_copies_to_deck",
                "owner": source_owner,
                "max_x": max_x,
                "copy_multiplier": multiplier,
                "source_minion_id": source_minion.instance_id if source_minion else None,
                "default_position": default_position,
            }
            state.log_event({"type": "choice_required",
                             "kind": "spend_extra_mana_add_copies_to_deck",
                             "player": source_owner})
            return

        x = int(ctx.get("x", ctx.get("extra_mana", max_x)) or 0)
        position = (ctx.get("position") or default_position or "MIDDLE")
        if position == "CHOSEN":
            position = "MIDDLE"
        apply_spend_extra_mana_add_copies_to_deck(state, source_owner, source_minion,
                                                  x, multiplier, position)

    @handler("SPEND_EXTRA_MANA_BUFF_SELF")
    def _spend_extra_mana_buff_self(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        max_x = p.mana
        if getattr(state, "manual_choices", False) and ctx.get("x") is None:
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "spend_extra_mana_buff_self",
                "owner": source_owner,
                "max_x": max_x,
                "source_minion_id": source_minion.instance_id if source_minion else None,
            }
            state.log_event({"type": "choice_required",
                             "kind": "spend_extra_mana_buff_self",
                             "player": source_owner})
            return

        x = int(ctx.get("x", ctx.get("extra_mana", max_x)) or 0)
        apply_spend_extra_mana_buff_self(state, source_owner, source_minion, x)
