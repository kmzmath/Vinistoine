"""Lote 5 — escolhas de descarte/compra e cartas recém-compradas."""
from __future__ import annotations

from game import engine, effects
from game.state import CardInHand, gen_id, Minion


def _ready_state(manual_choices: bool = False):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30,
                            seed=1, manual_choices=manual_choices)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    pid = state.current_player
    state.players[pid].mana = 10
    state.players[pid].max_mana = 10
    return state, pid, state.players[pid]


def _add_hand(p, card_id: str) -> CardInHand:
    c = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    p.hand.append(c)
    return c


def test_troca_justa_escolhe_descarte_e_continua_comprando():
    state, pid, p = _ready_state(manual_choices=True)
    p.hand = []
    troca = _add_hand(p, "troca_justa")
    discard_me = _add_hand(p, "camarao")
    keep_me = _add_hand(p, "banana")
    p.deck = ["peixe"] + p.deck

    ok, msg = engine.play_card(state, pid, troca.instance_id)
    assert ok, msg
    choice = state.pending_choice
    assert choice and choice["kind"] == "discard_hand_card"
    assert {c["card_id"] for c in choice["cards"]} == {"camarao", "banana"}

    ok, msg = engine.resolve_choice(state, pid, choice["choice_id"],
                                    {"card_id": discard_me.instance_id})
    assert ok, msg
    assert state.pending_choice is None
    assert [c.card_id for c in p.hand] == ["banana", "peixe"]
    assert keep_me in p.hand


def test_sas_escolhe_uma_carta_do_topo_e_descarta_a_outra():
    state, pid, p = _ready_state(manual_choices=True)
    p.hand = []
    sas = _add_hand(p, "sas")
    p.deck = ["banana", "camarao", "peixe"]

    ok, msg = engine.play_card(state, pid, sas.instance_id)
    assert ok, msg
    choice = state.pending_choice
    assert choice and choice["kind"] == "choose_draw_discard"
    assert choice["cards"] == ["banana", "camarao"]

    ok, msg = engine.resolve_choice(state, pid, choice["choice_id"], {"index": 1})
    assert ok, msg
    assert [c.card_id for c in p.hand] == ["camarao"]
    assert p.deck == ["peixe"]
    assert any(e["type"] == "discard_from_deck_choice" and e["card_id"] == "banana"
               for e in state.event_log)


def test_investidor_oponente_rouba_uma_das_tres_compradas_sem_compra_extra():
    state, pid, p = _ready_state()
    opp = state.opponent_of(pid)
    p.deck = ["banana", "camarao", "peixe", "vini_zumbi"]
    p.hand = []
    opp.hand = []

    effects.resolve_effect(state, {"action": "DRAW_CARD", "amount": 3,
                                   "target": {"mode": "SELF_PLAYER"}},
                           pid, None, {})
    assert [c.card_id for c in p.hand] == ["banana", "camarao", "peixe"]
    effects.resolve_effect(state, {"action": "OPPONENT_STEALS_RANDOM_DRAWN_CARD", "amount": 1},
                           pid, None, {})

    assert len(p.hand) == 2
    assert len(opp.hand) == 1
    assert len(p.deck) == 1  # não comprou carta extra durante o roubo
    assert opp.hand[0].card_id in {"banana", "camarao", "peixe"}


def test_foco_buffa_lacaio_recem_comprado_na_mao():
    state, pid, p = _ready_state()
    p.deck = ["pizza", "camarao", "banana"]  # camarao é o menor custo
    p.hand = []
    effects.resolve_effect(state, {"action": "DRAW_LOWEST_COST_MINION", "amount": 1,
                                   "target": {"mode": "SELF_DECK"}},
                           pid, None, {})
    effects.resolve_effect(state, {"action": "BUFF_DRAWN_CARD", "attack": 2, "health": 2,
                                   "target": {"mode": "DRAWN_CARD"}},
                           pid, None, {})
    assert len(p.hand) == 1
    drawn = p.hand[0]
    assert drawn.card_id == "camarao"
    assert drawn.stat_modifier == {"attack": 2, "health": 2}


def test_guilaozinho_reduz_custo_da_carta_recem_comprada():
    state, pid, p = _ready_state()
    p.deck = ["banana", "peixe"]
    p.hand = []
    effects.resolve_effect(state, {"action": "DRAW_CARD", "amount": 1,
                                   "target": {"mode": "SELF_PLAYER"}, "reveal": True},
                           pid, None, {})
    effects.resolve_effect(state, {"action": "REDUCE_COST", "amount": 1,
                                   "target": {"mode": "DRAWN_CARD"}},
                           pid, None, {})
    assert len(p.hand) == 1
    assert p.hand[0].card_id == "banana"
    assert p.hand[0].cost_modifier == -1
    assert any(e["type"] == "reveal_drawn_card" and e["card_id"] == "banana"
               for e in state.event_log)
