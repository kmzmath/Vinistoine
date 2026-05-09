"""Lote 15 - estados/passivos complexos."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import Minion, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"]*30, "B", ["vini_zumbi"]*30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=2, health=3,
                  tags=None, ready=True):
    card = get_card(card_id) or {}
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=card.get("name", card_id),
        attack=attack,
        health=health,
        max_health=health,
        tags=list(tags if tags is not None else (card.get("tags") or [])),
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=pid,
        summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


def test_freeze_until_self_dies_keeps_target_frozen_and_releases_on_death():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    source = _force_minion(state, pid, card_id="vini_geladinho", attack=2, health=2)
    target = _force_minion(state, foe, attack=3, health=3)

    eff = {"action": "FREEZE_UNTIL_SELF_DIES",
           "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}}
    effects.resolve_effect(state, eff, pid, source, {"chosen_target": target.instance_id})
    assert target.frozen is True

    # Mesmo passando o turno do dono do alvo, não descongela enquanto a fonte vive.
    state.current_player = foe
    engine.end_turn(state, foe)
    assert target.frozen is True

    source.health = 0
    engine.cleanup(state)
    assert target.frozen is False


def test_lock_all_other_minions_from_attacking_excludes_chosen_and_expires():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    chosen = _force_minion(state, foe, attack=2, health=2, ready=True)
    locked_enemy = _force_minion(state, foe, attack=2, health=2, ready=True)
    locked_friendly = _force_minion(state, pid, attack=2, health=2, ready=True)

    eff = {
        "action": "LOCK_ALL_OTHER_MINIONS_FROM_ATTACKING",
        "duration_turns": 2,
        "excluded_target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
        "target": {"mode": "ALL_MINIONS_EXCEPT_CHOSEN"},
    }
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": chosen.instance_id})
    assert chosen.can_attack() is True
    assert locked_enemy.can_attack() is False
    assert locked_friendly.can_attack() is False

    state.current_player = foe
    engine.end_turn(state, foe)
    assert locked_enemy.can_attack() is False
    state.current_player = foe
    engine.end_turn(state, foe)
    assert locked_enemy.can_attack() is True


def test_immune_to_triggered_effects_blocks_on_play_targeting_and_aoe():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    surdo = _force_minion(state, foe, card_id="surdo", attack=2, health=5)
    other = _force_minion(state, foe, attack=2, health=5)

    engine.apply_continuous_effects(state)
    assert "TRIGGER_IMMUNE_ON_PLAY" in surdo.tags

    # AOE de battlecry/ON_PLAY deve ignorar Surdo, mas atingir outros.
    eff = {"action": "DAMAGE", "amount": 2, "target": {"mode": "ALL_ENEMY_MINIONS"}}
    effects.resolve_effect(state, eff, pid, None, {"source_trigger": "ON_PLAY"})
    assert surdo.health == 5
    assert other.health == 3

    # Feitiço normal ainda atinge.
    effects.resolve_effect(state, eff, pid, None, {"source_trigger": "ON_SPELL"})
    assert surdo.health == 3


def test_cant_attack_while_only_friendly_minion_aura():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, card_id="vini_aleijado", attack=3, health=3, ready=True)

    engine.apply_continuous_effects(state)
    assert m.can_attack() is False

    ally = _force_minion(state, pid, attack=1, health=1, ready=True)
    engine.apply_continuous_effects(state)
    assert m.can_attack() is True


def test_reduce_attack_instead_of_health():
    state = _new_blank_match()
    pid = state.current_player
    edu = _force_minion(state, pid, card_id="edu_cachorrao", attack=4, health=6)

    effects.damage_character(state, edu, 3, source_owner=1-pid)
    assert edu.attack == 1
    assert edu.health == 6

    effects.damage_character(state, edu, 2, source_owner=1-pid)
    assert edu.attack == 0
    engine.cleanup(state)
    assert state.find_minion(edu.instance_id) is None


def test_apply_permanent_attack_half_status_once():
    state = _new_blank_match()
    pid = state.current_player
    source = _force_minion(state, pid, card_id="mao_tse_tung", attack=1, health=4)
    a = _force_minion(state, pid, attack=5, health=3)
    b = _force_minion(state, 1-pid, attack=3, health=3)

    eff = {"action": "APPLY_PERMANENT_ATTACK_HALF_STATUS",
           "rounding": "CEIL",
           "target": {"mode": "ALL_OTHER_MINIONS"}}
    effects.resolve_effect(state, eff, pid, source, {})

    assert source.attack == 1
    assert a.attack == 3  # ceil(5/2)
    assert b.attack == 2  # ceil(3/2)

    effects.resolve_effect(state, eff, pid, source, {})
    assert a.attack == 3
    assert b.attack == 2
