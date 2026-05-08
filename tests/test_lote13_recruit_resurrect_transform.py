"""Lote 13 — recrutamento, ressurreição, transformação e cura especial."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import Minion, gen_id


def _new_blank_match(seed: int = 1, deck_card: str = "vini_zumbi"):
    state = engine.new_game("A", [deck_card] * 30, "B", [deck_card] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=2, health=3,
                  max_health=None, tags=None, ready=True):
    card = get_card(card_id) or {}
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=card.get("name", card_id),
        attack=attack,
        health=health,
        max_health=max_health if max_health is not None else health,
        tags=list(tags if tags is not None else (card.get("tags") or [])),
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=pid,
        summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


def test_recruit_first_minion_with_cost_and_tribe_bonus():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["vini_zumbi", "mamaquinho", "pizza"]  # primeiro custo 2 é FERA

    eff = {
        "action": "RECRUIT_FIRST_MINION_WITH_COST",
        "cost": 2,
        "target": {"mode": "SELF_DECK"},
        "if_recruited_has_tribe": {"tribe": "FERA", "action": "ADD_TAG", "tag": "RUSH"},
    }
    effects.resolve_effect(state, eff, pid, None, {})

    assert p.board[-1].card_id == "mamaquinho"
    assert "RUSH" in p.board[-1].tags
    assert "mamaquinho" not in p.deck


def test_recruit_highest_cost_minion_up_to():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["mamaquinho", "pizza", "vini_zumbi"]  # todos <= 7, pizza/mamaquinho custo 2

    eff = {"action": "RECRUIT_HIGHEST_COST_MINION_UP_TO", "max_cost": 7,
           "target": {"mode": "SELF_DECK"}}
    effects.resolve_effect(state, eff, pid, None, {})

    # Empate por custo fica com o primeiro do deck.
    assert p.board[-1].card_id == "mamaquinho"
    assert "mamaquinho" not in p.deck


def test_discover_and_resurrect_recent_friendly_minion_fallback():
    state = _new_blank_match()
    pid = state.current_player
    state.graveyard.append({"card_id": "vini_zumbi", "owner": pid, "name": "Vini Zumbi"})
    state.graveyard.append({"card_id": "pizza", "owner": pid, "name": "Pizza"})

    eff = {"action": "DISCOVER_AND_RESURRECT_RECENT_FRIENDLY_MINION", "pool_size": 3}
    effects.resolve_effect(state, eff, pid, None, {})

    assert state.players[pid].board[-1].card_id == "pizza"


def test_discover_and_resurrect_recent_friendly_minion_manual_choice():
    state = _new_blank_match()
    state.manual_choices = True
    pid = state.current_player
    state.graveyard.append({"card_id": "vini_zumbi", "owner": pid, "name": "Vini Zumbi"})
    state.graveyard.append({"card_id": "pizza", "owner": pid, "name": "Pizza"})

    eff = {"action": "DISCOVER_AND_RESURRECT_RECENT_FRIENDLY_MINION", "pool_size": 3}
    effects.resolve_effect(state, eff, pid, None, {})

    assert state.pending_choice is not None
    assert state.pending_choice["kind"] == "resurrect_from_graveyard"
    choice_id = state.pending_choice["choice_id"]
    ok, msg = engine.resolve_choice(state, pid, choice_id, {"graveyard_index": 0})
    assert ok, msg
    assert state.players[pid].board[-1].card_id == "vini_zumbi"


def test_draw_minion_and_transform_into_popo():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["troca_justa", "pizza", "mamaquinho"]
    before = len(p.hand)

    eff = {"action": "DRAW_MINION_AND_TRANSFORM", "amount": 1,
           "transform_into": "popo", "target": {"mode": "SELF_DECK"}}
    effects.resolve_effect(state, eff, pid, None, {})

    assert len(p.hand) == before + 1
    assert p.hand[-1].card_id == "popo"
    assert "pizza" not in p.deck


def test_draw_tribe_from_deck_or_resurrect_self_draws_tribe():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["vini_zumbi", "laranja", "pizza"]  # laranja é FRUTA
    before = len(p.hand)

    eff = {"action": "DRAW_TRIBE_FROM_DECK_OR_RESURRECT_SELF", "tribe": "FRUTA",
           "amount": 1, "target": {"mode": "SELF_DECK"}}
    effects.resolve_effect(state, eff, pid, None, {"source_card_id": "morango"})

    assert len(p.hand) == before + 1
    assert p.hand[-1].card_id == "laranja"
    assert "laranja" not in p.deck


def test_draw_tribe_from_deck_or_resurrect_self_fallback():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["vini_zumbi"] * 5  # nenhuma FRUTA

    eff = {"action": "DRAW_TRIBE_FROM_DECK_OR_RESURRECT_SELF", "tribe": "FRUTA",
           "amount": 1, "fallback": {"action": "RESURRECT", "health": 1},
           "target": {"mode": "SELF_DECK"}}
    effects.resolve_effect(state, eff, pid, None, {"source_card_id": "morango"})

    assert p.board[-1].card_id == "morango"
    assert p.board[-1].health == 1


def test_draw_highest_cost_spell_and_set_cost():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["troca_justa", "ataque_de_niurau", "stonks"]
    before = len(p.hand)

    eff = {"action": "DRAW_HIGHEST_COST_SPELL_AND_SET_COST", "set_cost": 1,
           "target": {"mode": "SELF_DECK"}}
    effects.resolve_effect(state, eff, pid, None, {})

    assert len(p.hand) == before + 1
    assert p.hand[-1].card_id == "ataque_de_niurau"
    assert p.hand[-1].cost_override == 1


def test_heal_with_overheal_to_health():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, health=4, max_health=5)  # perdeu 1
    eff = {"action": "HEAL_WITH_OVERHEAL_TO_HEALTH", "amount": 3,
           "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]}}

    effects.resolve_effect(state, eff, pid, None, {"chosen_target": m.instance_id})

    assert m.health == 7
    assert m.max_health == 7


def test_heal_or_revive_friendly_heals_most_damaged():
    state = _new_blank_match()
    pid = state.current_player
    a = _force_minion(state, pid, health=1, max_health=5)
    b = _force_minion(state, pid, health=4, max_health=5)

    eff = {"action": "HEAL_OR_REVIVE_FRIENDLY", "amount": 3,
           "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_CHARACTER"]}}
    effects.resolve_effect(state, eff, pid, None, {})

    assert a.health == 4
    assert b.health == 4


def test_heal_or_revive_friendly_revives_if_no_damaged_targets():
    state = _new_blank_match()
    pid = state.current_player
    state.graveyard.append({"card_id": "pizza", "owner": pid, "name": "Pizza"})

    eff = {"action": "HEAL_OR_REVIVE_FRIENDLY", "amount": 3,
           "can_revive_dead_this_turn": True,
           "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_CHARACTER"]}}
    effects.resolve_effect(state, eff, pid, None, {})

    assert state.players[pid].board[-1].card_id == "pizza"
    assert state.players[pid].board[-1].health == 3


def test_heal_opponent_and_draw_scaling():
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    opp.hero_health = 20
    before = len(me.hand)

    eff = {
        "action": "HEAL_OPPONENT_AND_DRAW_SCALING",
        "options": [
            {"heal_amount": 4, "draw_amount": 1},
            {"heal_amount": 8, "draw_amount": 2},
            {"heal_amount": 12, "draw_amount": 3},
        ],
        "target": {"mode": "OPPONENT_PLAYER"},
    }
    effects.resolve_effect(state, eff, pid, None, {"chose_index": 1})

    assert opp.hero_health == 28
    assert len(me.hand) == before + 2
