"""Lote 28.1 - loader de assets não deve ficar preso em 0%."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_lobby_calls_preload_card_assets_on_init():
    html = (ROOT / "static" / "lobby.html").read_text(encoding="utf-8")
    assert "async function preloadCardAssets()" in html
    assert "preloadCardAssets()," in html
    assert "Promise.allSettled" in html


def test_preload_image_has_timeout_and_overlay_finally_hides():
    for rel in ["static/lobby.html", "static/game.html"]:
        html = (ROOT / rel).read_text(encoding="utf-8")
        assert "setTimeout(() => finish(false), 7000)" in html
        assert "overlay.classList.add(\"hidden\")" in html
        assert "updateAssetProgress(1, 1)" in html
