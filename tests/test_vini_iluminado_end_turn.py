from game import engine, effects
from game.cards import get_card
from game.state import Minion, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    state.manual_choices = True
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=2, health=3, max_health=None):
    card = get_card(card_id) or {}
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=card.get("name", card_id),
        attack=attack,
        health=health,
        max_health=max_health if max_health is not None else health,
        tags=list(card.get("tags") or []),
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=pid,
        summoning_sick=False,
    )
    state.players[pid].board.append(m)
    return m


def test_vini_iluminado_revives_dead_this_turn_once_and_finishes_turn():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    vini = _force_minion(state, pid, card_id="vini_o_iluminado", health=6)
    ally = _force_minion(state, pid, card_id="vini_zumbi", health=3, max_health=3)

    effects.damage_character(state, ally, 5, source_owner=foe)
    engine.cleanup(state)

    assert ally not in state.players[pid].board
    rec_idx = len(state.graveyard) - 1
    assert state.graveyard[rec_idx]["health_at_death"] == -2
    assert state.graveyard[rec_idx]["turn_number"] == state.turn_number

    assert engine.end_turn(state, pid) is True
    choice = state.pending_choice
    assert choice is not None
    assert choice["kind"] == "heal_or_revive_friendly"
    dead_options = [o for o in choice["options"] if o.get("kind") == "dead_minion"]
    assert dead_options == [{
        "id": f"dead:{rec_idx}",
        "kind": "dead_minion",
        "card_id": "vini_zumbi",
        "name": "Vini Zumbi",
        "revived_health": 1,
    }]

    ok, msg = engine.resolve_choice(state, pid, choice["choice_id"], {"target_id": f"dead:{rec_idx}"})
    assert ok, msg
    assert state.pending_choice is None
    assert state.current_player == foe
    revived = [m for m in state.players[pid].board if m.card_id == "vini_zumbi"]
    assert len(revived) == 1
    assert revived[0].health == 1
    assert vini in state.players[pid].board


def test_vini_iluminado_does_not_offer_dead_from_previous_turn():
    state = _new_blank_match()
    pid = state.current_player
    _force_minion(state, pid, card_id="vini_o_iluminado", health=6)
    state.graveyard.append({
        "card_id": "vini_zumbi",
        "owner": pid,
        "name": "Vini Zumbi",
        "turn_number": state.turn_number - 1,
        "health_at_death": -2,
    })

    assert engine.end_turn(state, pid) is True
    choice = state.pending_choice
    assert choice is not None
    assert choice["kind"] == "heal_or_revive_friendly"
    assert not [o for o in choice["options"] if o.get("kind") == "dead_minion"]
