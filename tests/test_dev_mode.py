"""Modo dev para playtest singleplayer de cartas."""
from __future__ import annotations

import asyncio
import json

from game import engine
from game.state import MAX_MANA
from server.lobby import LobbyManager, start_match_if_ready


class DummySocket:
    async def send_text(self, payload: str):
        self.last_payload = payload


def _start_dev_match():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", [], game_mode="dev")
    match.sockets[1] = DummySocket()
    match.user_to_player[1] = 0

    asyncio.run(start_match_if_ready(match))
    return match


def test_lobby_dev_mode_is_singleplayer_and_not_public_joinable():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", [], game_mode="dev")

    assert match.game_mode == "dev"
    assert lobby.list_open_matches() == []
    assert lobby.join(match.code, 2, "Guest", []) is None
    assert match.guest_user_id is None


def test_dev_match_starts_with_only_host_socket_in_playing_phase():
    match = _start_dev_match()

    assert match.started is True
    assert match.state is not None
    assert match.guest_nickname == "Oponente Dev"
    assert match.state.dev_mode is True
    assert match.state.phase == "PLAYING"
    assert match.state.turn_number == 1
    assert match.state.mulligan_done == [True, True]

    payload = json.loads(match.sockets[1].last_payload)
    assert payload["type"] == "state"
    assert payload["state"]["dev_mode"] is True
    assert any(c.get("card_id") for c in payload["state"]["opponent"]["hand"])


def test_dev_tools_can_control_both_players_hand_deck_draw_mana_and_board():
    match = _start_dev_match()
    state = match.state
    assert state is not None
    state.players[0].hand.clear()
    state.players[0].deck.clear()
    state.players[1].hand.clear()
    state.players[1].board.clear()

    ok, msg = engine.dev_add_card_to_hand(state, 0, "vini_zumbi", target_player_id=1)
    assert ok, msg
    assert [c.card_id for c in state.players[1].hand] == ["vini_zumbi"]

    ok, msg = engine.dev_set_mana(state, 0, mana=10, max_mana=10, target_player_id=1)
    assert ok, msg
    assert state.players[1].mana == MAX_MANA
    assert state.players[1].max_mana == MAX_MANA

    ok, msg = engine.dev_add_card_to_deck(state, 0, "vini_zumbi", target_player_id=0)
    assert ok, msg
    assert state.players[0].deck[0] == "vini_zumbi"

    ok, msg = engine.dev_draw_card(state, 0, "vini_zumbi", target_player_id=0)
    assert ok, msg
    assert [c.card_id for c in state.players[0].hand] == ["vini_zumbi"]

    ok, msg = engine.dev_summon_minion(state, 0, "vini_zumbi", target_player_id=1)
    assert ok, msg
    assert [m.card_id for m in state.players[1].board] == ["vini_zumbi"]

    ok, msg = engine.dev_clear_hand(state, 0, target_player_id=1)
    assert ok, msg
    assert state.players[1].hand == []


def test_dev_tools_are_rejected_outside_dev_mode():
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30)

    ok, msg = engine.dev_add_card_to_hand(state, 0, "vini_zumbi")

    assert ok is False
    assert "modo dev" in msg
