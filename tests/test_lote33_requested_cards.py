"""Lote 33 - cartas solicitadas adicionadas ao jogo."""
from __future__ import annotations

from game import engine
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


def _play_from_hand(state, pid, card_id, **kwargs):
    p = state.players[pid]
    p.mana = 10
    ch = _add_hand(state, pid, card_id)
    ok, msg = engine.play_card(state, pid, ch.instance_id, **kwargs)
    assert ok, msg
    return ch


def test_carvalho_replaces_deck_through_ten_manual_discovers():
    state = _new_game(seed=2, manual_choices=True)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck = ["pizza", "vini_zumbi", "soldado_italiano"]
    p.mana = 10

    card = _add_hand(state, pid, "carvalho")
    ok, msg = engine.play_card(state, pid, card.instance_id)
    assert ok, msg
    assert p.deck == []
    assert state.pending_choice["kind"] == "build_replacement_deck"

    picks = []
    for _ in range(10):
        choice = state.pending_choice
        picked = choice["options"][0]["card_id"]
        picks.append(picked)
        ok, msg = engine.resolve_choice(state, pid, choice["choice_id"], {"index": 0})
        assert ok, msg

    assert state.pending_choice is None
    assert sorted(p.deck) == sorted(picks)
    assert len(p.deck) == 10


def test_dede_santana_reveals_and_moves_four_opponent_top_cards_to_your_deck_top():
    state = _new_game(seed=3)
    pid = state.current_player
    foe = 1 - pid
    state.players[pid].hand.clear()
    state.players[pid].deck = ["vini_zumbi"]
    state.players[foe].deck = ["pizza", "soldado_italiano", "vini_zumbi", "fusca_tunado", "maca"]

    _play_from_hand(state, pid, "dede_santana")

    assert state.players[pid].deck[:4] == ["pizza", "soldado_italiano", "vini_zumbi", "fusca_tunado"]
    assert state.players[foe].deck == ["maca"]
    event = next(ev for ev in state.event_log if ev.get("type") == "reveal_and_steal_top_deck_cards")
    assert event["card_ids"] == ["pizza", "soldado_italiano", "vini_zumbi", "fusca_tunado"]
    assert event["cards"] == [
        {"owner": foe, "card_id": "pizza"},
        {"owner": foe, "card_id": "soldado_italiano"},
        {"owner": foe, "card_id": "vini_zumbi"},
        {"owner": foe, "card_id": "fusca_tunado"},
    ]


def test_sub_buffs_minion_marks_portrait_and_returns_to_deck_top_on_death():
    state = _new_game(seed=4)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    target = _force_minion(state, pid, card_id="vini_zumbi", attack=1, health=2)

    _play_from_hand(state, pid, "sub", chosen_target=target.instance_id)

    assert target.attack == 8
    assert target.health == 11
    assert any(pm.get("kind") == "return_spell_to_deck_on_minion_death" for pm in state.pending_modifiers)
    view = state.to_dict(pid)["you"]["board"][0]
    assert view["linked_card"]["card_id"] == "sub"

    target.health = 0
    engine.cleanup(state)
    assert p.deck[0] == "sub"


def test_capitao_graga_ability_has_two_uses_damage_and_gains_divine_shield():
    state = _new_game(seed=5)
    pid = state.current_player
    foe = 1 - pid
    graga = _force_minion(state, pid, card_id="capitao_graga")
    target = _force_minion(state, foe, card_id="soldado_italiano", health=10)

    ok, msg = engine.activate_ability(state, pid, graga.instance_id, chosen_target=target.instance_id)
    assert ok, msg
    assert target.health == 7
    assert graga.divine_shield is True
    assert graga.ability_uses_remaining["0"] == 1

    ok, msg = engine.activate_ability(state, pid, graga.instance_id, chosen_target=target.instance_id)
    assert ok, msg
    assert graga.ability_uses_remaining["0"] == 0
    ok, _ = engine.activate_ability(state, pid, graga.instance_id, chosen_target=target.instance_id)
    assert not ok


def test_muralha_reduces_chinese_minions_in_hand_while_alive():
    state = _new_game(seed=6)
    pid = state.current_player
    p = state.players[pid]
    wall = _force_minion(state, pid, card_id="a_grande_muralha_da_china")
    chinese = CardInHand(instance_id=gen_id("h_"), card_id="a_grande_muralha_da_china")
    p.hand.append(chinese)

    assert engine.compute_dynamic_cost(state, p, chinese, get_card(chinese.card_id)) == 7
    p.board.remove(wall)
    assert engine.compute_dynamic_cost(state, p, chinese, get_card(chinese.card_id)) == 10


def test_dinomancia_revives_first_dead_as_beast_9_9_with_charge():
    state = _new_game(seed=7)
    pid = state.current_player
    state.graveyard.append({"owner": pid, "card_id": "soldado_italiano", "name": "Soldado Italiano"})
    state.players[pid].hand.clear()

    _play_from_hand(state, pid, "dinomancia")

    revived = state.players[pid].board[-1]
    assert revived.card_id == "soldado_italiano"
    assert (revived.attack, revived.health, revived.max_health) == (9, 9, 9)
    assert revived.tribes == ["FERA"]
    assert "CHARGE" in revived.tags


def test_lagd_recruits_five_minions_as_2_2_divine_shield_rush_without_battlecry():
    state = _new_game(seed=8)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck = ["dede_santana", "pizza", "maldicao_do_vini_sombrio", "soldado_italiano", "fusca_tunado", "carvalho"]

    _play_from_hand(state, pid, "lagd")

    recruited = p.board
    assert len(recruited) == 5
    assert [m.card_id for m in recruited] == ["dede_santana", "pizza", "soldado_italiano", "fusca_tunado", "carvalho"]
    assert all((m.attack, m.health, m.max_health) == (2, 2, 2) for m in recruited)
    assert all(m.divine_shield and "RUSH" in m.tags for m in recruited)
    assert "maldicao_do_vini_sombrio" in p.deck


def test_maldicao_destroys_minions_discards_both_hands_and_draws_five():
    state = _new_game(seed=9)
    pid = state.current_player
    foe = 1 - pid
    state.players[pid].hand.clear()
    state.players[foe].hand.clear()
    _force_minion(state, pid, card_id="soldado_italiano")
    _force_minion(state, foe, card_id="vini_zumbi")
    state.players[pid].deck = ["vini_zumbi"] * 5
    state.players[foe].deck = ["pizza"] * 5
    _add_hand(state, foe, "pizza")

    _play_from_hand(state, pid, "maldicao_do_vini_sombrio")

    assert state.players[pid].board == []
    assert state.players[foe].board == []
    assert len(state.players[pid].hand) == 5
    assert len(state.players[foe].hand) == 5


def test_mussolini_summons_two_soldiers_and_buffs_other_italians():
    state = _new_game(seed=10)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()

    _play_from_hand(state, pid, "mussolini")
    engine.apply_continuous_effects(state)

    mussolini = next(m for m in p.board if m.card_id == "mussolini")
    soldiers = [m for m in p.board if m.card_id == "soldado_italiano"]
    assert len(soldiers) == 2
    assert (mussolini.attack, mussolini.health) == (6, 2)
    assert all((s.attack, s.health, s.max_health) == (2, 6, 6) for s in soldiers)


def test_mussovini_summons_six_vinis_3_anos_and_makes_vinis_italian():
    state = _new_game(seed=40, manual_choices=False)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()

    _play_from_hand(state, pid, "mussovini")
    engine.apply_continuous_effects(state)

    assert len(p.board) == 7
    assert p.board[0].card_id == "mussovini"
    assert [m.card_id for m in p.board[1:]] == ["vini_3_anos"] * 6
    assert all("ITALIA" in m.tribes for m in p.board if m.card_id.startswith("vini"))


def test_rei_arvore_corrompido_replaces_all_minions_with_trunks_without_deaths():
    state = _new_game(seed=41, manual_choices=False)
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.hand.clear()
    _force_minion(state, pid, card_id="rei_arvore")
    _force_minion(state, foe, card_id="vinish")

    _play_from_hand(state, pid, "rei_arvore_corrompido")

    assert [m.card_id for m in state.players[pid].board] == ["tronco", "rei_arvore_corrompido"]
    assert [m.card_id for m in state.players[foe].board] == ["tronco"]
    assert state.graveyard == []
    assert not any(ev.get("type") == "death" for ev in state.event_log)


def test_rei_arvore_deathrattle_summons_trunk_that_transforms_on_owner_start():
    state = _new_game(seed=42, manual_choices=False)
    pid = state.current_player
    tree = _force_minion(state, pid, card_id="rei_arvore", health=1)

    tree.health = 0
    engine.cleanup(state)

    assert [m.card_id for m in state.players[pid].board] == ["tronco"]
    assert any(pm.get("kind") == "transform_trunk_into_rei_arvore_start_turn" for pm in state.pending_modifiers)
    engine.end_turn(state, pid)
    engine.end_turn(state, 1 - pid)
    assert [m.card_id for m in state.players[pid].board] == ["rei_arvore"]


def test_veneza_halves_vehicle_stats_and_prevents_attacking_while_alive():
    state = _new_game(seed=43, manual_choices=False)
    pid = state.current_player
    vehicle = _force_minion(state, pid, card_id="tres_fuscas", ready=True)
    veneza = _force_minion(state, 1 - pid, card_id="veneza")

    engine.apply_continuous_effects(state)

    assert (vehicle.attack, vehicle.health, vehicle.max_health) == (6, 6, 6)
    assert not vehicle.can_attack()
    state.players[1 - pid].board.remove(veneza)
    engine.apply_continuous_effects(state)
    assert (vehicle.attack, vehicle.health, vehicle.max_health) == (12, 13, 13)


def test_sastv_draws_three_and_manual_choice_plays_one_for_free():
    state = _new_game(seed=44, manual_choices=True)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck = ["vini", "tres_fuscas", "vinish"]
    p.mana = 10
    ch = _add_hand(state, pid, "sastv")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    choice = state.pending_choice
    assert choice["kind"] == "choose_drawn_card_to_play_free"
    ok, msg = engine.resolve_choice(state, pid, choice["choice_id"], {"index": 1})
    assert ok, msg

    assert [m.card_id for m in p.board] == ["tres_fuscas"]
    assert sorted(c.card_id for c in p.hand) == ["vini", "vinish"]


def test_sastv_allows_free_play_choice_when_only_two_cards_are_drawn():
    state = _new_game(seed=48, manual_choices=True)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck = ["vini", "vinish"]
    p.mana = 10
    ch = _add_hand(state, pid, "sastv")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    choice = state.pending_choice

    assert choice["kind"] == "choose_drawn_card_to_play_free"
    assert [opt["card_id"] for opt in choice["cards"]] == ["vini", "vinish"]
    ok, msg = engine.resolve_choice(state, pid, choice["choice_id"], {"index": 0})
    assert ok, msg
    assert [m.card_id for m in p.board] == ["vini"]
    assert [c.card_id for c in p.hand] == ["vinish"]


def test_sastv_does_nothing_when_no_cards_are_drawn():
    state = _new_game(seed=49, manual_choices=True)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck = []
    p.mana = 10
    ch = _add_hand(state, pid, "sastv")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    assert state.pending_choice is None
    assert p.hand == []
    assert p.board == []
    assert any(ev.get("type") == "free_play_choice_skipped" and ev.get("reason") == "no_drawn_cards"
               for ev in state.event_log)


def test_vinish_deathrattle_wins_when_owner_has_no_cards_anywhere():
    state = _new_game(seed=45, manual_choices=False)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck.clear()
    vinish = _force_minion(state, pid, card_id="vinish", health=1)

    vinish.health = 0
    engine.cleanup(state)

    assert state.winner == pid
    assert state.phase == "ENDED"


def test_vini_sombrio_summons_taunt_1_1_deathrattle_copies_from_deck_and_graveyard():
    state = _new_game(seed=46, manual_choices=False)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.deck = ["rei_arvore", "vini", "vinish"]
    state.graveyard.append({"owner": pid, "card_id": "rei_arvore", "name": "Rei Árvore"})

    _play_from_hand(state, pid, "vinisombrio")

    copies = p.board[1:]
    assert [m.card_id for m in copies] == ["rei_arvore", "vinish", "rei_arvore"]
    assert all((m.attack, m.health, m.max_health) == (1, 1, 1) for m in copies)
    assert all("TAUNT" in m.tags for m in copies)


def test_reflexo_costs_chosen_minion_and_summons_exact_current_copy():
    state = _new_game(seed=47, manual_choices=False)
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    target = _force_minion(state, 1 - pid, card_id="tres_fuscas", attack=9, health=5)
    target.max_health = 7
    target.divine_shield = False
    p.mana = 10
    ch = _add_hand(state, pid, "reflexo")

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=target.instance_id)
    assert ok, msg

    assert p.mana == 0
    copy = p.board[-1]
    assert copy.card_id == "tres_fuscas"
    assert (copy.attack, copy.health, copy.max_health) == (9, 5, 7)
    assert copy.tags == target.tags
    assert copy.tribes == target.tribes
