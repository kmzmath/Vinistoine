from __future__ import annotations

from game import effects, engine, targeting
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id


def _new_game(seed: int = 1, manual_choices: bool = True):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["pizza"] * 30,
                            seed=seed, manual_choices=manual_choices)
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
    tags = list(card.get("tags") or [])
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=card.get("name", card_id),
        attack=atk,
        health=hp,
        max_health=hp,
        tags=tags,
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=pid,
        summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in tags,
    )
    state.players[pid].board.append(m)
    return m


def _add_hand(state, pid, card_id):
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def test_spaghetti_only_opens_sauce_choice_after_attacking_opposing_hero():
    state = _new_game(manual_choices=True)
    pid = state.current_player
    foe = 1 - pid
    spaghetti = _force_minion(state, pid, card_id="spaghetti", ready=True)
    enemy = _force_minion(state, foe, card_id="pizza", attack=0, health=5)

    ok, msg = engine.attack(state, pid, spaghetti.instance_id, enemy.instance_id)
    assert ok, msg
    assert state.pending_choice is None

    spaghetti.attacks_this_turn = 0
    ok, msg = engine.attack(state, pid, spaghetti.instance_id, f"hero:{foe}")
    assert ok, msg
    assert state.pending_choice is not None
    assert state.pending_choice["kind"] == "choose_one_effect"


def test_ninjagui_cozinheiro_requires_one_target_for_heal_and_taunt():
    card = get_card("ninjagui_3_anos_cozinheiro")
    targets = targeting.chosen_targets_for_card(card)
    assert len(targets) == 1

    state = _new_game()
    pid = state.current_player
    ally = _force_minion(state, pid, card_id="pizza", attack=1, health=1)
    ally.max_health = 5
    player = state.players[pid]
    player.hand.clear()
    player.mana = 10
    ch = _add_hand(state, pid, "ninjagui_3_anos_cozinheiro")

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=ally.instance_id)
    assert ok, msg
    assert ally.health == 5
    assert "TAUNT" in ally.tags


def test_vini_egoista_increases_friendly_minion_hand_costs():
    state = _new_game()
    pid = state.current_player
    _force_minion(state, pid, card_id="vini_egoista")
    ch = _add_hand(state, pid, "pizza")

    cost = engine.compute_dynamic_cost(state, state.players[pid], ch, get_card("pizza"))

    assert cost == (get_card("pizza")["cost"] + 1)


def test_lost_coin_bonus_draw_does_not_trigger_another_lost_coin_immediately():
    state = _new_game()
    pid = state.current_player
    player = state.players[pid]
    player.hand.clear()
    player.deck = ["moeda_perdida", "moeda_perdida", "pizza"]

    effects.draw_card(state, player, 1)

    assert [c.card_id for c in player.hand] == ["coin", "moeda_perdida", "pizza"]
    assert player.deck == []


def test_boludo_aura_kills_low_health_minions_and_brazilian_gets_minus_two_total():
    state = _new_game()
    pid = state.current_player
    foe = 1 - pid
    boludo = _force_minion(state, pid, card_id="boludo")
    normal = _force_minion(state, foe, card_id="pizza", attack=1, health=1)
    brazilian = _force_minion(state, foe, card_id="baiano", attack=3, health=4)

    engine.cleanup(state)

    assert state.find_minion(normal.instance_id) is None
    found_brazilian = state.find_minion(brazilian.instance_id)
    assert found_brazilian is not None
    assert found_brazilian[0].attack == 1
    assert found_brazilian[0].max_health == 2
    assert state.find_minion(boludo.instance_id) is not None


def test_damage_source_reflect_hits_stealthed_nando():
    state = _new_game()
    pid = state.current_player
    foe = 1 - pid
    tronco = _force_minion(state, pid, card_id="tronco", health=4)
    nando = _force_minion(state, foe, card_id="nando", attack=5, health=5)

    effects.damage_character(state, tronco, 2, source_owner=foe, source_minion=nando)

    assert nando.health == 3


def test_aulao_played_third_card_counts_as_played_for_minion_triggers():
    state = _new_game()
    pid = state.current_player
    egoista = _force_minion(state, pid, card_id="vini_egoista", attack=2, health=3)
    player = state.players[pid]
    player.hand.clear()
    player.deck = ["pizza", "pizza", "pizza"]
    player.mana = 10
    ch = _add_hand(state, pid, "aulao")

    ok, msg = engine.play_card(state, pid, ch.instance_id)

    assert ok, msg
    assert [m.card_id for m in player.board][-1] == "pizza"
    assert egoista.attack == 3
    assert egoista.max_health == 5


def test_el_luca_view_links_the_restricted_attacker_portrait():
    state = _new_game()
    pid = state.current_player
    foe = 1 - pid
    luca = _force_minion(state, pid, card_id="el_luca")
    attacker = _force_minion(state, foe, card_id="pizza")

    effects.resolve_effect(state, luca.effects[0], pid, luca, {"chosen_target": attacker.instance_id})

    view = state.to_dict(pid)["you"]["board"]
    luca_view = next(m for m in view if m["instance_id"] == luca.instance_id)
    assert luca_view["linked_minion"]["instance_id"] == attacker.instance_id
    assert luca_view["linked_minion"]["card_id"] == "pizza"
