"""Lote 26 — correções adicionais solicitadas em gameplay."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id


def _new_blank_match(seed: int = 1, manual: bool = False):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30,
                            seed=seed, manual_choices=manual)
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


def test_nando_3_anos_survives_at_zero_health_while_stealthed_immune_then_dies_when_revealed():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    nando = _force_minion(state, pid, card_id="nando_3_anos", ready=True)
    engine.cleanup(state)

    assert nando.health == 0
    assert nando.immune is True
    assert state.find_minion(nando.instance_id) is not None

    target = _force_minion(state, foe, card_id="pizza", attack=0, health=10, ready=True)
    ok, msg = engine.attack(state, pid, nando.instance_id, target.instance_id)
    assert ok, msg
    assert "STEALTH" not in nando.tags

    engine.cleanup(state)
    assert state.find_minion(nando.instance_id) is None


def test_vic_returns_only_when_marked_target_dies_by_attack():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    vic = _force_minion(state, pid, card_id="vic", attack=4, health=2, ready=True)
    enemy = _force_minion(state, foe, card_id="pizza", attack=0, health=3, ready=True)

    effects.resolve_effect(state, get_card("vic")["effects"][0], pid, vic,
                           {"chosen_target": enemy.instance_id})
    ok, msg = engine.attack(state, pid, vic.instance_id, enemy.instance_id)
    assert ok, msg

    assert state.find_minion(vic.instance_id) is None
    assert state.find_minion(enemy.instance_id) is None
    assert [c.card_id for c in state.players[pid].hand].count("vic") == 1
    assert [c.card_id for c in state.players[pid].hand].count("pizza") == 1


def test_vic_does_not_return_if_marked_target_dies_from_non_attack_damage():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    vic = _force_minion(state, pid, card_id="vic", attack=4, health=2, ready=True)
    enemy = _force_minion(state, foe, card_id="pizza", attack=0, health=3, ready=True)

    effects.resolve_effect(state, get_card("vic")["effects"][0], pid, vic,
                           {"chosen_target": enemy.instance_id})
    effects.damage_character(state, enemy, 4, source_owner=pid, source_minion=vic)
    engine.cleanup(state)

    assert state.find_minion(vic.instance_id) is not None
    assert state.find_minion(enemy.instance_id) is None
    assert "pizza" not in [c.card_id for c in state.players[pid].hand]


def test_cardume_de_peixes_summons_four_and_adds_four_to_hand():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    ch = _add_hand(state, pid, "cardume_de_peixes")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    assert [m.card_id for m in p.board].count("peixe") == 4
    assert [c.card_id for c in p.hand].count("peixe") == 4


def test_ramoni_resummons_from_base_stats_then_stops_before_zero_health():
    state = _new_blank_match()
    pid = state.current_player
    ramoni = _force_minion(state, pid, card_id="ramoni", attack=99, health=1)
    ramoni.health = 0
    engine.cleanup(state)

    r1 = next(m for m in state.players[pid].board if m.card_id == "ramoni")
    assert (r1.attack, r1.health, r1.max_health) == (4, 2, 2)

    r1.health = 0
    engine.cleanup(state)
    r2 = next(m for m in state.players[pid].board if m.card_id == "ramoni")
    assert (r2.attack, r2.health, r2.max_health) == (3, 1, 1)

    r2.health = 0
    engine.cleanup(state)
    assert not any(m.card_id == "ramoni" for m in state.players[pid].board)


def test_lamboinha_ma_cozinheiro_can_be_played_without_food_target():
    state = _new_blank_match(manual=True)
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    ch = _add_hand(state, pid, "lamboinha_ma_cozinheiro")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    assert state.pending_choice is None
    assert any(m.card_id == "lamboinha_ma_cozinheiro" for m in p.board)


def test_troca_justa_draws_even_when_no_card_to_discard():
    state = _new_blank_match(manual=True)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck = ["pizza"]
    ch = _add_hand(state, pid, "troca_justa")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    assert state.pending_choice is None
    assert [c.card_id for c in p.hand] == ["pizza"]


def test_sagrado_rafa_grants_divine_shield_only_to_played_minions():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    _force_minion(state, pid, card_id="sagrado_rafa")
    ch = _add_hand(state, pid, "vini_zumbi")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    played = next(m for m in p.board if m.card_id == "vini_zumbi")
    assert played.divine_shield is True
    assert "DIVINE_SHIELD" in played.tags

    summoned = effects.summon_minion_from_card(state, pid, "pizza")
    assert summoned is not None
    assert summoned.divine_shield is False


def test_vini_religioso_sets_hero_max_health_to_45_then_heals_above_30():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hero_health = 25
    p.hand.clear()
    ch = _add_hand(state, pid, "vini_religioso")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    assert p.hero_max_health == 45
    assert p.hero_health == 40


def test_lamboinha_rook_and_how_adds_six_mana_six_six_copy():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    target = _force_minion(state, foe, card_id="vini_zumbi", attack=2, health=3)
    ch = _add_hand(state, pid, "lamboinha_rook_and_how")

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=target.instance_id)
    assert ok, msg

    copied = next(c for c in p.hand if c.card_id == "vini_zumbi")
    assert copied.cost_override == 6
    card = get_card("vini_zumbi")
    assert (card["attack"] + copied.stat_modifier["attack"]) == 6
    assert (card["health"] + copied.stat_modifier["health"]) == 6


def test_empowered_spell_costs_one_extra_mana():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 3
    p.hand.clear()
    enemy = _force_minion(state, foe, card_id="pizza", health=3)
    ch = _add_hand(state, pid, "absorver")  # custo 3, fortalecer deve custar 4

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=enemy.instance_id,
                               empowered=True)
    assert not ok
    assert "Mana insuficiente" in msg

    p.mana = 4
    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=enemy.instance_id,
                               empowered=True)
    assert ok, msg
    assert p.mana == 0
