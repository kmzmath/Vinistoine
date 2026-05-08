"""Lote 10 — mão/deck/reveal/trocas.

Ações cobertas:
- REVEAL_CARD_FROM_HAND
- REVEAL_LEFTMOST_AND_RIGHTMOST_HAND_CARDS
- SWAP_RANDOM_HAND_CARD_WITH_OPPONENT
- MOVE_HAND_CARD_TO_OPPONENT_DECK_TOP
- MOVE_HAND_CARDS_TO_DECK_AND_HEAL
- MOVE_ENEMY_MINION_TO_HAND_AND_SET_COST
"""
from __future__ import annotations

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, gen_id
from .cards import get_card
from . import targeting


def _hand_card_matches(card_in_hand: CardInHand, valid: list[str] | None) -> bool:
    """Valida filtros simples usados em targets de cartas na mão."""
    valid = valid or []
    if not valid:
        return True
    card = get_card(card_in_hand.card_id) or {}
    from .cards import card_has_tribe
    for v in valid:
        if v == "SPELL" and card.get("type") == "SPELL":
            return True
        if v == "MINION" and card.get("type") == "MINION":
            return True
        if v.startswith("CARD_WITH_TRIBE_"):
            tribe = v[len("CARD_WITH_TRIBE_"):]
            if card_has_tribe(card, tribe):
                return True
        if v.startswith("CARD_WITH_TAG_"):
            tag = v[len("CARD_WITH_TAG_"):]
            if tag in (card.get("tags") or []):
                return True
    return False


def _eligible_hand_cards(player: PlayerState, eff) -> list[CardInHand]:
    target = eff.get("target") or {}
    valid = target.get("valid") or []
    return [c for c in player.hand if _hand_card_matches(c, valid)]


def _marker_for_hand_card(state, ch: CardInHand, *, cost_override=None, cost_modifier_delta=0):
    """Move uma carta da mão para um deck preservando modificadores relevantes.

    O deck é list[str], então cartas modificadas precisam de um marker resolvido
    por effects.draw_card quando forem compradas.
    """
    card = get_card(ch.card_id) or {}
    preserve_current_cost_modifier = cost_override is None
    if cost_override is None:
        base = ch.cost_override if ch.cost_override is not None else card.get("cost", 0)
        cost_override = base
    if not hasattr(state, "deck_card_modifiers"):
        state.deck_card_modifiers = {}
    marker = f"{ch.card_id}__mod__{gen_id('')}"
    state.deck_card_modifiers[marker] = {
        "card_id": ch.card_id,
        "cost_override": cost_override,
        "cost_modifier": (int(ch.cost_modifier or 0) if preserve_current_cost_modifier else 0) + int(cost_modifier_delta or 0),
        "stat_modifier": dict(ch.stat_modifier or {}),
        "extra_tags": list(ch.extra_tags or []),
    }
    return marker


def _insert_cards(deck: list[str], cards: list[str], position: str):
    pos = (position or "TOP").upper()
    if pos == "BOTTOM":
        deck.extend(cards)
    elif pos == "MIDDLE":
        idx = len(deck) // 2
        deck[idx:idx] = cards
    else:
        deck[0:0] = cards


def register_lote10_handlers(handler):
    @handler("REVEAL_CARD_FROM_HAND")
    def _reveal_card_from_hand(state, eff, source_owner, source_minion, ctx):
        """Viní Estudioso: revela um feitiço da mão e reduz seu custo em 1."""
        p = state.players[source_owner]
        candidates = _eligible_hand_cards(p, eff)
        if not candidates:
            state.log_event({"type": "reveal_hand_no_valid_card", "player": source_owner})
            return

        if getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "reveal_card_from_hand",
                "owner": source_owner,
                "cards": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in candidates],
                "cost_modifier": int(eff.get("cost_modifier", -1) or -1),
            }
            state.log_event({"type": "choice_required", "kind": "reveal_card_from_hand", "player": source_owner})
            return

        chosen_id = ctx.get("hand_card_id") or ctx.get("chosen_hand_card")
        ch = next((c for c in candidates if c.instance_id == chosen_id), None) if chosen_id else candidates[0]
        ch.cost_modifier += int(eff.get("cost_modifier", -1) or -1)
        state.log_event({"type": "reveal_hand_card", "player": source_owner,
                         "instance_id": ch.instance_id, "card_id": ch.card_id,
                         "cost_modifier": int(eff.get("cost_modifier", -1) or -1)})

    @handler("REVEAL_LEFTMOST_AND_RIGHTMOST_HAND_CARDS")
    def _reveal_left_right(state, eff, source_owner, source_minion, ctx):
        """El Gusnabito: revela a carta mais à esquerda e mais à direita da mão alvo."""
        target = eff.get("target") or {}
        mode = target.get("mode")
        players = []
        if mode == "OPPONENT_HAND":
            players = [state.opponent_of(source_owner)]
        elif mode == "SELF_HAND":
            players = [state.players[source_owner]]
        else:
            players = [state.opponent_of(source_owner)]
        for p in players:
            if not p.hand:
                state.log_event({"type": "reveal_hand_edges", "player": p.player_id, "cards": []})
                continue
            cards = [p.hand[0]]
            if len(p.hand) > 1:
                cards.append(p.hand[-1])
            state.log_event({
                "type": "reveal_hand_edges",
                "player": p.player_id,
                "cards": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in cards],
            })

    @handler("SWAP_RANDOM_HAND_CARD_WITH_OPPONENT")
    def _swap_random_hand_card_with_opponent(state, eff, source_owner, source_minion, ctx):
        """Tomé: no fim do turno, troca uma carta aleatória da mão com o oponente."""
        me = state.players[source_owner]
        opp = state.opponent_of(source_owner)
        if not me.hand or not opp.hand:
            state.log_event({"type": "swap_random_hand_card_skipped", "player": source_owner})
            return
        my_idx = state.rng.randrange(len(me.hand))
        opp_idx = state.rng.randrange(len(opp.hand))
        my_card = me.hand.pop(my_idx)
        opp_card = opp.hand.pop(opp_idx)
        me.hand.append(opp_card)
        opp.hand.append(my_card)
        state.log_event({
            "type": "swap_random_hand_card",
            "player": source_owner,
            "received": {"instance_id": opp_card.instance_id, "card_id": opp_card.card_id},
            "given": {"instance_id": my_card.instance_id, "card_id": my_card.card_id},
        })

    @handler("MOVE_HAND_CARD_TO_OPPONENT_DECK_TOP")
    def _move_hand_card_to_opp_deck_top(state, eff, source_owner, source_minion, ctx):
        """Spiidinho Presenteador: põe uma carta da mão no topo do deck inimigo com custo aumentado."""
        p = state.players[source_owner]
        candidates = _eligible_hand_cards(p, eff)
        if not candidates:
            state.log_event({"type": "move_hand_card_no_valid_card", "player": source_owner})
            return

        if getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "move_hand_card_to_opponent_deck_top",
                "owner": source_owner,
                "cards": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in candidates],
                "cost_modifier": int(eff.get("cost_modifier", 0) or 0),
                "max_cost": eff.get("max_cost"),
            }
            state.log_event({"type": "choice_required", "kind": "move_hand_card_to_opponent_deck_top", "player": source_owner})
            return

        chosen_id = ctx.get("hand_card_id") or ctx.get("chosen_hand_card")
        ch = next((c for c in candidates if c.instance_id == chosen_id), None) if chosen_id else candidates[0]
        _move_hand_card_to_opp_deck_top_resolved(state, source_owner, ch,
                                                 int(eff.get("cost_modifier", 0) or 0), eff.get("max_cost"))

    @handler("MOVE_HAND_CARDS_TO_DECK_AND_HEAL")
    def _move_hand_cards_to_deck_and_heal(state, eff, source_owner, source_minion, ctx):
        """Viní Barman: move X cartas da mão para o deck e cura 3X."""
        p = state.players[source_owner]
        candidates = list(p.hand)
        if not candidates:
            return
        heal_per = int(eff.get("heal_per_card", 3) or 3)
        destination = eff.get("destination") or {}
        default_position = destination.get("position", "TOP")

        if getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "move_hand_cards_to_deck_and_heal",
                "owner": source_owner,
                "cards": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in candidates],
                "heal_per_card": heal_per,
                "default_position": default_position,
            }
            state.log_event({"type": "choice_required", "kind": "move_hand_cards_to_deck_and_heal", "player": source_owner})
            return

        ids = ctx.get("hand_card_ids") or ctx.get("chosen_hand_cards") or []
        if isinstance(ids, str):
            ids = [ids]
        if not ids:
            ids = [candidates[0].instance_id]
        _move_hand_cards_to_deck_and_heal_resolved(state, source_owner, ids, default_position, heal_per)

    @handler("MOVE_ENEMY_MINION_TO_HAND_AND_SET_COST")
    def _move_enemy_minion_to_hand_and_set_cost(state, eff, source_owner, source_minion, ctx):
        """Hora de Nanar: devolve lacaio inimigo para sua mão, custando set_cost."""
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        if not targets:
            return
        p = state.players[source_owner]
        set_cost = int(eff.get("set_cost", eff.get("cost", 1)) or 1)
        for t in list(targets):
            if not isinstance(t, Minion):
                continue
            owner = state.players[t.owner]
            if t not in owner.board:
                continue
            owner.board.remove(t)
            if len(p.hand) >= MAX_HAND_SIZE:
                state.log_event({"type": "burn_returned_minion", "player": source_owner, "card_id": t.card_id})
                continue
            ch = CardInHand(instance_id=gen_id("h_"), card_id=t.card_id, cost_override=set_cost)
            p.hand.append(ch)
            state.log_event({"type": "move_enemy_minion_to_hand",
                             "player": source_owner, "from_owner": owner.player_id,
                             "card_id": t.card_id, "instance_id": ch.instance_id,
                             "cost_override": set_cost})


def _move_hand_card_to_opp_deck_top_resolved(state, source_owner: int, ch: CardInHand,
                                             cost_modifier: int, max_cost):
    p = state.players[source_owner]
    opp = state.opponent_of(source_owner)
    if ch not in p.hand:
        return False
    card = get_card(ch.card_id) or {}
    base_cost = ch.effective_cost()
    new_cost = base_cost + int(cost_modifier or 0)
    if max_cost is not None:
        new_cost = min(int(max_cost), new_cost)
    p.hand.remove(ch)
    marker = _marker_for_hand_card(state, ch, cost_override=new_cost, cost_modifier_delta=0)
    opp.deck.insert(0, marker)
    state.log_event({"type": "move_hand_card_to_opponent_deck_top",
                     "player": source_owner, "opponent": opp.player_id,
                     "card_id": ch.card_id, "new_cost": new_cost})
    return True


def _move_hand_cards_to_deck_and_heal_resolved(state, source_owner: int, ids: list[str],
                                               position: str, heal_per: int):
    from .effects import heal_character
    p = state.players[source_owner]
    selected = []
    seen = set()
    for cid in ids:
        if cid in seen:
            continue
        ch = next((c for c in p.hand if c.instance_id == cid), None)
        if ch is None:
            continue
        selected.append(ch)
        seen.add(cid)
    if not selected:
        state.log_event({"type": "move_hand_cards_to_deck_and_heal", "player": source_owner,
                         "count": 0, "healed": 0})
        return True
    markers = []
    for ch in selected:
        p.hand.remove(ch)
        markers.append(_marker_for_hand_card(state, ch, cost_override=ch.effective_cost()))
    _insert_cards(p.deck, markers, position)
    healed = heal_character(state, p, heal_per * len(selected))
    state.log_event({"type": "move_hand_cards_to_deck_and_heal", "player": source_owner,
                     "count": len(selected), "position": position, "healed": healed,
                     "cards": [c.card_id for c in selected]})
    return True
