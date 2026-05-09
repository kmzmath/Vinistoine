"""Lote 21 - modo Decks Aleatórios."""
from __future__ import annotations

import asyncio
import random

from server.lobby import LobbyManager, generate_random_deck, start_match_if_ready
from game.cards import get_card, card_max_copies
from game.state import DECK_SIZE


class DummySocket:
    async def send_text(self, payload: str):
        self.last_payload = payload


def test_generate_random_deck_has_30_collectible_cards_and_respects_copy_limit():
    deck = generate_random_deck(rng=random.Random(123))

    assert len(deck) == DECK_SIZE
    counts = {cid: deck.count(cid) for cid in set(deck)}
    for cid, count in counts.items():
        card = get_card(cid)
        assert card is not None
        assert card.get("type") in {"MINION", "SPELL"}
        assert cid != "coin"
        assert count <= card_max_copies(cid)


def test_random_deck_generation_changes_with_seed():
    deck_a = generate_random_deck(rng=random.Random(1))
    deck_b = generate_random_deck(rng=random.Random(2))

    assert deck_a != deck_b


def test_lobby_random_mode_lists_room_and_join_does_not_need_deck():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", [], game_mode="random")

    assert match.game_mode == "random"
    listed = lobby.list_open_matches()
    assert listed == [{
        "code": match.code,
        "host": "Host",
        "mode": "random",
        "mode_label": "Decks aleatórios",
    }]

    joined = lobby.join(match.code, 2, "Guest", [])
    assert joined is match
    assert match.guest_deck == []


def test_random_match_generates_decks_only_when_starting():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", [], game_mode="random")
    lobby.join(match.code, 2, "Guest", [])

    match.sockets[1] = DummySocket()
    match.sockets[2] = DummySocket()
    match.user_to_player[1] = 0
    match.user_to_player[2] = 1

    asyncio.run(start_match_if_ready(match))

    assert match.started is True
    assert match.state is not None

    total_p0 = len(match.state.players[0].deck) + len(match.state.players[0].hand)
    total_p1 = len(match.state.players[1].deck) + len(match.state.players[1].hand)
    assert total_p0 == DECK_SIZE
    assert total_p1 == DECK_SIZE

    all_ids = set(match.state.players[0].deck + [c.card_id for c in match.state.players[0].hand])
    all_ids |= set(match.state.players[1].deck + [c.card_id for c in match.state.players[1].hand])
    assert "coin" not in all_ids
    assert all(get_card(cid) is not None for cid in all_ids)


def test_constructed_lobby_keeps_existing_behavior_and_self_join_block():
    lobby = LobbyManager()
    deck = ["vini_zumbi"] * 30
    match = lobby.create_match(1, "Host", deck)

    assert match.game_mode == "constructed"
    assert lobby.join(match.code, 1, "Host", deck) is None
    assert match.guest_user_id is None
