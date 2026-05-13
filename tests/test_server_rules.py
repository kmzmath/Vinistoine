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


def test_lobby_cancel_match_apenas_host_e_antes_de_iniciar():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", ["vini_zumbi"] * 30)

    assert not lobby.cancel_match(match.match_id, 2)
    assert lobby.get(match.match_id) is match

    assert lobby.cancel_match(match.match_id, 1)
    assert lobby.get(match.match_id) is None
    assert lobby.get_by_code(match.code) is None


def test_lobby_nao_cancela_partida_iniciada():
    lobby = LobbyManager()
    match = lobby.create_match(1, "Host", ["vini_zumbi"] * 30)
    match.started = True

    assert not lobby.cancel_match(match.match_id, 1)
    assert lobby.get(match.match_id) is match
