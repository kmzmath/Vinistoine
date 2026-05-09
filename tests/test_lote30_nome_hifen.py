"""Lote 30 - nome do jogo e hífen curto em textos visíveis."""
from __future__ import annotations

from pathlib import Path


TEXT_EXTS = {".html", ".css", ".js", ".md", ".txt", ".yaml", ".yml", ".json"}


def test_visible_game_name_and_short_hyphen():
    root = Path(__file__).resolve().parents[1]
    visible_files = [
        root / "README.md",
        root / "static" / "index.html",
        root / "static" / "lobby.html",
        root / "static" / "game.html",
        root / "static" / "deckbuilder.html",
    ]
    text = "\n".join(p.read_text(encoding="utf-8") for p in visible_files if p.exists())

    assert "Vinístone" in text
    assert "Vinistone" not in text
    assert "ViniStone" not in text
    assert not any(ch in text for ch in ["—", "–", "‑", "‒", "―"])
