"""Modo dev para playtest de cartas."""
from __future__ import annotations

import asyncio

from game import engine
from game.state import MAX_MANA
from server.lobby import LobbyManager, start_match_if_ready


class DummySocket:
    async def send_text(self, payload: str):
        self.last_payload = payload


def _start_dev_match():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", [], game_mode="dev")
    lobby.join(match.code, 2, "Guest", [])
    match.sockets[1] = DummySocket()
    match.sockets[2] = DummySocket()
    match.user_to_player[1] = 0
    match.user_to_player[2] = 1

    asyncio.run(start_match_if_ready(match))
    return match


def test_lobby_dev_mode_lists_room_and_join_does_not_need_deck():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", [], game_mode="dev")

    assert match.game_mode == "dev"
    assert lobby.list_open_matches() == [{
        "code": match.code,
        "host": "Host",
        "mode": "dev",
        "mode_label": "Modo dev",
    }]

    joined = lobby.join(match.code, 2, "Guest", [])

    assert joined is match
    assert match.guest_deck == []


def test_dev_match_starts_directly_in_playing_phase():
    match = _start_dev_match()

    assert match.started is True
    assert match.state is not None
    assert match.state.dev_mode is True
    assert match.state.phase == "PLAYING"
    assert match.state.turn_number == 1
    assert match.state.mulligan_done == [True, True]


def test_dev_tools_can_add_cards_set_mana_and_clear_hand():
    match = _start_dev_match()
    state = match.state
    assert state is not None
    state.players[0].hand.clear()

    ok, msg = engine.dev_add_card_to_hand(state, 0, "vini_zumbi")
    assert ok, msg
    assert [c.card_id for c in state.players[0].hand] == ["vini_zumbi"]

    ok, msg = engine.dev_set_mana(state, 0, mana=10, max_mana=10)
    assert ok, msg
    assert state.players[0].mana == MAX_MANA
    assert state.players[0].max_mana == MAX_MANA

    ok, msg = engine.dev_clear_hand(state, 0)
    assert ok, msg
    assert state.players[0].hand == []


def test_dev_tools_are_rejected_outside_dev_mode():
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30)

    ok, msg = engine.dev_add_card_to_hand(state, 0, "vini_zumbi")

    assert ok is False
    assert "modo dev" in msg
