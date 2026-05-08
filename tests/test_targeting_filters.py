"""Testes dos filtros de targeting usados por cartas com tribos/tags."""
from game import engine, targeting
from game.state import Minion, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, tribes=None, tags=None):
    m = Minion(
        instance_id=gen_id("m_"),
        card_id="test",
        name="Test",
        attack=1,
        health=1,
        max_health=1,
        owner=pid,
        tribes=list(tribes or []),
        tags=list(tags or []),
        summoning_sick=False,
    )
    state.players[pid].board.append(m)
    return m


def test_chosen_target_respeita_required_tribe():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    comida = _force_minion(state, foe, tribes=["COMIDA"])
    outro = _force_minion(state, foe, tribes=["BRASIL"])
    desc = {"mode": "CHOSEN", "valid": ["ENEMY_MINION"], "required_tribe": "COMIDA"}

    assert targeting.resolve_targets(state, desc, pid, None, comida.instance_id) == [comida]
    assert targeting.resolve_targets(state, desc, pid, None, outro.instance_id) == []


def test_same_as_previous_target_reusa_chosen_target():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, tribes=["ITALIA"])
    desc = {"mode": "SAME_AS_PREVIOUS_TARGET", "valid": ["FRIENDLY_MINION"]}

    assert targeting.resolve_targets(state, desc, pid, None, m.instance_id) == [m]
