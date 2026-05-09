"""Lote 31 - Viní em Chamas causa dano em si mesmo."""
from __future__ import annotations

from game import engine
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30,
                            seed=seed, manual_choices=True)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


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
        divine_shield="DIVINE_SHIELD" in (card.get("tags") or []),
    )
    state.players[pid].board.append(m)
    return m


def _add_hand(state, pid, card_id):
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def test_vini_em_chamas_takes_damage_when_friendly_minion_is_played():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.hand.clear()
    p.mana = 10

    vini = _force_minion(state, pid, card_id="vini_em_chamas", health=6)
    enemy = _force_minion(state, foe, card_id="pizza", health=5)
    played_card = _add_hand(state, pid, "vini_zumbi")

    ok, msg = engine.play_card(state, pid, played_card.instance_id)
    assert ok, msg

    played = next(m for m in p.board if m.card_id == "vini_zumbi")
    assert vini.health == 5
    assert played.health == get_card("vini_zumbi")["health"]
    assert enemy.health == 4
