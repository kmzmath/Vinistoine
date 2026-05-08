"""
Loader e templates de cartas. As cartas são carregadas do JSON e indexadas por id.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional


_CARDS_BY_ID: dict[str, dict] = {}
_ALL_CARDS: list[dict] = []
_LOADED = False


def load_cards(path: Optional[Path] = None) -> list[dict]:
    """Carrega cartas do JSON. Idempotente."""
    global _CARDS_BY_ID, _ALL_CARDS, _LOADED
    if _LOADED:
        return _ALL_CARDS
    if path is None:
        path = Path(__file__).parent / "data" / "cards.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Adiciona cartas auxiliares (tokens) que aparecem em efeitos mas não na lista
    aux_cards = [
        {
            "id": "coin",
            "name": "Moeda",
            "type": "SPELL",
            "cost": 0,
            "text": "Ganhe 1 Cristal de Mana este turno apenas.",
            "tags": [],
            "tribes": [],
            "effects": [
                {"trigger": "ON_PLAY", "action": "GAIN_TEMP_MANA", "amount": 1,
                 "target": {"mode": "SELF_PLAYER"}}
            ],
        },
    ]
    for c in aux_cards:
        if not any(card["id"] == c["id"] for card in data):
            data.append(c)

    _ALL_CARDS = data
    _CARDS_BY_ID = {c["id"]: c for c in data}
    _LOADED = True
    return _ALL_CARDS


def get_card(card_id: str) -> Optional[dict]:
    if not _LOADED:
        load_cards()
    return _CARDS_BY_ID.get(card_id)


def all_cards() -> list[dict]:
    if not _LOADED:
        load_cards()
    return list(_ALL_CARDS)


def card_has_tribe(card: dict, tribe: str) -> bool:
    """Considera tribos derivadas. Toda FRUTA é também COMIDA."""
    if not card:
        return False
    tribes = card.get("tribes") or []
    if tribe in tribes:
        return True
    if tribe == "COMIDA" and "FRUTA" in tribes:
        return True
    return False


def is_collectible_card(card_id: str) -> bool:
    """Cartas que podem aparecer no deckbuilder/decks salvos.

    Tokens auxiliares usados pela engine, como a Moeda, continuam disponíveis
    via get_card(), mas não são colecionáveis.
    """
    return card_id not in {"coin", "moeda"}


def card_max_copies(card_id: str) -> int:
    """Quantas cópias da mesma carta podem entrar num deck. Usamos limite simples."""
    return 2  # Hearthstone-like: até 2 (e legendárias 1, mas simplificamos)
