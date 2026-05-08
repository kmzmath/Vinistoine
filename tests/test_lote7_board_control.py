"""Lote 7 — controle de mesa, sacrifício, ressurreição e substituição."""
from __future__ import annotations

import game.cards as _cards_mod
from game import engine, effects
from game.state import Minion, CardInHand, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", name="Test", attack=2, health=2,
                  max_health=None, tags=None, effects_list=None, ready=True):
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=name,
        attack=attack,
        health=health,
        max_health=max_health if max_health is not None else health,
        tags=list(tags or []),
        effects=list(effects_list or []),
        owner=pid,
        summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


def test_return_all_minions_to_hand():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    a = _force_minion(state, pid, card_id="camarao")
    b = _force_minion(state, foe, card_id="vini_zumbi")
    hand_a = len(state.players[pid].hand)
    hand_b = len(state.players[foe].hand)

    effects.resolve_effect(state, {
        "action": "RETURN_ALL_MINIONS_TO_HAND",
        "target": {"mode": "ALL_MINIONS"},
    }, pid, None, {})

    assert a not in state.players[pid].board
    assert b not in state.players[foe].board
    assert len(state.players[pid].hand) == hand_a + 1
    assert len(state.players[foe].hand) == hand_b + 1
    assert state.players[pid].hand[-1].card_id == "camarao"
    assert state.players[foe].hand[-1].card_id == "vini_zumbi"


def test_sacrifice_friendly_minion_destroy_enemy_minion_via_engine_targets():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = p.max_mana = 10
    ally = _force_minion(state, pid, card_id="camarao", health=3)
    enemy = _force_minion(state, foe, card_id="vini_zumbi", health=3)
    card_in_hand = CardInHand(instance_id=gen_id("h_"), card_id="gusnabo_o_mago")
    p.hand.append(card_in_hand)

    ok, msg = engine.play_card(state, pid, card_in_hand.instance_id,
                               chosen_targets=[ally.instance_id, enemy.instance_id])
    assert ok, msg
    assert state.find_minion(ally.instance_id) is None
    assert state.find_minion(enemy.instance_id) is None
    assert any(m.card_id == "gusnabo_o_mago" for m in p.board)


def test_devour_friendly_minion_gain_attributes_and_text():
    state = _new_blank_match()
    pid = state.current_player
    eater = _force_minion(state, pid, card_id="spiid_faminto", attack=3, health=4)
    victim_effects = [{"trigger": "ON_DEATH", "action": "DRAW_CARD", "amount": 1,
                       "target": {"mode": "SELF_PLAYER"}}]
    victim = _force_minion(state, pid, card_id="camarao", attack=2, health=3,
                           tags=["RUSH"], effects_list=victim_effects)

    effects.resolve_effect(state, {
        "action": "DEVOUR_FRIENDLY_MINION_GAIN_ATTRIBUTES",
        "bonus_attack": 1,
        "bonus_health": 1,
        "copy_text": True,
        "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]},
    }, pid, eater, {"chosen_target": victim.instance_id})
    engine.cleanup(state)

    assert state.find_minion(victim.instance_id) is None
    assert eater.attack == 3 + 2 + 1
    assert eater.health == 4 + 3 + 1
    assert "RUSH" in eater.tags
    assert any(e.get("action") == "DRAW_CARD" for e in eater.effects)


def test_destroy_and_resummon_doubles_attack_and_full_health():
    state = _new_blank_match()
    pid = state.current_player
    target = _force_minion(state, pid, card_id="vini_zumbi", attack=2, health=1, max_health=3)

    effects.resolve_effect(state, {
        "action": "DESTROY_AND_RESUMMON",
        "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]},
        "resummon": {"health": "FULL", "attack_multiplier": 2},
    }, pid, None, {"chosen_target": target.instance_id})

    assert state.find_minion(target.instance_id) is None
    reborn = state.players[pid].board[-1]
    assert reborn.card_id == "vini_zumbi"
    assert reborn.attack == 4
    assert reborn.health == 3
    assert reborn.max_health == 3


def test_destroy_and_resummon_full_health_keeps_attack():
    state = _new_blank_match()
    pid = state.current_player
    target = _force_minion(state, pid, card_id="vini_zumbi", attack=5, health=1, max_health=3)

    effects.resolve_effect(state, {
        "action": "DESTROY_AND_RESUMMON_FULL_HEALTH",
        "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]},
    }, pid, None, {"chosen_target": target.instance_id})

    reborn = state.players[pid].board[-1]
    assert reborn.card_id == "vini_zumbi"
    assert reborn.attack == 5
    assert reborn.health == 3


def test_replace_friendly_minions_from_deck_picks_best_legal_minion():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    _cards_mod._CARDS_BY_ID["old_one"] = {
        "id": "old_one", "name": "Old", "type": "MINION", "cost": 1,
        "attack": 1, "health": 1, "tags": [], "tribes": [], "effects": [],
    }
    _cards_mod._CARDS_BY_ID["legal_big"] = {
        "id": "legal_big", "name": "Legal Big", "type": "MINION", "cost": 4,
        "attack": 4, "health": 4, "tags": [], "tribes": [], "effects": [],
    }
    _cards_mod._CARDS_BY_ID["too_big"] = {
        "id": "too_big", "name": "Too Big", "type": "MINION", "cost": 5,
        "attack": 9, "health": 9, "tags": [], "tribes": [], "effects": [],
    }
    old = _force_minion(state, pid, card_id="old_one", attack=1, health=1)
    p.deck = ["too_big", "legal_big", "vini_zumbi"]

    effects.resolve_effect(state, {
        "action": "REPLACE_FRIENDLY_MINIONS_FROM_DECK",
        "cost_increase_limit": 3,
        "target": {"mode": "FRIENDLY_MINIONS"},
    }, pid, None, {})

    assert state.find_minion(old.instance_id) is None
    assert p.board[0].card_id == "legal_big"
    assert "legal_big" not in p.deck
    assert "too_big" in p.deck
