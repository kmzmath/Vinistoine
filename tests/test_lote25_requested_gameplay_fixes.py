"""Lote 25 — correções solicitadas após teste em partida real."""
from __future__ import annotations

import random

from game import engine, effects
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id, DECK_SIZE
from server.lobby import generate_random_deck


def _new_blank_match(seed: int = 1, manual: bool = True):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30,
                            seed=seed, manual_choices=manual)
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


def test_random_deck_singleton_no_repetitions():
    deck = generate_random_deck(rng=random.Random(42))
    assert len(deck) == DECK_SIZE
    assert len(set(deck)) == DECK_SIZE


def test_freeze_consumes_only_one_attack_opportunity():
    state = _new_blank_match(manual=False)
    pid = state.current_player
    foe = 1 - pid
    m = _force_minion(state, pid, attack=3, health=3, ready=True)

    effects.resolve_effect(
        state,
        {"action": "FREEZE", "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}},
        foe,
        None,
        {"chosen_target": m.instance_id},
    )

    assert m.frozen
    assert not m.can_attack()
    engine.end_turn(state, pid)
    assert not m.frozen


def test_spaghetti_after_attacking_enemy_hero_opens_choice_and_applies_selected_sauce():
    state = _new_blank_match(manual=True)
    pid = state.current_player
    foe = 1 - pid
    spaghetti = _force_minion(state, pid, card_id="spaghetti", ready=True)
    hp_before = state.players[foe].hero_health

    ok, msg = engine.attack(state, pid, spaghetti.instance_id, f"hero:{foe}")
    assert ok, msg
    assert state.players[foe].hero_health == hp_before - spaghetti.attack
    assert state.pending_choice is not None
    assert state.pending_choice["kind"] == "choose_one_effect"

    # Escolhe Molho Branco: Escudo Divino.
    ok, msg = engine.resolve_choice(state, pid, state.pending_choice["choice_id"], {"index": 1})
    assert ok, msg
    assert spaghetti.divine_shield is True
    assert "DIVINE_SHIELD" in spaghetti.tags


def test_mario_on_draw_opens_choice_and_can_draw_revealed_card():
    state = _new_blank_match(manual=True)
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["mario", "pizza", "vini_zumbi"]
    p.hand.clear()

    effects.draw_card(state, p, 1)

    assert state.pending_choice is not None
    assert state.pending_choice["kind"] == "mario_reveal_top_choose_draw"
    ok, msg = engine.resolve_choice(state, pid, state.pending_choice["choice_id"],
                                    {"choose_revealed": True})
    assert ok, msg
    assert [c.card_id for c in p.hand] == ["pizza"]
    assert p.deck[0] == "vini_zumbi"


def test_obansug_gains_health_it_steals():
    state = _new_blank_match(manual=False)
    pid = state.current_player
    foe = 1 - pid
    obansug = _force_minion(state, pid, card_id="obansug")
    target = _force_minion(state, foe, card_id="pizza", health=5)

    effects.resolve_effect(
        state,
        get_card("obansug")["effects"][0],
        pid,
        obansug,
        {"chosen_target": target.instance_id},
    )

    assert target.health == 2
    assert obansug.health == 4
    assert obansug.max_health == 4


def test_lamboia_reorder_uses_explicit_manual_order():
    state = _new_blank_match(manual=True)
    pid = state.current_player
    p = state.players[pid]
    p.deck = ["pizza", "vini_zumbi", "gusneba", "stonks"]

    effects.resolve_effect(
        state,
        {"action": "REORDER_TOP_CARDS", "amount": 3, "target": {"mode": "SELF_DECK"}},
        pid,
        None,
        {},
    )

    assert state.pending_choice is not None
    ok, msg = engine.resolve_choice(state, pid, state.pending_choice["choice_id"],
                                    {"order": [2, 0, 1]})
    assert ok, msg
    assert p.deck[:4] == ["gusneba", "pizza", "vini_zumbi", "stonks"]


def test_ramoninho_has_three_free_total_uses_with_remaining_counter():
    state = _new_blank_match(manual=False)
    pid = state.current_player
    foe = 1 - pid
    ramon = _force_minion(state, pid, card_id="ramoninho_mestre_da_nerf")
    enemy = _force_minion(state, foe, card_id="gusneba", health=20)
    state.players[pid].mana = 0

    for expected_remaining in [2, 1, 0]:
        ok, msg = engine.activate_ability(state, pid, ramon.instance_id,
                                          chosen_target=enemy.instance_id)
        assert ok, msg
        assert ramon.ability_uses_remaining["0"] == expected_remaining

    ok, msg = engine.activate_ability(state, pid, ramon.instance_id,
                                      chosen_target=enemy.instance_id)
    assert not ok
    assert "sem usos" in msg
    assert state.players[pid].mana == 0


def test_dormant_minion_not_valid_target_and_wakes_after_two_owner_turns():
    state = _new_blank_match(manual=False)
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    ch = _add_hand(state, pid, "vini_3_anos_cansado")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    dormant = p.board[-1]

    assert "DORMANT" in dormant.tags
    assert dormant.immune
    assert not dormant.has_tag("TAUNT")
    assert not dormant.can_attack()

    # Não pode ser alvo escolhido nem atacado.
    assert not engine.list_legal_attack_targets(state, foe, dormant.instance_id)
    target_desc = {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}
    assert not __import__("game.targeting", fromlist=["has_valid_chosen_target"]).has_valid_chosen_target(
        state, target_desc, foe
    )

    engine.end_turn(state, pid)
    engine.end_turn(state, foe)
    assert "DORMANT" in dormant.tags
    engine.end_turn(state, pid)
    engine.end_turn(state, foe)
    assert "DORMANT" not in dormant.tags
    assert dormant.summoning_sick is True


def test_vic_assada_buffs_only_other_food_minions():
    state = _new_blank_match(manual=False)
    pid = state.current_player
    vic = _force_minion(state, pid, card_id="vic_assada", attack=5, health=4)
    food = _force_minion(state, pid, card_id="pizza", attack=0, health=2)
    non_food = _force_minion(state, pid, card_id="vini_zumbi", attack=2, health=3)

    engine.apply_continuous_effects(state)

    assert food.attack == 1
    assert food.health == 3
    assert non_food.attack == 2
    assert non_food.health == 3
    assert vic.attack == 5
    assert vic.health == 4
