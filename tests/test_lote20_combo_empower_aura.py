"""Lote 20 - Combo, Fortalecer e auras recalculadas."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=None, health=None, ready=True):
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
        summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in (card.get("tags") or []),
    )
    state.players[pid].board.append(m)
    return m


def _add_hand(state, pid, card_id):
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def test_combo_felps_requires_prior_card_and_damages_target():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    enemy = _force_minion(state, foe, card_id="pizza", health=10)

    first = _add_hand(state, pid, "vini_zumbi")
    ok, msg = engine.play_card(state, pid, first.instance_id)
    assert ok, msg
    assert p.cards_played_this_turn == 1

    felps = _add_hand(state, pid, "felps")
    ok, msg = engine.play_card(state, pid, felps.instance_id, chosen_target=enemy.instance_id)
    assert ok, msg
    assert enemy.health == 2


def test_combo_felps_without_prior_card_has_no_combo_target_requirement():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    felps = _add_hand(state, pid, "felps")

    ok, msg = engine.play_card(state, pid, felps.instance_id)

    assert ok, msg
    assert p.board[-1].card_id == "felps"


def test_absorver_empowered_replaces_normal_self_damage():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    enemy = _force_minion(state, foe, card_id="pizza", health=5)
    ch = _add_hand(state, pid, "absorver")
    hero_before = p.hero_health

    ok, msg = engine.play_card(state, pid, ch.instance_id,
                               chosen_target=enemy.instance_id,
                               empowered=True)
    assert ok, msg
    assert enemy.health <= 0
    assert p.hero_health == hero_before


def test_absorver_normal_applies_self_damage_from_target_health():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    enemy = _force_minion(state, foe, card_id="pizza", health=5)
    ch = _add_hand(state, pid, "absorver")
    hero_before = p.hero_health

    ok, msg = engine.play_card(state, pid, ch.instance_id,
                               chosen_target=enemy.instance_id,
                               empowered=False)
    assert ok, msg
    assert enemy.health <= 0
    assert p.hero_health < hero_before


def test_awp_empowered_uses_empower_trigger_instead_of_double_damage():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    left = _force_minion(state, foe, card_id="vini_zumbi", health=4)
    target = _force_minion(state, foe, card_id="pizza", health=3)
    right = _force_minion(state, foe, card_id="vini_zumbi", health=10)
    ch = _add_hand(state, pid, "awp")

    ok, msg = engine.play_card(state, pid, ch.instance_id,
                               chosen_target=target.instance_id,
                               empowered=True)
    assert ok, msg
    # 7 no alvo de vida 3 deixa 4 de excesso para a direita por fallback.
    assert target.health <= 0
    assert right.health == 6
    assert left.health == 4


def test_awp_empowered_accepts_left_direction():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    left = _force_minion(state, foe, card_id="vini_zumbi", health=10)
    target = _force_minion(state, foe, card_id="pizza", health=3)
    right = _force_minion(state, foe, card_id="vini_zumbi", health=10)
    ch = _add_hand(state, pid, "awp")

    ok, msg = engine.play_card(state, pid, ch.instance_id,
                               chosen_target=target.instance_id,
                               empowered=True,
                               direction="LEFT")
    assert ok, msg
    assert target.health <= 0
    assert left.health == 6
    assert right.health == 10


def test_vini_egoista_aura_increases_minion_card_cost():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    _force_minion(state, pid, card_id="vini_egoista")
    ch = _add_hand(state, pid, "vini_zumbi")
    card = get_card("vini_zumbi")

    assert engine.compute_dynamic_cost(state, p, ch, card) == (card["cost"] + 1)


def test_aura_adds_and_removes_tribe_and_stealth():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    ward = _force_minion(state, pid, card_id="vini_ward")
    stealth_enemy = _force_minion(state, foe, card_id="vini_zumbi")
    stealth_enemy.tags.append("STEALTH")

    brasileiro = _force_minion(state, pid, card_id="vini_brasileiro")
    vini = _force_minion(state, pid, card_id="vini_zumbi")

    engine.apply_continuous_effects(state)

    assert "STEALTH" not in stealth_enemy.tags
    assert "BRASIL" in vini.tribes
    assert "BRASIL" in brasileiro.tribes

    state.players[pid].board.remove(ward)
    state.players[pid].board.remove(brasileiro)
    engine.apply_continuous_effects(state)

    assert "STEALTH" in stealth_enemy.tags
    assert "BRASIL" not in vini.tribes


def test_aura_stats_recalculate_without_stacking():
    state = _new_blank_match()
    pid = state.current_player
    memes = _force_minion(state, pid, card_id="memes", attack=2, health=2)
    left = _force_minion(state, pid, card_id="vini_zumbi", attack=2, health=3)
    # coloca left à esquerda de memes e pizza à direita
    state.players[pid].board = [left, memes]
    right = _force_minion(state, pid, card_id="pizza", attack=0, health=2)
    state.players[pid].board = [left, memes, right]

    engine.apply_continuous_effects(state)
    assert left.attack == 3
    assert right.attack == 1

    engine.apply_continuous_effects(state)
    assert left.attack == 3
    assert right.attack == 1

    state.players[pid].board.remove(memes)
    engine.apply_continuous_effects(state)
    assert left.attack == 2
    assert right.attack == 0


def test_pastel_and_mario_verde_auras():
    state = _new_blank_match()
    pid = state.current_player
    pastel = _force_minion(state, pid, card_id="pastel", attack=3, health=2)
    _force_minion(state, 1 - pid, card_id="memes", attack=2, health=2)  # BRASIL fora da adjacência aliada

    engine.apply_continuous_effects(state)
    assert pastel.attack == 4

    state2 = _new_blank_match()
    pid2 = state2.current_player
    mario = _force_minion(state2, pid2, card_id="mario_verde", attack=7, health=8)
    engine.apply_continuous_effects(state2)
    assert mario.attack == 8
    assert mario.health == 9
    assert "TAUNT" in mario.tags

    _force_minion(state2, pid2, card_id="vini_zumbi", attack=2, health=3)
    engine.apply_continuous_effects(state2)
    assert mario.attack == 7
    assert mario.health == 8
    assert "TAUNT" not in mario.tags
