"""Lote 10 — efeitos de mão/deck/revelação."""
from __future__ import annotations

from game import engine, effects
from game.state import CardInHand, Minion, gen_id


def _new_blank_match(seed: int = 1, manual_choices: bool = False):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30,
                            seed=seed, manual_choices=manual_choices)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=2, health=3, ready=True):
    m = Minion(
        instance_id=gen_id("m_"), card_id=card_id, name="Test",
        attack=attack, health=health, max_health=health,
        tags=[], tribes=[], effects=[], owner=pid, summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


def test_reveal_card_from_hand_reduz_custo_do_feitico_escolhido():
    state = _new_blank_match(manual_choices=True)
    pid = state.current_player
    p = state.players[pid]
    p.hand = [
        CardInHand(instance_id=gen_id("h_"), card_id="troca_justa"),
        CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi"),
        CardInHand(instance_id=gen_id("h_"), card_id="stonks"),
    ]
    spell = p.hand[2]
    eff = {"action": "REVEAL_CARD_FROM_HAND", "target": {"mode": "SELF_HAND", "valid": ["SPELL"]}}
    effects.resolve_effect(state, eff, pid, None, {})
    assert state.pending_choice is not None
    assert state.pending_choice["kind"] == "reveal_card_from_hand"

    ok, msg = engine.resolve_choice(state, pid, state.pending_choice["choice_id"], {"card_id": spell.instance_id})
    assert ok, msg
    assert spell.cost_modifier == -1
    assert any(ev.get("type") == "reveal_hand_card" and ev.get("card_id") == "stonks" for ev in state.event_log)


def test_reveal_leftmost_and_rightmost_hand_cards_loga_as_duas_pontas():
    state = _new_blank_match()
    pid = state.current_player
    opp = state.opponent_of(pid)
    opp.hand = [
        CardInHand(instance_id=gen_id("h_"), card_id="camarao"),
        CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi"),
        CardInHand(instance_id=gen_id("h_"), card_id="pizza"),
    ]
    effects.resolve_effect(state, {"action": "REVEAL_LEFTMOST_AND_RIGHTMOST_HAND_CARDS", "target": {"mode": "OPPONENT_HAND"}}, pid, None, {})
    ev = next(ev for ev in reversed(state.event_log) if ev.get("type") == "reveal_hand_edges")
    assert [c["card_id"] for c in ev["cards"]] == ["camarao", "pizza"]


def test_swap_random_hand_card_with_opponent_troca_uma_de_cada_mao():
    state = _new_blank_match(seed=3)
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    me.hand = [CardInHand(instance_id=gen_id("h_"), card_id="camarao")]
    opp.hand = [CardInHand(instance_id=gen_id("h_"), card_id="pizza")]
    effects.resolve_effect(state, {"action": "SWAP_RANDOM_HAND_CARD_WITH_OPPONENT"}, pid, None, {})
    assert [c.card_id for c in me.hand] == ["pizza"]
    assert [c.card_id for c in opp.hand] == ["camarao"]


def test_move_hand_card_to_opponent_deck_top_com_custo_aumentado():
    state = _new_blank_match(manual_choices=True)
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    me.hand = [CardInHand(instance_id=gen_id("h_"), card_id="camarao")]
    chosen = me.hand[0]
    opp.deck = ["vini_zumbi"]
    eff = {
        "action": "MOVE_HAND_CARD_TO_OPPONENT_DECK_TOP",
        "cost_modifier": 5,
        "max_cost": 10,
        "target": {"mode": "CHOSEN_FRIENDLY_HAND_CARD"},
    }
    effects.resolve_effect(state, eff, pid, None, {})
    ok, msg = engine.resolve_choice(state, pid, state.pending_choice["choice_id"], {"card_id": chosen.instance_id})
    assert ok, msg
    assert me.hand == []
    marker = opp.deck[0]
    assert marker != "camarao"
    assert state.deck_card_modifiers[marker]["card_id"] == "camarao"
    assert state.deck_card_modifiers[marker]["cost_override"] == 6

    effects.draw_card(state, opp, 1)
    drawn = opp.hand[-1]
    assert drawn.card_id == "camarao"
    assert drawn.effective_cost() == 6


def test_move_hand_cards_to_deck_and_heal_cura_por_carta():
    state = _new_blank_match(manual_choices=True)
    pid = state.current_player
    p = state.players[pid]
    p.hero_health = 20
    a = CardInHand(instance_id=gen_id("h_"), card_id="camarao")
    b = CardInHand(instance_id=gen_id("h_"), card_id="pizza")
    c = CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi")
    p.hand = [a, b, c]
    p.deck = ["peixe"]
    eff = {
        "action": "MOVE_HAND_CARDS_TO_DECK_AND_HEAL",
        "heal_per_card": 3,
        "target": {"mode": "CHOSEN_FRIENDLY_HAND_CARDS"},
        "destination": {"mode": "SELF_DECK", "position": "CHOSEN"},
        "heal_target": {"mode": "SELF_HERO"},
    }
    effects.resolve_effect(state, eff, pid, None, {})
    ok, msg = engine.resolve_choice(state, pid, state.pending_choice["choice_id"],
                                    {"card_ids": [a.instance_id, b.instance_id], "position": "BOTTOM"})
    assert ok, msg
    assert [x.card_id for x in p.hand] == ["vini_zumbi"]
    assert p.hero_health == 26
    assert len(p.deck) == 3
    assert p.deck[0] == "peixe"


def test_move_enemy_minion_to_hand_and_set_cost():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    target = _force_minion(state, foe, card_id="pizza", attack=5, health=5)
    hand_before = len(state.players[pid].hand)
    eff = {
        "action": "MOVE_ENEMY_MINION_TO_HAND_AND_SET_COST",
        "set_cost": 1,
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": target.instance_id})
    assert state.find_minion(target.instance_id) is None
    assert len(state.players[pid].hand) == hand_before + 1
    moved = state.players[pid].hand[-1]
    assert moved.card_id == "pizza"
    assert moved.effective_cost() == 1
