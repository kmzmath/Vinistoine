"""Lote 32 - limpeza final de branding visível."""
from __future__ import annotations

from pathlib import Path


VISIBLE_FILES = [
    "static/index.html",
    "static/lobby.html",
    "static/deckbuilder.html",
    "static/game.html",
]


def test_visible_branding_is_vinistone():
    root = Path(__file__).resolve().parents[1]
    visible = "\n".join((root / rel).read_text(encoding="utf-8") for rel in VISIBLE_FILES)

    assert "Vinístone" in visible
    assert "Carta & Lâmina" not in visible
    assert "Carta &amp; Lâmina" not in visible
    assert "um jogo entre amigos" not in visible


def test_tab_titles_use_vinistone():
    root = Path(__file__).resolve().parents[1]
    assert "<title>Vinístone</title>" in (root / "static/index.html").read_text(encoding="utf-8")
    assert "<title>Saguão - Vinístone</title>" in (root / "static/lobby.html").read_text(encoding="utf-8")
    assert "<title>Construtor de Decks - Vinístone</title>" in (root / "static/deckbuilder.html").read_text(encoding="utf-8")
    assert "<title>Partida - Vinístone</title>" in (root / "static/game.html").read_text(encoding="utf-8")


def test_audio_surrender_row_hidden_rule_overrides_flex_display():
    root = Path(__file__).resolve().parents[1]
    css = (root / "static/css/main.css").read_text(encoding="utf-8")

    assert ".audio-settings-panel .aud-row[hidden] { display: none; }" in css
