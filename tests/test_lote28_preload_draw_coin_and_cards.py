"""Lote 28 - preload, animações de compra, Moeda e bugs reportados."""
from __future__ import annotations

from pathlib import Path

from game import engine
from game.cards import get_card, is_collectible_card
from game.state import CardInHand, Minion, gen_id


ROOT = Path(__file__).parents[1]


def _new_started_match(seed: int = 2):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["pizza"] * 30,
                            seed=seed, manual_choices=True)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=None, health=None, ready=True):
    card = get_card(card_id) or {}
    atk = card.get("attack") if attack is None else attack
    hp = card.get("health") if health is None else health
    if atk is None:
        atk = 0
    if hp is None:
        hp = 1
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=card.get("name", card_id),
        attack=atk,
        health=hp,
        max_health=hp,
        tags=list(card.get("tags") or []),
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=pid,
        summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in (card.get("tags") or []),
    )
    state.players[pid].board.append(m)
    return m


def _add_hand(state, pid, card_id):
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def test_coin_is_real_non_collectible_and_second_player_gets_it_after_mulligan():
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["pizza"] * 30,
                            seed=2, manual_choices=True)
    first = state.current_player
    second = 1 - first

    assert len(state.players[first].hand) == 3
    assert len(state.players[second].hand) == 4
    assert get_card("coin") is not None
    assert not is_collectible_card("coin")

    engine.confirm_mulligan(state, first, [])
    assert all(c.card_id != "coin" for p in state.players for c in p.hand)
    engine.confirm_mulligan(state, second, [])

    assert any(c.card_id == "coin" for c in state.players[second].hand)


def test_burgues_adds_coin_alias_to_hand():
    state = _new_started_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    ch = _add_hand(state, pid, "burgues")

    ok, msg = engine.play_card(state, pid, ch.instance_id)

    assert ok, msg
    assert any(c.card_id == "coin" for c in p.hand)


def test_turn_start_draw_event_has_turn_start_reason_for_slow_animation():
    state = _new_started_match()
    turn_draws = [e for e in state.event_log if e.get("type") == "draw" and e.get("reason") == "turn_start"]
    assert turn_draws, "start_turn deve marcar compra automática como turn_start"


def test_iglu_atleta_refresh_after_attacking_only_allows_minion_attack():
    state = _new_started_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    iglu = _force_minion(state, pid, card_id="iglu_atleta", ready=True)
    enemy = _force_minion(state, foe, card_id="pizza", health=5, ready=True)

    ok, msg = engine.attack(state, pid, iglu.instance_id, f"hero:{foe}")
    assert ok, msg
    assert iglu.attacks_this_turn == 1

    ch = _add_hand(state, pid, "vini_zumbi")
    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    assert iglu.can_attack() is True
    assert iglu.can_attack_hero() is False
    targets = engine.list_legal_attack_targets(state, pid, iglu.instance_id)
    assert enemy.instance_id in targets
    assert f"hero:{foe}" not in targets


def test_frontend_preload_hover_delay_draw_durations_and_targeting_fixes_present():
    src = (ROOT / "static" / "game.html").read_text(encoding="utf-8")
    assert "/api/cards?include_tokens=1" in src
    assert "preloadAllAssets" in src
    assert "hoverPreviewTimer = setTimeout" in src
    assert "}, 1500)" in src
    assert "TURN_START_DRAW_MS = 3000" in src
    assert "EFFECT_DRAW_MS = 1000" in src
    assert "OPPONENT_DRAW_MS = 700" in src
    assert 'tgt.mode === "CHOSEN_EACH"' in src
    assert 'v === "ANY_MINION" || v === "MINION"' in src
    assert 'v === "ANY_CHARACTER"' in src
    assert 'action === "ADD_TAG"' in src
    assert 'choice.attack_amount' in src


def test_lobby_preloads_assets_after_login_and_vini_formoso_has_placeholder_image():
    lobby = (ROOT / "static" / "lobby.html").read_text(encoding="utf-8")
    assert "preloadCardAssets" in lobby
    assert "asset-loading-overlay" in lobby
    assert (ROOT / "static" / "cards" / "vini_formoso.png").exists()
