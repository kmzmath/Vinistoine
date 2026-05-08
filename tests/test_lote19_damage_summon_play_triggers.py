"""Lote 19 — triggers de dano, summon e carta jogada."""
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


def test_tronco_reflects_damage_taken_to_damage_source():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    tronco = _force_minion(state, pid, card_id="tronco", attack=0, health=4)
    attacker = _force_minion(state, foe, card_id="vini_zumbi", attack=3, health=5)

    effects.damage_character(state, tronco, 2, source_owner=foe, source_minion=attacker)

    assert tronco.health == 2
    assert attacker.health == 3


def test_baiano_gains_attack_equal_to_damage_taken():
    state = _new_blank_match()
    pid = state.current_player
    baiano = _force_minion(state, pid, card_id="baiano", attack=0, health=12)

    effects.damage_character(state, baiano, 5, source_owner=1-pid)

    assert baiano.health == 7
    assert baiano.attack == 5


def test_iglu_freezes_minion_damaged_by_self():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    iglu = _force_minion(state, pid, card_id="iglu", attack=2, health=7)
    enemy = _force_minion(state, foe, card_id="pizza", attack=0, health=5)

    effects.damage_character(state, enemy, 2, source_owner=pid, source_minion=iglu)

    assert enemy.health == 3
    assert enemy.frozen is True


def test_igao_awakes_when_own_hero_takes_health_damage():
    state = _new_blank_match()
    pid = state.current_player
    igao = effects.summon_minion_from_card(state, pid, "igao")
    assert igao is not None
    assert "DORMANT" in igao.tags
    assert igao.immune is True

    effects.damage_character(state, state.players[pid], 3, source_owner=1-pid)

    assert "DORMANT" not in igao.tags
    assert igao.immune is False
    assert igao.cant_attack is False


def test_igao_does_not_awake_if_armor_absorbs_all_damage():
    state = _new_blank_match()
    pid = state.current_player
    igao = effects.summon_minion_from_card(state, pid, "igao")
    state.players[pid].hero_armor = 5

    effects.damage_character(state, state.players[pid], 3, source_owner=1-pid)

    assert "DORMANT" in igao.tags
    assert igao.immune is True


def test_pera_buffs_health_when_friendly_fruit_is_summoned():
    state = _new_blank_match()
    pid = state.current_player
    pera = _force_minion(state, pid, card_id="pera", attack=2, health=3)

    effects.summon_minion_from_card(state, pid, "laranja")

    assert pera.health == 4
    assert pera.max_health == 4


def test_pera_does_not_buff_on_non_fruit_summon():
    state = _new_blank_match()
    pid = state.current_player
    pera = _force_minion(state, pid, card_id="pera", attack=2, health=3)

    effects.summon_minion_from_card(state, pid, "vini_zumbi")

    assert pera.health == 3
    assert pera.max_health == 3


def test_spiid_heals_when_owner_plays_food_card():
    state = _new_blank_match()
    pid = state.current_player
    spiid = _force_minion(state, pid, card_id="spiid", attack=4, health=9)
    spiid.health = 4
    state.players[pid].mana = 10
    state.players[pid].hand.append(CardInHand(instance_id=gen_id("h_"), card_id="pizza"))

    ok, msg = engine.play_card(state, pid, state.players[pid].hand[-1].instance_id)

    assert ok, msg
    assert spiid.health == 8


def test_spiid_does_not_heal_when_owner_plays_non_food_card():
    state = _new_blank_match()
    pid = state.current_player
    spiid = _force_minion(state, pid, card_id="spiid", attack=4, health=9)
    spiid.health = 4
    state.players[pid].mana = 10
    state.players[pid].hand.append(CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi"))

    ok, msg = engine.play_card(state, pid, state.players[pid].hand[-1].instance_id)

    assert ok, msg
    assert spiid.health == 4
