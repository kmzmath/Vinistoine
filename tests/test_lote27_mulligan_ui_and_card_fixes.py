"""Lote 27 - mulligan/moeda, targeting de UI e animações/eventos."""
from __future__ import annotations

from pathlib import Path

from game import engine, effects
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id, MAX_HAND_SIZE


def _new_match(seed: int = 1):
    deck_a = ["vini_zumbi"] * 30
    deck_b = ["pizza"] * 30
    return engine.new_game("A", deck_a, "B", deck_b, seed=seed, manual_choices=True)


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=None, health=None):
    card = get_card(card_id) or {}
    atk = card.get("attack") if attack is None else attack
    hp = card.get("health") if health is None else health
    if atk is None:
        atk = 0
    if hp is None:
        hp = 1
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=card.get("name", card_id),
        attack=atk,
        health=hp,
        max_health=hp,
        tags=list(card.get("tags") or []),
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=pid,
        divine_shield="DIVINE_SHIELD" in (card.get("tags") or []),
    )
    state.players[pid].board.append(m)
    return m


def _add_hand(state, pid, card_id):
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def test_mulligan_first_has_three_second_has_four_and_coin_after_both_confirm():
    state = _new_match(seed=2)
    first = state.current_player
    second = 1 - first

    assert len(state.players[first].hand) == 3
    assert len(state.players[second].hand) == 4
    assert all(c.card_id != "coin" for p in state.players for c in p.hand)

    engine.confirm_mulligan(state, first, [])
    assert all(c.card_id != "coin" for p in state.players for c in p.hand)
    engine.confirm_mulligan(state, second, [])

    assert state.phase == "PLAYING"
    assert any(c.card_id == "coin" for c in state.players[second].hand)
    assert get_card("coin") is not None


def test_burgues_adds_real_coin_card_to_hand():
    state = _new_match()
    pid = state.current_player
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    ch = _add_hand(state, pid, "burgues")

    ok, msg = engine.play_card(state, pid, ch.instance_id)

    assert ok, msg
    assert any(c.card_id == "coin" for c in p.hand)
    assert get_card("coin")["effects"][0]["action"] == "GAIN_TEMP_MANA"


def test_remove_divine_shield_tag_updates_divine_shield_flag():
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid
    target = _force_minion(state, foe, card_id="pizza")
    target.tags.append("DIVINE_SHIELD")
    target.divine_shield = True

    effects.resolve_effect(
        state,
        {"action": "REMOVE_TAG", "tag": "DIVINE_SHIELD", "target": {"mode": "ENEMY_MINIONS"}},
        pid,
        None,
        {},
    )

    assert "DIVINE_SHIELD" not in target.tags
    assert target.divine_shield is False


def test_vini_em_disparada_removes_taunt_and_divine_shield_from_enemies():
    state = _new_match()
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    enemy = _force_minion(state, foe, card_id="pizza")
    enemy.tags.extend(["TAUNT", "DIVINE_SHIELD"])
    enemy.divine_shield = True
    ch = _add_hand(state, pid, "vini_em_disparada")

    ok, msg = engine.play_card(state, pid, ch.instance_id)

    assert ok, msg
    assert "TAUNT" not in enemy.tags
    assert "DIVINE_SHIELD" not in enemy.tags
    assert enemy.divine_shield is False


def test_burn_event_reveals_card_id_when_hand_is_full():
    state = _new_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand = [CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi") for _ in range(MAX_HAND_SIZE)]
    p.deck = ["pizza"]

    effects.draw_card(state, p, 1)

    burn = state.event_log[-1]
    assert burn["type"] == "burn"
    assert burn["card_id"] == "pizza"


def test_empowered_spell_costs_one_extra_mana():
    state = _new_match()
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.hand.clear()
    target = _force_minion(state, foe, card_id="pizza", health=5)
    ch = _add_hand(state, pid, "absorver")
    p.mana = get_card("absorver")["cost"]

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=target.instance_id, empowered=True)
    assert not ok
    assert "Mana insuficiente" in msg

    p.mana = get_card("absorver")["cost"] + 1
    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=target.instance_id, empowered=True)
    assert ok, msg


def test_frontend_targeting_supports_minion_and_any_character():
    src = (Path(__file__).parents[1] / "static" / "game.html").read_text(encoding="utf-8")
    assert 'v === "ANY_MINION" || v === "MINION"' in src
    assert 'v === "ANY_CHARACTER"' in src
    assert 'Fortalecer (+1)' in src
    assert 'OPPONENT_PLAY_ANNOUNCE_MS = 4000' in src
    assert 'draw-fly-v2' in src
    assert 'Carta queimada' in src
