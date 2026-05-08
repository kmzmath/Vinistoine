"""Lote 14 — ataques forçados e dano especial."""
from __future__ import annotations

from game import engine, effects
from game.state import Minion, gen_id
from game.cards import get_card


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="test", name="Test", attack=2, health=2,
                  tags=None, ready=True, effects_list=None):
    card = get_card(card_id) or {}
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=name if name != "Test" else card.get("name", name),
        attack=attack,
        health=health,
        max_health=health,
        tags=list(tags or []),
        tribes=list(card.get("tribes") or []),
        effects=list(effects_list if effects_list is not None else (card.get("effects") or [])),
        owner=pid,
        summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


def test_force_minion_attack():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    forced = _force_minion(state, foe, attack=4, health=6)
    target = _force_minion(state, pid, attack=2, health=5)

    eff = {
        "action": "FORCE_MINION_ATTACK",
        "source": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
        "target": {"mode": "CHOSEN", "valid": ["MINION"]},
    }
    effects.resolve_effect(state, eff, pid, None,
                           {"target_queue": [forced.instance_id, target.instance_id]})

    assert target.health == 1
    assert forced.health == 4
    assert forced.attacks_this_turn == 1


def test_damage_sequence_with_target_queue():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    a = _force_minion(state, foe, health=5)
    b = _force_minion(state, foe, health=5)
    c = _force_minion(state, foe, health=5)

    eff = {
        "action": "DAMAGE_SEQUENCE",
        "amounts": [1, 2, 3],
        "target": {"mode": "CHOSEN_EACH", "valid": ["ENEMY_HERO", "ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, None,
                           {"target_queue": [a.instance_id, b.instance_id, c.instance_id]})

    assert a.health == 4
    assert b.health == 3
    assert c.health == 2


def test_repeat_damage_if_kills_repeats_until_no_death():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    source = _force_minion(state, pid, attack=5, health=5)
    weak = _force_minion(state, foe, health=1)
    sturdy = _force_minion(state, foe, health=3)

    eff = {"action": "REPEAT_DAMAGE_IF_KILLS", "amount": 1,
           "target": {"mode": "ALL_OTHER_MINIONS"}}
    effects.resolve_effect(state, eff, pid, source, {})

    assert state.find_minion(weak.instance_id) is None
    found = state.find_minion(sturdy.instance_id)
    assert found is not None
    assert found[0].health == 1  # tomou 2 passes: um matou weak, outro não matou ninguém


def test_damage_adjacent_equal_to_target_health():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    left = _force_minion(state, foe, health=6)
    mid = _force_minion(state, foe, health=4)
    right = _force_minion(state, foe, health=6)

    eff = {
        "action": "DAMAGE_ADJACENT_EQUAL_TO_TARGET_HEALTH",
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
        "secondary_target": {"mode": "ADJACENT_MINIONS"},
    }
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": mid.instance_id})

    assert left.health == 2
    assert mid.health == 4
    assert right.health == 2


def test_damage_adjacent_minions_instead_during_attack():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    effect = {"trigger": "ON_ATTACK_MINION", "action": "DAMAGE_ADJACENT_MINIONS_INSTEAD",
              "amount_source": "SELF_ATTACK", "target": {"mode": "ADJACENT_TO_ATTACK_TARGET"}}
    attacker = _force_minion(state, pid, attack=3, health=10, ready=True,
                             effects_list=[effect])
    left = _force_minion(state, foe, health=8)
    mid = _force_minion(state, foe, attack=1, health=8)
    right = _force_minion(state, foe, health=8)

    ok, msg = engine.attack(state, pid, attacker.instance_id, mid.instance_id)

    assert ok, msg
    assert left.health == 5
    assert mid.health == 8  # alvo principal não recebe dano do atacante
    assert right.health == 5
    assert attacker.health == 9  # ainda recebe o contra-ataque do alvo


def test_excess_damage_to_chosen_side():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    left = _force_minion(state, foe, health=4)
    mid = _force_minion(state, foe, health=3)
    right = _force_minion(state, foe, health=5)

    eff = {
        "action": "EXCESS_DAMAGE_TO_CHOSEN_SIDE",
        "source_damage": 7,
        "target": {"mode": "CHOSEN_ADJACENT_DIRECTION", "valid": ["MINION"]},
    }
    effects.resolve_effect(state, eff, pid, None,
                           {"chosen_target": mid.instance_id, "direction": "RIGHT"})

    assert mid.health == -4
    assert right.health == 1
    assert left.health == 4
