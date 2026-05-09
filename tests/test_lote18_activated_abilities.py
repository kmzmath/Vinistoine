"""Lote 18 - habilidades ativadas e efeitos durante o turno."""
from __future__ import annotations

from game import engine
from game.cards import get_card
from game.state import Minion, gen_id


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


def test_ramoninho_activated_ability_uses_charge_and_damages_enemy_minion():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    ramon = _force_minion(state, pid, card_id="ramoninho_mestre_da_nerf")
    enemy = _force_minion(state, foe, card_id="pizza", health=5)
    state.players[pid].mana = 6

    ok, msg = engine.activate_ability(state, pid, ramon.instance_id, chosen_target=enemy.instance_id)
    assert ok, msg

    assert state.players[pid].mana == 6
    assert enemy.health == 2
    assert ramon.ability_uses_remaining.get('0') == 2


def test_ramoninho_activated_ability_has_three_total_free_uses():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    ramon = _force_minion(state, pid, card_id="ramoninho_mestre_da_nerf")
    enemy = _force_minion(state, foe, card_id="pizza", health=20)
    state.players[pid].mana = 0

    for _ in range(3):
        ok, msg = engine.activate_ability(state, pid, ramon.instance_id, chosen_target=enemy.instance_id)
        assert ok, msg

    assert enemy.health == 11
    assert ramon.ability_uses_remaining.get("0") == 0

    ok, msg = engine.activate_ability(state, pid, ramon.instance_id, chosen_target=enemy.instance_id)
    assert not ok
    assert "sem usos" in msg


def test_ramoninho_activated_ability_rejects_without_mana_or_target():
    state = _new_blank_match()
    pid = state.current_player
    ramon = _force_minion(state, pid, card_id="ramoninho_mestre_da_nerf")
    state.players[pid].mana = 0

    ok, msg = engine.activate_ability(state, pid, ramon.instance_id)
    assert not ok
    assert "alvo" in msg.lower()


def test_activated_ability_resets_next_turn():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    ramon = _force_minion(state, pid, card_id="ramoninho_mestre_da_nerf")
    enemy = _force_minion(state, foe, card_id="pizza", health=10)
    state.players[pid].mana = 10

    ok, msg = engine.activate_ability(state, pid, ramon.instance_id, chosen_target=enemy.instance_id)
    assert ok, msg
    assert ramon.ability_uses_remaining.get('0') == 2

    engine.end_turn(state, pid)
    engine.end_turn(state, foe)
    assert state.current_player == pid
    assert ramon.activated_abilities_this_turn == 0


def test_rica_coelinho_during_turn_moves_to_hand():
    state = _new_blank_match()
    pid = state.current_player
    rica = _force_minion(state, pid, card_id="rica_coelinho")
    hand_before = len(state.players[pid].hand)

    ok, msg = engine.activate_ability(state, pid, rica.instance_id, zone="HAND")
    assert ok, msg

    assert state.find_minion(rica.instance_id) is None
    assert len(state.players[pid].hand) == hand_before + 1
    assert state.players[pid].hand[-1].card_id == "rica_coelinho"


def test_rica_coelinho_during_turn_moves_to_deck_and_graveyard():
    state = _new_blank_match()
    pid = state.current_player
    rica = _force_minion(state, pid, card_id="rica_coelinho")
    deck_before = len(state.players[pid].deck)

    ok, msg = engine.activate_ability(state, pid, rica.instance_id, zone="DECK", position="TOP")
    assert ok, msg
    assert state.find_minion(rica.instance_id) is None
    assert len(state.players[pid].deck) == deck_before + 1
    assert state.players[pid].deck[0] == "rica_coelinho"

    rica2 = _force_minion(state, pid, card_id="rica_coelinho")
    ok, msg = engine.activate_ability(state, pid, rica2.instance_id, zone="GRAVEYARD")
    assert ok, msg
    assert state.find_minion(rica2.instance_id) is None
    assert state.graveyard[-1]["card_id"] == "rica_coelinho"


def test_rica_coelinho_during_turn_moves_to_board_position_once():
    state = _new_blank_match()
    pid = state.current_player
    a = _force_minion(state, pid, card_id="vini_zumbi")
    rica = _force_minion(state, pid, card_id="rica_coelinho")
    b = _force_minion(state, pid, card_id="pizza")
    assert [m.instance_id for m in state.players[pid].board] == [a.instance_id, rica.instance_id, b.instance_id]

    ok, msg = engine.activate_ability(state, pid, rica.instance_id, zone="BOARD_POSITION", position=0)
    assert ok, msg
    assert [m.instance_id for m in state.players[pid].board][0] == rica.instance_id

    ok, msg = engine.activate_ability(state, pid, rica.instance_id, zone="BOARD_POSITION", position=2)
    assert not ok
    assert "já usada" in msg
