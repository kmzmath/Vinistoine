from __future__ import annotations

from game import engine, effects
import game.cards as cards_mod
from game.state import CardInHand, Minion, gen_id


def _new_match(manual_choices: bool = True):
    state = engine.new_game(
        "A", ["vini_zumbi"] * 30,
        "B", ["vini_zumbi"] * 30,
        seed=1,
        manual_choices=manual_choices,
    )
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, attack=1, health=5):
    m = Minion(
        instance_id=gen_id("m_"), card_id="test", name="Test",
        attack=attack, health=health, max_health=health,
        owner=pid, summoning_sick=False,
    )
    state.players[pid].board.append(m)
    return m


def test_manual_reorder_cria_escolha_pendente_e_resolve():
    state = _new_match(manual_choices=True)
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["vini_zumbi", "camarao", "banana"] + p.deck

    effects.resolve_effect(
        state,
        {"action": "REORDER_TOP_CARDS", "amount": 3, "target": {"mode": "SELF_DECK"}},
        pid, None, {},
    )

    assert state.pending_choice is not None
    assert state.pending_choice["kind"] == "reorder_top_cards"
    choice_id = state.pending_choice["choice_id"]

    ok, msg = engine.attack(state, pid, "missing", "hero:1")
    assert not ok
    assert "pendente" in msg

    ok, msg = engine.resolve_choice(state, pid, choice_id, {"order": [2, 1, 0]})
    assert ok, msg
    assert state.pending_choice is None
    assert p.deck[:3] == ["banana", "camarao", "vini_zumbi"]


def test_manual_swap_revealed_top_cards_resolve():
    state = _new_match(manual_choices=True)
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    me.deck = ["camarao"] + me.deck
    opp.deck = ["banana"] + opp.deck

    effects.resolve_effect(
        state,
        {"action": "OPTIONAL_SWAP_REVEALED_TOP_CARDS", "target": {"mode": "BOTH_DECKS"}},
        pid, None, {},
    )

    assert state.pending_choice is not None
    choice_id = state.pending_choice["choice_id"]
    ok, msg = engine.resolve_choice(state, pid, choice_id, {"swap": True})
    assert ok, msg
    assert me.deck[0] == "banana"
    assert opp.deck[0] == "camarao"


def test_play_card_suporta_multiplos_alvos_em_ordem():
    state = _new_match(manual_choices=False)
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.max_mana = 10

    card_id = "test_double_target_spell"
    cards_mod._CARDS_BY_ID[card_id] = {
        "id": card_id,
        "name": "Double Target",
        "type": "SPELL",
        "cost": 1,
        "tags": [],
        "tribes": [],
        "effects": [{
            "trigger": "ON_PLAY",
            "action": "SEQUENCE",
            "effects": [
                {"action": "DAMAGE", "amount": 1, "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}},
                {"action": "DAMAGE", "amount": 2, "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}},
            ],
        }],
    }

    first = _force_minion(state, foe, health=5)
    second = _force_minion(state, foe, health=5)
    h = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    p.hand.append(h)

    ok, msg = engine.play_card(
        state, pid, h.instance_id,
        chosen_targets=[first.instance_id, second.instance_id],
    )
    assert ok, msg
    assert first.health == 4
    assert second.health == 3

    cards_mod._CARDS_BY_ID.pop(card_id, None)
