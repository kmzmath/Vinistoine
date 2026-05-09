"""Lote 16 - mão/deck/zonas e mana extra."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="vini_zumbi", attack=None, health=None,
                  tags=None, ready=True):
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
        tags=list(tags if tags is not None else (card.get("tags") or [])),
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=pid,
        summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


def _add_hand(state, pid, card_id):
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def test_discard_up_to_cards_discards_spells_and_recruits_same_count():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    spell1 = _add_hand(state, pid, "troca_justa")
    spell2 = _add_hand(state, pid, "stonks")
    minion_card = _add_hand(state, pid, "vini_zumbi")
    p.deck = ["pizza", "mamaquinho", "troca_justa"] + p.deck

    eff = {
        "action": "DISCARD_UP_TO_CARDS",
        "amount": 2,
        "filter": {"type": "SPELL"},
        "target": {"mode": "SELF_HAND"},
    }
    effects.resolve_effect(state, eff, pid, None,
                           {"card_ids": [spell1.instance_id, spell2.instance_id, minion_card.instance_id]})

    assert all(ch.instance_id not in {spell1.instance_id, spell2.instance_id} for ch in p.hand)
    assert any(ch.instance_id == minion_card.instance_id for ch in p.hand)
    assert [m.card_id for m in p.board[-2:]] == ["pizza", "mamaquinho"]


def test_discard_up_to_cards_manual_choice():
    state = _new_blank_match()
    state.manual_choices = True
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    spell = _add_hand(state, pid, "troca_justa")
    _add_hand(state, pid, "vini_zumbi")
    p.deck = ["pizza"] + p.deck

    eff = {"action": "DISCARD_UP_TO_CARDS", "amount": 2,
           "filter": {"type": "SPELL"}, "target": {"mode": "SELF_HAND"}}
    effects.resolve_effect(state, eff, pid, None, {})
    assert state.pending_choice is not None
    choice_id = state.pending_choice["choice_id"]

    ok, msg = engine.resolve_choice(state, pid, choice_id, {"card_ids": [spell.instance_id]})
    assert ok, msg
    assert p.board[-1].card_id == "pizza"


def test_destroy_minions_in_deck_by_cost_threshold():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["vini_zumbi", "pizza", "mamaquinho", "troca_justa", "ravioli"]

    eff = {
        "action": "DESTROY_MINIONS_IN_DECK_BY_COST",
        "cost_threshold": "X",
        "comparison": "LESS_THAN_OR_EQUAL",
        "target": {"mode": "SELF_DECK"},
    }
    effects.resolve_effect(state, eff, pid, None, {"x": 2})

    assert "vini_zumbi" not in p.deck
    assert "pizza" not in p.deck
    assert "mamaquinho" not in p.deck
    assert "troca_justa" in p.deck  # feitiço não é destruído
    assert "ravioli" in p.deck      # custo maior


def test_move_self_to_hand_deck_and_graveyard():
    state = _new_blank_match()
    pid = state.current_player

    m = _force_minion(state, pid, card_id="rica_coelinho")
    effects.resolve_effect(state, {"action": "MOVE_SELF_TO_ZONE",
                                   "valid_zones": ["HAND"], "target": {"mode": "SELF"}},
                           pid, m, {"zone": "HAND"})
    assert state.find_minion(m.instance_id) is None
    assert state.players[pid].hand[-1].card_id == "rica_coelinho"

    m2 = _force_minion(state, pid, card_id="rica_coelinho")
    deck_before = len(state.players[pid].deck)
    effects.resolve_effect(state, {"action": "MOVE_SELF_TO_ZONE",
                                   "valid_zones": ["DECK"], "target": {"mode": "SELF"}},
                           pid, m2, {"zone": "DECK", "position": "TOP"})
    assert state.find_minion(m2.instance_id) is None
    assert len(state.players[pid].deck) == deck_before + 1
    assert state.players[pid].deck[0] == "rica_coelinho"

    m3 = _force_minion(state, pid, card_id="rica_coelinho")
    effects.resolve_effect(state, {"action": "MOVE_SELF_TO_ZONE",
                                   "valid_zones": ["GRAVEYARD"], "target": {"mode": "SELF"}},
                           pid, m3, {"zone": "GRAVEYARD"})
    assert state.find_minion(m3.instance_id) is None
    assert state.graveyard[-1]["card_id"] == "rica_coelinho"


def test_death_replacement_shuffle_into_deck():
    state = _new_blank_match()
    pid = state.current_player
    aura = _force_minion(state, pid, card_id="lamboia_religioso")
    ally = _force_minion(state, pid, card_id="pizza")
    deck_before = len(state.players[pid].deck)

    ally.health = 0
    engine.cleanup(state)

    assert state.find_minion(ally.instance_id) is None
    assert len(state.players[pid].deck) == deck_before + 1
    assert "pizza" in state.players[pid].deck
    assert not any(g["card_id"] == "pizza" for g in state.graveyard)


def test_spend_extra_mana_buff_self():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 4
    m = _force_minion(state, pid, card_id="dani_perereca", attack=2, health=3)

    eff = {"action": "SPEND_EXTRA_MANA_BUFF_SELF", "amount": "X",
           "target": {"mode": "SELF"}, "buff": {"attack": "X", "health": "X"}}
    effects.resolve_effect(state, eff, pid, m, {"x": 3})

    assert p.mana == 1
    assert m.attack == 5
    assert m.health == 6
    assert m.max_health == 6


def test_spend_extra_mana_add_copies_to_deck():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 3
    m = _force_minion(state, pid, card_id="perereca_formoso")
    deck_before = len(p.deck)

    eff = {"action": "SPEND_EXTRA_MANA_ADD_COPIES_TO_DECK", "variable": "X",
           "copy_multiplier": 2, "target": {"mode": "SELF_DECK", "position": "CHOSEN"}}
    effects.resolve_effect(state, eff, pid, m, {"x": 2, "position": "BOTTOM"})

    assert p.mana == 1
    assert len(p.deck) == deck_before + 4
    assert p.deck[-4:] == ["perereca_formoso"] * 4
