"""Testes de regras do servidor/lobby."""
import pytest
from server.lobby import LobbyManager


def test_lobby_bloqueia_self_join():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", ["vini_zumbi"] * 30)

    joined = lobby.join(match.code, 1, "Host", ["vini_zumbi"] * 30)

    assert joined is None
    assert match.guest_user_id is None


def test_validate_deck_rejeita_coin():
    pytest.importorskip("sqlalchemy")
    from server.main import validate_deck

    deck = ["vini_zumbi"] * 29 + ["coin"]
    err = validate_deck(deck)

    assert err is not None
    assert "não permitida" in err
