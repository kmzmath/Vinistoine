"""Testes do Lote 9 - custo, mana e efeitos de próximo turno."""
from __future__ import annotations

import game.cards as _cards_mod
from game import engine, effects
from game.state import CardInHand, gen_id
from game.cards import get_card


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _add_to_hand(state, pid, card_id):
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def test_banana_buffa_proxima_fruta_vida():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.mana = p.max_mana = 10

    banana = _add_to_hand(state, pid, "banana")
    laranja = _add_to_hand(state, pid, "laranja")  # FRUTA 2/1

    ok, msg = engine.play_card(state, pid, banana.instance_id)
    assert ok, msg
    ok, msg = engine.play_card(state, pid, laranja.instance_id)
    assert ok, msg

    played = next(m for m in p.board if m.card_id == "laranja")
    base_hp = get_card("laranja")["health"]
    assert played.max_health == base_hp + 1
    assert played.health == base_hp + 1


def test_la_selecione_reduz_primeiro_lacaio_do_proximo_turno():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]

    effects.resolve_effect(state, {
        "action": "REDUCE_NEXT_TURN_FIRST_MINION_COST",
        "amount": 1,
        "target": {"mode": "SELF_PLAYER"},
    }, pid, None, {})

    engine.end_turn(state, pid)
    engine.end_turn(state, state.current_player)
    assert state.current_player == pid

    p = state.players[pid]
    p.hand.clear()
    p.mana = p.max_mana = 10
    ch = _add_to_hand(state, pid, "vini_zumbi")  # custo 1
    mana_before = p.mana
    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    assert p.mana == mana_before  # custo 1 - 1 = 0


def test_vinas_reducao_condicional_para_vini():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]

    effects.resolve_effect(state, {
        "action": "NEXT_TURN_FIRST_MINION_COST_REDUCTION",
        "amount": 2,
        "conditional_amount": 3,
        "condition": {"type": "CARD_TRIBE", "tribe": "VINI"},
        "target": {"mode": "SELF_PLAYER"},
    }, pid, None, {})

    engine.end_turn(state, pid)
    engine.end_turn(state, state.current_player)
    assert state.current_player == pid

    p = state.players[pid]
    p.hand.clear()
    p.mana = p.max_mana = 10
    ch = _add_to_hand(state, pid, "donut")  # COMIDA custo 2, não VINI
    mana_before = p.mana
    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    assert p.mana == mana_before  # custo 2 - redução base 2

    # Nova aplicação: VINI custo 1 deve receber redução condicional 3 e custar 0.
    effects.resolve_effect(state, {
        "action": "NEXT_TURN_FIRST_MINION_COST_REDUCTION",
        "amount": 2,
        "conditional_amount": 3,
        "condition": {"type": "CARD_TRIBE", "tribe": "VINI"},
        "target": {"mode": "SELF_PLAYER"},
    }, pid, None, {})
    # força ativação como se fosse o próximo turno, sem avançar toda a partida
    for pm in state.pending_modifiers:
        if pm.get("kind") == "next_turn_first_minion_cost_reduction":
            pm["kind"] = "next_card_cost_reduction"
            pm["valid"] = ["MINION"]
            pm["active"] = True
    ch2 = _add_to_hand(state, pid, "vini_zumbi")
    mana_before = p.mana
    ok, msg = engine.play_card(state, pid, ch2.instance_id)
    assert ok, msg
    assert p.mana == mana_before


def test_funkeiro_reduz_mana_disponivel_no_proximo_turno():
    state = _new_blank_match()
    pid = state.current_player
    effects.resolve_effect(state, {
        "action": "REDUCE_MANA_NEXT_TURN",
        "amount": 1,
        "target": {"mode": "SELF_PLAYER"},
    }, pid, None, {})

    engine.end_turn(state, pid)
    engine.end_turn(state, state.current_player)
    p = state.players[pid]
    assert state.current_player == pid
    assert p.mana == max(0, p.max_mana - 1)


def test_draw_card_type_compra_apenas_tipo_especificado():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["camarao", "troca_justa", "banana", "bencao_do_vini_da_luz"]
    p.hand.clear()

    effects.resolve_effect(state, {
        "action": "DRAW_CARD_TYPE",
        "card_type": "SPELL",
        "amount": 2,
        "target": {"mode": "SELF_DECK"},
    }, pid, None, {})

    assert [c.card_id for c in p.hand] == ["troca_justa", "bencao_do_vini_da_luz"]
    assert p.deck == ["camarao", "banana"]


def test_next_spell_custa_vida_em_vez_de_mana():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.mana = 0
    p.hero_health = 20

    _cards_mod._CARDS_BY_ID["fake_costly_spell"] = {
        "id": "fake_costly_spell",
        "name": "Fake Costly Spell",
        "type": "SPELL",
        "cost": 4,
        "tags": [],
        "tribes": [],
        "effects": [],
    }
    effects.resolve_effect(state, {
        "action": "NEXT_SPELL_COSTS_HEALTH_INSTEAD_OF_MANA",
        "target": {"mode": "SELF_PLAYER"},
    }, pid, None, {})
    ch = _add_to_hand(state, pid, "fake_costly_spell")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    assert p.mana == 0
    assert p.hero_health == 16


def test_aliexpress_compra_atrasada_chega_no_proximo_turno_com_custo_reduzido():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["camarao"] + p.deck
    p.hand.clear()

    effects.resolve_effect(state, {
        "action": "DRAW_CARD_DELAYED",
        "amount": 1,
        "delay_turns": 1,
        "cost_modifier": -2,
        "target": {"mode": "SELF_DECK"},
    }, pid, None, {})

    assert not p.hand
    assert p.deck[0] != "camarao"

    engine.end_turn(state, pid)
    engine.end_turn(state, state.current_player)
    p = state.players[pid]
    arrived = [c for c in p.hand if c.card_id == "camarao"]
    assert arrived
    assert arrived[-1].cost_modifier == -2
    assert arrived[-1].effective_cost() == 0
