"""Lote 23 - bugs prováveis encontrados por auditoria estática."""
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


def test_vini_formoso_adds_bottom_copy_with_plus_one_plus_one():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    p.deck = ["pizza"]
    ch = _add_hand(state, pid, "vini_formoso")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    entry = p.deck[-1]
    assert entry in state.deck_card_modifiers
    mod = state.deck_card_modifiers[entry]
    assert mod["card_id"] == "vini_formoso"
    assert mod["stat_modifier"] == {"attack": 1, "health": 1}


def test_estrategista_returned_card_costs_one_only_this_turn():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    target = _force_minion(state, pid, card_id="pizza")
    ch = _add_hand(state, pid, "estrategista")

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=target.instance_id)
    assert ok, msg

    returned = next(c for c in p.hand if c.card_id == "pizza")
    assert returned.cost_override == 1

    engine.end_turn(state, pid)
    assert returned.cost_override is None


def test_rica_gets_health_for_each_OTHER_friendly_minion():
    """Rica conta apenas OUTROS lacaios aliados (não inclui a si mesma).
    Texto: '+1 de vida para cada outro lacaio aliado.'"""
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    _force_minion(state, pid, card_id="pizza")
    ch = _add_hand(state, pid, "rica")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    rica = p.board[-1]
    # Apenas a Pizza conta (Rica não conta a si mesma) = +1 de vida.
    assert rica.max_health == (get_card("rica")["health"] + 1)
    assert rica.health == rica.max_health


def test_limpa_limpa_damages_enemy_minions_by_enemy_board_count():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    a = _force_minion(state, foe, card_id="pizza", health=10)
    b = _force_minion(state, foe, card_id="pizza", health=10)
    c = _force_minion(state, foe, card_id="pizza", health=10)
    ch = _add_hand(state, pid, "limpa_limpa")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    assert [a.health, b.health, c.health] == [7, 7, 7]


def test_frifas_draws_preferred_spell_from_deck():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck = ["stonks", "saudades", "vini_zumbi"]
    frifas = _force_minion(state, pid, card_id="frifas")
    frifas.health = 0

    engine.cleanup(state)

    assert p.hand[-1].card_id == "saudades"


def test_vini_flamenguista_draws_preferred_minion_from_deck():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    p.deck = ["vini_zumbi", "la_selecione", "stonks"]
    ch = _add_hand(state, pid, "vini_flamenguista")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    assert p.hand[-1].card_id == "la_selecione"


def test_vinassito_prefers_vic_or_fera_minion():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    p.deck = ["vini_zumbi", "peixe", "stonks"]
    ch = _add_hand(state, pid, "vinassito")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    assert p.hand[-1].card_id == "peixe"


def test_drenar_almas_lifesteal_heals_hero():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.hero_health = 20
    p.mana = 10
    p.hand.clear()
    _force_minion(state, pid, card_id="pizza", health=5)
    _force_minion(state, foe, card_id="pizza", health=5)
    ch = _add_hand(state, pid, "drenar_almas")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    assert p.hero_health > 20


def test_vineba_flamejante_deals_attack_damage_to_adjacent_minions():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    vineba = _force_minion(state, pid, card_id="vineba_flamejante", attack=4, health=5, ready=True)
    left = _force_minion(state, foe, card_id="pizza", attack=0, health=10, ready=True)
    target = _force_minion(state, foe, card_id="pizza", attack=0, health=10, ready=True)
    right = _force_minion(state, foe, card_id="pizza", attack=0, health=10, ready=True)
    state.players[foe].board = [left, target, right]

    ok, msg = engine.attack(state, pid, vineba.instance_id, target.instance_id)
    assert ok, msg

    assert left.health == 6
    assert right.health == 6
