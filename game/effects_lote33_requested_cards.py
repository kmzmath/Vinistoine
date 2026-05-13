"""Lote 33 - cartas solicitadas pelo usuário.

Implementa efeitos customizados para Carvalho, Dedé Santana, Sub, Dinomancia,
LAGD e Maldição do Viní Sombrio.
"""
from __future__ import annotations

from .state import Minion, gen_id, MAX_BOARD_SIZE
from .cards import all_cards, get_card, is_collectible_card
from . import targeting


def _public_card_option(card_id: str) -> dict:
    card = get_card(card_id) or {}
    return {"card_id": card_id, "name": card.get("name", card_id)}


def _random_collectible_card_ids(state, amount: int = 3) -> list[str]:
    pool = [c["id"] for c in all_cards() if is_collectible_card(c.get("id"))]
    if not pool:
        return []
    return [state.rng.choice(pool) for _ in range(amount)]


def _finish_carvalho_deck_build(state, player_id: int, picked: list[str]):
    p = state.players[player_id]
    new_deck = list(picked)
    state.rng.shuffle(new_deck)
    p.deck = new_deck
    state.log_event({
        "type": "carvalho_deck_replaced",
        "player": player_id,
        "count": len(new_deck),
        "card_ids": list(new_deck),
    })


def _start_or_continue_carvalho_choice(state, player_id: int, picked: list[str], total: int):
    if len(picked) >= total:
        state.pending_choice = None
        _finish_carvalho_deck_build(state, player_id, picked)
        return
    options = _random_collectible_card_ids(state, 3)
    state.pending_choice = {
        "choice_id": gen_id("choice_"),
        "owner": player_id,
        "kind": "build_replacement_deck",
        "step": len(picked) + 1,
        "total": total,
        "picked": list(picked),
        "options": [_public_card_option(cid) for cid in options],
    }


def resolve_build_replacement_deck_choice(state, player_id: int, choice: dict, response: dict) -> tuple[bool, str]:
    options = list(choice.get("options") or [])
    try:
        idx = int(response.get("index", response.get("chosen_index", 0)))
    except Exception:
        return False, "Opção inválida"
    if idx < 0 or idx >= len(options):
        return False, "Opção inválida"
    picked = list(choice.get("picked") or [])
    picked.append(options[idx].get("card_id"))
    total = int(choice.get("total", 10) or 10)
    state.log_event({
        "type": "carvalho_pick",
        "player": player_id,
        "step": int(choice.get("step", len(picked)) or len(picked)),
        "card_id": options[idx].get("card_id"),
    })
    _start_or_continue_carvalho_choice(state, player_id, picked, total)
    return True, "OK"


def handle_return_spell_on_death(state, minion: Minion, owner: int):
    """Processa marcas do Sub quando o lacaio encantado morre."""
    keep = []
    for pm in state.pending_modifiers:
        if pm.get("kind") != "return_spell_to_deck_on_minion_death":
            keep.append(pm)
            continue
        if pm.get("minion_id") != minion.instance_id:
            keep.append(pm)
            continue
        spell_id = pm.get("card_id") or "sub"
        deck_owner = int(pm.get("owner", owner))
        state.players[deck_owner].deck.insert(0, spell_id)
        state.log_event({
            "type": "spell_returned_to_deck_on_death",
            "player": deck_owner,
            "minion": minion.instance_id,
            "card_id": spell_id,
            "position": "TOP",
        })
    state.pending_modifiers = keep


def register_lote33_requested_cards_handlers(handler):
    @handler("BUILD_REPLACEMENT_DECK_DISCOVER")
    def _build_replacement_deck_discover(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        destroyed = list(p.deck)
        p.deck = []
        total = int(eff.get("amount", 10) or 10)
        state.log_event({
            "type": "deck_destroyed_for_replacement",
            "player": source_owner,
            "count": len(destroyed),
        })
        if getattr(state, "manual_choices", False):
            _start_or_continue_carvalho_choice(state, source_owner, [], total)
            return
        picked = []
        for _ in range(total):
            options = _random_collectible_card_ids(state, 3)
            if not options:
                break
            picked.append(options[0])
        _finish_carvalho_deck_build(state, source_owner, picked)

    @handler("STEAL_OPPONENT_TOP_DECK_CARDS")
    def _steal_opponent_top_deck_cards(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 4) or 4)
        me = state.players[source_owner]
        opp = state.opponent_of(source_owner)
        stolen = opp.deck[:amount]
        if not stolen:
            return
        del opp.deck[:len(stolen)]
        me.deck = list(stolen) + me.deck
        state.log_event({
            "type": "reveal_and_steal_top_deck_cards",
            "player": source_owner,
            "opponent": opp.player_id,
            "card_ids": list(stolen),
            "amount": len(stolen),
        })

    @handler("BUFF_AND_RETURN_SPELL_ON_DEATH")
    def _buff_and_return_spell_on_death(state, eff, source_owner, source_minion, ctx):
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"),
                                            is_spell=bool(ctx.get("is_spell")))
        atk = int(eff.get("attack", eff.get("attack_bonus", 0)) or 0)
        hp = int(eff.get("health", eff.get("health_bonus", 0)) or 0)
        spell_id = eff.get("card_id") or ctx.get("source_card_id") or "sub"
        for t in targets:
            if not isinstance(t, Minion):
                continue
            t.attack += atk
            t.max_health += hp
            t.health += hp
            state.pending_modifiers.append({
                "kind": "return_spell_to_deck_on_minion_death",
                "owner": source_owner,
                "minion_id": t.instance_id,
                "card_id": spell_id,
            })
            state.log_event({
                "type": "buff_spell_attached",
                "player": source_owner,
                "minion": t.instance_id,
                "card_id": spell_id,
                "attack_delta": atk,
                "health_delta": hp,
            })

    @handler("REVIVE_FIRST_FRIENDLY_DEAD_AS_BEAST")
    def _revive_first_friendly_dead_as_beast(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        if len(p.board) >= MAX_BOARD_SIZE:
            return
        rec = next((r for r in state.graveyard if r.get("owner") == source_owner), None)
        if not rec:
            state.log_event({"type": "no_friendly_dead_minion", "player": source_owner})
            return
        card_id = rec.get("card_id")
        card = get_card(card_id) or {}
        if card.get("type") != "MINION":
            return
        tags = list(card.get("tags") or [])
        if "CHARGE" not in tags:
            tags.append("CHARGE")
        m = Minion(
            instance_id=gen_id("m_"),
            card_id=card_id,
            name=card.get("name", rec.get("name", card_id)),
            attack=9,
            health=9,
            max_health=9,
            tags=tags,
            tribes=["FERA"],
            effects=list(card.get("effects") or []),
            owner=source_owner,
            divine_shield="DIVINE_SHIELD" in tags,
            summoning_sick=True,
        )
        p.board.append(m)
        state.log_event({"type": "resummon", "owner": source_owner, "card_id": card_id, "minion": m.to_dict()})

    @handler("RECRUIT_MINIONS_SET_2_2_WITH_TAGS")
    def _recruit_minions_set_2_2_with_tags(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 5) or 5)
        p = state.players[source_owner]
        recruited = 0
        idx = 0
        tags_to_add = list(eff.get("tags") or ["DIVINE_SHIELD", "RUSH"])
        while recruited < amount and idx < len(p.deck) and len(p.board) < MAX_BOARD_SIZE:
            cid = p.deck[idx]
            card = get_card(cid) or {}
            if card.get("type") != "MINION":
                idx += 1
                continue
            p.deck.pop(idx)
            tags = list(card.get("tags") or [])
            for tag in tags_to_add:
                if tag not in tags:
                    tags.append(tag)
            m = Minion(
                instance_id=gen_id("m_"),
                card_id=cid,
                name=card.get("name", cid),
                attack=2,
                health=2,
                max_health=2,
                tags=tags,
                tribes=list(card.get("tribes") or []),
                effects=list(card.get("effects") or []),
                owner=source_owner,
                divine_shield="DIVINE_SHIELD" in tags,
                summoning_sick=True,
            )
            p.board.append(m)
            recruited += 1
            state.log_event({"type": "recruit", "player": source_owner, "minion": m.to_dict(), "set_stats": [2, 2]})

    @handler("DESTROY_ALL_DISCARD_HANDS_DRAW")
    def _destroy_all_discard_hands_draw(state, eff, source_owner, source_minion, ctx):
        draw_amount = int(eff.get("draw", 5) or 5)
        for m in list(state.all_minions()):
            m.health = 0
        for p in state.players:
            discarded = list(p.hand)
            p.hand.clear()
            for card in discarded:
                state.log_event({
                    "type": "discard",
                    "player": p.player_id,
                    "instance_id": card.instance_id,
                    "card_id": card.card_id,
                })
        # Import tardio para evitar ciclo durante registro de handlers.
        from .effects import draw_card
        for p in state.players:
            draw_card(state, p, draw_amount)

    @handler("SUMMON_FIRST_FRIENDLY_MINION_FROM_GRAVEYARD_AS_BEAST_9_9_CHARGE")
    def _alias_dinomancia(state, eff, source_owner, source_minion, ctx):
        _revive_first_friendly_dead_as_beast(state, eff, source_owner, source_minion, ctx)
