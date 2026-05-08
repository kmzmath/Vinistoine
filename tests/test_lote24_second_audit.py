"""Lote 24 — segunda auditoria de cartas.

Foca em interações que a cobertura estrutural não pegava:
- efeito de descarte + recrutamento em sequência;
- condição TARGET_IS_FROZEN usada como filtro em alvo coletivo.
"""
from __future__ import annotations

from game import engine
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id


def _new_blank_match(seed: int = 1, manual_choices: bool = False):
    state = engine.new_game(
        "A", ["vini_zumbi"] * 30,
        "B", ["vini_zumbi"] * 30,
        seed=seed,
        manual_choices=manual_choices,
    )
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _add_hand(state, pid, card_id):
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=None, health=None):
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
        summoning_sick=True,
        divine_shield="DIVINE_SHIELD" in (card.get("tags") or []),
    )
    state.players[pid].board.append(m)
    return m


def test_queima_de_estoque_does_not_recruit_twice():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    p.deck = ["pizza", "mamaquinho", "gusneba", "vini_zumbi"]

    queima = _add_hand(state, pid, "queima_de_estoque")
    _add_hand(state, pid, "troca_justa")
    _add_hand(state, pid, "stonks")

    ok, msg = engine.play_card(state, pid, queima.instance_id)
    assert ok, msg

    recruited = [m.card_id for m in p.board]
    assert len(recruited) == 2
    assert all(cid in {"pizza", "mamaquinho", "gusneba", "vini_zumbi"} for cid in recruited)


def test_queima_de_estoque_manual_choice_resumes_with_exact_discard_count():
    state = _new_blank_match(manual_choices=True)
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    p.deck = ["pizza", "mamaquinho", "gusneba"]

    queima = _add_hand(state, pid, "queima_de_estoque")
    spell = _add_hand(state, pid, "troca_justa")
    _add_hand(state, pid, "vini_zumbi")

    ok, msg = engine.play_card(state, pid, queima.instance_id)
    assert ok, msg
    assert state.pending_choice is not None
    choice_id = state.pending_choice["choice_id"]

    ok, msg = engine.resolve_choice(state, pid, choice_id, {"card_ids": [spell.instance_id]})
    assert ok, msg

    assert len(p.board) == 1
    assert p.board[0].card_id in {"pizza", "mamaquinho", "gusneba"}


def test_furia_do_vini_geladinho_damages_minions_after_freezing_them():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()

    ally = _force_minion(state, pid, card_id="pizza", health=5)
    enemy = _force_minion(state, foe, card_id="gusneba", health=5)
    furia = _add_hand(state, pid, "furia_do_vini_geladinho")

    ok, msg = engine.play_card(state, pid, furia.instance_id)
    assert ok, msg

    assert ally.frozen is True
    assert enemy.frozen is True
    assert ally.health == 3
    assert enemy.health == 3
