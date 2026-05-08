"""Lote 17 — handlers especiais finais."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import Minion, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=None, health=None,
                  tags=None, ready=True):
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
        tags=list(tags if tags is not None else (card.get("tags") or [])),
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=pid,
        summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in (tags or card.get("tags") or []),
    )
    state.players[pid].board.append(m)
    return m


def test_cast_card_on_minions_applies_spell_to_adjacent_targets():
    state = _new_blank_match()
    pid = state.current_player
    left = _force_minion(state, pid, attack=1, health=2)
    source = _force_minion(state, pid, card_id="gusnabo_sagrado", attack=7, health=7)
    right = _force_minion(state, pid, attack=1, health=2)

    eff = {"action": "CAST_CARD_ON_MINIONS", "card_id": "pera_sagrada",
           "target": {"mode": "ADJACENT_FRIENDLY_MINIONS"}}
    effects.resolve_effect(state, eff, pid, source, {})

    assert left.health == 5 and left.max_health == 5 and left.divine_shield
    assert right.health == 5 and right.max_health == 5 and right.divine_shield
    assert source.health == 7


def test_choose_n_keywords_fallback_and_manual_choice():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, card_id="aleixo", attack=3, health=3)

    eff = {"action": "CHOOSE_N_KEYWORDS", "choose": 2,
           "choices": ["RUSH", "DIVINE_SHIELD", "WINDFURY", "TAUNT"],
           "target": {"mode": "SELF"}}
    effects.resolve_effect(state, eff, pid, m, {})
    assert "RUSH" in m.tags
    assert "DIVINE_SHIELD" in m.tags
    assert m.divine_shield

    state2 = _new_blank_match()
    state2.manual_choices = True
    pid2 = state2.current_player
    m2 = _force_minion(state2, pid2, card_id="aleixo", attack=3, health=3)
    effects.resolve_effect(state2, eff, pid2, m2, {})
    assert state2.pending_choice["kind"] == "choose_n_keywords"
    ok, msg = engine.resolve_choice(state2, pid2, state2.pending_choice["choice_id"],
                                    {"selected_keywords": ["WINDFURY", "TAUNT"]})
    assert ok, msg
    assert "WINDFURY" in m2.tags and "TAUNT" in m2.tags


def test_choose_x_damage_self_player_summon():
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    before = me.hero_health
    eff = {"action": "CHOOSE_X_DAMAGE_SELF_PLAYER_SUMMON", "amount": 4,
           "choices": [1, 2, 3], "summon_card_id": "tronco",
           "target": {"mode": "SELF_PLAYER"}}
    effects.resolve_effect(state, eff, pid, None, {"x": 2})

    assert me.hero_health == before - 8
    assert [m.card_id for m in me.board[-2:]] == ["tronco", "tronco"]


def test_copy_self_stats_to_minion():
    state = _new_blank_match()
    pid = state.current_player
    source = _force_minion(state, pid, card_id="igor_estiloso", attack=7, health=2)
    target = _force_minion(state, pid, attack=1, health=9)

    eff = {"action": "COPY_SELF_STATS_TO_MINION",
           "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]}}
    effects.resolve_effect(state, eff, pid, source, {"chosen_target": target.instance_id})

    assert target.attack == 7
    assert target.health == 2
    assert target.max_health == 2


def test_summoned_copy_death_buffs_original():
    state = _new_blank_match()
    pid = state.current_player
    original = _force_minion(state, pid, card_id="spiideba", attack=1, health=1)

    eff = {"action": "SUMMON_COPY", "amount": 1,
           "modifications": {"tags": ["TAUNT"]}, "target": {"mode": "SELF_BOARD"}}
    effects.resolve_effect(state, eff, pid, original, {})
    copy = state.players[pid].board[-1]
    assert copy is not original

    copy.attack = 3
    copy.max_health = 4
    copy.health = 0
    engine.cleanup(state)

    assert original.attack == 4
    assert original.max_health == 5
    assert original.health == 5


def test_vini_sertanejo_marked_ally_killed_by_opponent_buffs_source():
    state = _new_blank_match()
    pid = state.current_player
    source = _force_minion(state, pid, card_id="vini_sertanejo", attack=4, health=8)
    ally = _force_minion(state, pid, attack=2, health=3)
    eff = {"action": "MARK_FRIENDLY_MINION_FOR_SELF_BUFF_ON_OPPONENT_KILL",
           "buff_attack": 3, "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]}}
    effects.resolve_effect(state, eff, pid, source, {"chosen_target": ally.instance_id})

    effects.damage_character(state, ally, 5, source_owner=1-pid)
    engine.cleanup(state)
    assert source.attack == 7


def test_vic_returns_self_and_marked_killed_target_to_hand():
    state = _new_blank_match()
    pid = state.current_player
    vic = _force_minion(state, pid, card_id="vic", attack=4, health=2, ready=True)
    enemy = _force_minion(state, 1-pid, card_id="pizza", attack=0, health=3, ready=True)

    eff = {"action": "MARK_KILL_TARGET_RETURN_BOTH_TO_HAND",
           "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}}
    effects.resolve_effect(state, eff, pid, vic, {"chosen_target": enemy.instance_id})

    effects.damage_character(state, enemy, 4, source_owner=pid, source_minion=vic)
    engine.cleanup(state)

    assert state.find_minion(vic.instance_id) is None
    assert state.find_minion(enemy.instance_id) is None
    hand_ids = [c.card_id for c in state.players[pid].hand]
    assert "vic" in hand_ids and "pizza" in hand_ids
    assert not any(g["card_id"] == "pizza" for g in state.graveyard)


def test_redistribute_self_stats_manual_choice():
    state = _new_blank_match()
    state.manual_choices = True
    pid = state.current_player
    tower = _force_minion(state, pid, card_id="la_torre_de_pisa", attack=8, health=4)
    enemy = _force_minion(state, 1-pid, attack=1, health=9)

    eff = {"action": "REDISTRIBUTE_SELF_STATS", "optional": True,
           "condition": {"target_type": "MINION"}, "target": {"mode": "SELF"}}
    effects.resolve_effect(state, eff, pid, tower, {"attack_target_id": enemy.instance_id})
    assert state.pending_choice["kind"] == "redistribute_self_stats"
    ok, msg = engine.resolve_choice(state, pid, state.pending_choice["choice_id"], {"attack": 5})
    assert ok, msg
    assert tower.attack == 5
    assert tower.health == 7


def test_sleep_resummons_dormant_and_awakes_after_two_friendly_summons():
    state = _new_blank_match()
    pid = state.current_player
    sleepy = _force_minion(state, pid, card_id="vini_dorminhoco", attack=4, health=4)

    effects.resolve_effect(state, {"action": "SLEEP", "target": {"mode": "SELF"}},
                           pid, sleepy, {"source_card_id": "vini_dorminhoco"})
    new_sleepy = state.players[pid].board[-1]
    assert new_sleepy.card_id == "vini_dorminhoco"
    assert "DORMANT" in new_sleepy.tags
    assert new_sleepy.immune and new_sleepy.cant_attack

    effects.summon_minion_from_card(state, pid, "vini_zumbi")
    assert "DORMANT" in new_sleepy.tags
    effects.summon_minion_from_card(state, pid, "vini_zumbi")
    assert "DORMANT" not in new_sleepy.tags
    assert not new_sleepy.immune
    assert not new_sleepy.cant_attack
