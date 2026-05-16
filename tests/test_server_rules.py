"""Testes de regras do servidor/lobby."""
import json
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


def test_update_deck_edita_deck_existente_sem_duplicar(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy")
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from game.cards import all_cards, card_max_copies, load_cards
    from server import main
    from server.db import Base, Deck, User

    load_cards()
    card_ids: list[str] = []
    for card in all_cards():
        cid = card.get("id")
        if card.get("type") not in {"MINION", "SPELL"} or cid in main.NON_COLLECTIBLE_CARD_IDS:
            continue
        card_ids.extend([cid] * min(card_max_copies(cid), 2))
        if len(card_ids) >= 32:
            break
    original_cards = card_ids[:30]
    updated_cards = card_ids[2:32]

    engine = create_engine(f"sqlite:///{tmp_path / 'decks.db'}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        user = User(nickname="Tester", password_hash="hash")
        s.add(user)
        s.commit()
        s.refresh(user)
        deck = Deck(user_id=user.id, name="Antigo", cards_json=json.dumps(original_cards))
        s.add(deck)
        s.commit()
        s.refresh(deck)
        user_id = user.id
        deck_id = deck.id

    monkeypatch.setattr(main, "get_session", lambda: Session(engine))
    monkeypatch.setattr(main, "require_user", lambda request: SimpleNamespace(id=user_id))

    response = main.update_deck(deck_id, main.DeckIn(name="Atualizado", cards=updated_cards), None)

    assert response == {"id": deck_id, "name": "Atualizado", "cards": updated_cards}
    with Session(engine) as s:
        decks = s.query(Deck).filter(Deck.user_id == user_id).all()
        assert len(decks) == 1
        assert decks[0].id == deck_id
        assert decks[0].name == "Atualizado"
        assert decks[0].card_ids() == updated_cards


def test_deck_code_roundtrip_e_import_salva_deck(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy")
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from game.cards import all_cards, card_max_copies, load_cards
    from server import main
    from server.db import Base, Deck, User

    load_cards()
    card_ids: list[str] = []
    for card in all_cards():
        cid = card.get("id")
        if card.get("type") not in {"MINION", "SPELL"} or cid in main.NON_COLLECTIBLE_CARD_IDS:
            continue
        card_ids.extend([cid] * min(card_max_copies(cid), 2))
        if len(card_ids) >= 30:
            break
    cards = card_ids[:30]

    code = main.encode_deck_code("Deck Compartilhado", cards)
    decoded = main.decode_deck_code(code)
    assert decoded == {"name": "Deck Compartilhado", "cards": cards}
    assert main.validate_deck(decoded["cards"]) is None

    engine = create_engine(f"sqlite:///{tmp_path / 'decks.db'}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        user = User(nickname="Importer", password_hash="hash")
        s.add(user)
        s.commit()
        s.refresh(user)
        user_id = user.id

    monkeypatch.setattr(main, "get_session", lambda: Session(engine))
    monkeypatch.setattr(main, "require_user", lambda request: SimpleNamespace(id=user_id))

    response = main.import_deck_code(main.DeckImportIn(code=code), None)

    assert response["name"] == "Deck Compartilhado"
    assert response["cards"] == cards
    with Session(engine) as s:
        decks = s.query(Deck).filter(Deck.user_id == user_id).all()
        assert len(decks) == 1
        assert decks[0].card_ids() == cards
