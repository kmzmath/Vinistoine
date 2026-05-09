"""Lote 29 - animações/features e correções Caverna/Dorminhoco."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id


def _new_game(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["pizza"] * 30, seed=seed, manual_choices=True)
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


def test_caverna_grants_echo_to_friendly_minions_in_hand_and_play_uses_it():
    state = _new_game()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    _force_minion(state, pid, card_id="caverna")
    hand_card = _add_hand(state, pid, "vini_zumbi")

    engine.apply_continuous_effects(state)

    assert "ECHO" in hand_card.extra_tags
    ok, msg = engine.play_card(state, pid, hand_card.instance_id)
    assert ok, msg
    assert any(ch.card_id == "vini_zumbi" and ch.echo_temporary for ch in p.hand)


def test_caverna_echo_marker_is_removed_when_aura_source_leaves():
    state = _new_game()
    pid = state.current_player
    p = state.players[pid]
    cave = _force_minion(state, pid, card_id="caverna")
    hand_card = _add_hand(state, pid, "vini_zumbi")

    engine.apply_continuous_effects(state)
    assert "ECHO" in hand_card.extra_tags

    p.board.remove(cave)
    engine.apply_continuous_effects(state)
    assert "ECHO" not in hand_card.extra_tags
    assert not any(str(t).startswith("_AURA_HAND_TAG:") for t in hand_card.extra_tags)


def test_vini_dorminhoco_awakes_after_two_friendly_minions_are_played_from_hand():
    state = _new_game()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    sleepy = effects.summon_minion_from_card(state, pid, "vini_dorminhoco")
    assert sleepy is not None
    assert "DORMANT" in sleepy.tags

    c1 = _add_hand(state, pid, "vini_zumbi")
    ok, msg = engine.play_card(state, pid, c1.instance_id)
    assert ok, msg
    assert "DORMANT" in sleepy.tags

    c2 = _add_hand(state, pid, "vini_zumbi")
    ok, msg = engine.play_card(state, pid, c2.instance_id)
    assert ok, msg
    assert "DORMANT" not in sleepy.tags
    assert sleepy.summoning_sick is True


def test_stonks_reveals_both_decks_and_animates_winner_draw():
    state = _new_game()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    opp = state.players[foe]
    p.mana = 10
    p.hand.clear()
    p.deck = ["vini_zumbi"]
    opp.deck = ["gusneba"]
    stonks = _add_hand(state, pid, "stonks")

    ok, msg = engine.play_card(state, pid, stonks.instance_id)
    assert ok, msg

    assert any(ev["type"] == "reveal_top_each_deck" and len(ev["cards"]) == 2 for ev in state.event_log)
    draw_events = [ev for ev in state.event_log if ev["type"] == "draw_highest_revealed"]
    assert draw_events
    assert draw_events[-1]["player"] == foe
    assert draw_events[-1]["card_id"] == "gusneba"
    assert opp.hand[-1].card_id == "gusneba"


def test_vini_abridor_reveals_both_decks_and_discards_lowest_with_card_id():
    state = _new_game()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    opp = state.players[foe]
    p.mana = 10
    p.hand.clear()
    p.deck = ["pizza"]       # custo 2
    opp.deck = ["vini_zumbi"]  # custo 1, deve queimar/descartar
    abridor = _add_hand(state, pid, "vini_3_anos_abridor_de_caixa")

    ok, msg = engine.play_card(state, pid, abridor.instance_id)
    assert ok, msg

    assert any(ev["type"] == "reveal_top_each_deck" and len(ev["cards"]) == 2 for ev in state.event_log)
    discards = [ev for ev in state.event_log if ev["type"] == "discard_lowest_revealed"]
    assert discards
    assert discards[-1]["player"] == foe
    assert discards[-1]["card_id"] == "vini_zumbi"
    assert opp.deck == []


def test_vini_flamejante_exposes_public_hero_burning_status_for_ui():
    state = _new_game()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    flame = _add_hand(state, pid, "vini_flamejante")

    ok, msg = engine.play_card(state, pid, flame.instance_id)
    assert ok, msg

    view = state.to_dict(pid)
    assert any(s["kind"] == "hero_burning" and s["player_id"] == foe for s in view["public_statuses"])


def test_frontend_has_reveal_pair_and_fire_frame_support():
    src = __import__("pathlib").Path(__file__).parents[1].joinpath("static/game.html").read_text(encoding="utf-8")
    css = __import__("pathlib").Path(__file__).parents[1].joinpath("static/css/main.css").read_text(encoding="utf-8")
    assert "reveal_top_each_deck" in src
    assert "reveal_pair" in src
    assert "draw_highest_revealed" in src
    assert "discard_lowest_revealed" in src
    assert "hero.burning" in css
