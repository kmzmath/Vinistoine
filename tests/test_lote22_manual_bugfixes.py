"""Lote 22 — correções reportadas em teste manual."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import CardInHand, Minion, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
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


def test_hello_world_adds_cost_one_copy_to_middle_of_deck():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    hello = _add_hand(state, pid, "hello_world")
    p.deck = ["stonks", "troca_justa", "vini_zumbi", "pizza"]

    ok, msg = engine.play_card(state, pid, hello.instance_id)
    assert ok, msg

    # Comprou os dois feitiços e inseriu uma cópia modificada no deck.
    assert sorted(c.card_id for c in p.hand) == ["stonks", "troca_justa"]
    assert len(p.deck) == 3
    marker = next(e for e in p.deck if e != "vini_zumbi" and e != "pizza")
    mod = state.deck_card_modifiers[marker]
    assert mod["card_id"] == "hello_world"
    assert mod["cost_override"] == 1


def test_gusneba_summons_one_taunt_copy_and_one_poisonous_copy():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    ch = _add_hand(state, pid, "gusneba")

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    gusnebas = [m for m in p.board if m.card_id == "gusneba"]
    assert len(gusnebas) == 3
    assert any("TAUNT" in m.tags for m in gusnebas if m is not p.board[0])
    assert any("POISONOUS" in m.tags for m in gusnebas if m is not p.board[0])


def test_ninjagui_3_anos_accepts_two_targets_in_order():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    ally = _force_minion(state, pid, card_id="pizza", health=5)
    enemy = _force_minion(state, foe, card_id="vini_zumbi", health=7)
    ch = _add_hand(state, pid, "ninjagui_3_anos")

    ok, msg = engine.play_card(state, pid, ch.instance_id,
                               chosen_targets=[ally.instance_id, enemy.instance_id])

    assert ok, msg
    assert ally.health == 4
    assert enemy.health == 4


def test_vinagra_buffs_and_skips_next_attack():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    target = _force_minion(state, pid, card_id="pizza", attack=1, health=2)
    ch = _add_hand(state, pid, "vinagra")

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=target.instance_id)
    assert ok, msg

    assert target.attack == 8
    assert target.health == 9
    assert target.max_health == 9
    assert target.skip_next_attack is True


def test_igleba_deathrattle_summons_three_rush_tokens_without_deathrattle():
    state = _new_blank_match()
    pid = state.current_player
    igleba = _force_minion(state, pid, card_id="igleba", attack=3, health=3)
    igleba.health = 0

    engine.cleanup(state)

    tokens = [m for m in state.players[pid].board if m.card_id == "igleba_token"]
    assert len(tokens) == 3
    assert all(m.attack == 1 and m.health == 1 for m in tokens)
    assert all("RUSH" in m.tags for m in tokens)
    assert all(m.effects == [] for m in tokens)


def test_mao_tse_tung_halves_board_hand_and_deck_minions():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    opp = state.players[foe]
    p.mana = 10
    p.hand.clear()
    mao = _add_hand(state, pid, "mao_tse_tung")
    hand_minion = _add_hand(state, pid, "gusneba")  # 2 atk -> 1
    opp_hand_minion = _add_hand(state, foe, "soldado_italiano")  # 1 atk -> 1
    board_enemy = _force_minion(state, foe, card_id="gusneba", attack=5, health=5)
    p.deck = ["gusneba", "stonks"]
    opp.deck = ["gusneba"]

    ok, msg = engine.play_card(state, pid, mao.instance_id)
    assert ok, msg

    assert board_enemy.attack == 3
    assert hand_minion.stat_modifier["attack"] == -1
    assert opp_hand_minion.stat_modifier["attack"] == 0

    deck_card_entry = p.deck[0]
    assert deck_card_entry in state.deck_card_modifiers
    assert state.deck_card_modifiers[deck_card_entry]["card_id"] == "gusneba"
    assert state.deck_card_modifiers[deck_card_entry]["stat_modifier"]["attack"] == -1


def test_nando_keeps_stealth_after_attacking():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    nando = _force_minion(state, pid, card_id="nando", attack=5, health=5, ready=True)
    enemy = _force_minion(state, foe, card_id="pizza", attack=0, health=10, ready=True)
    engine.apply_continuous_effects(state)

    assert "STEALTH" in nando.tags
    assert "PERMANENT_STEALTH" in nando.tags

    ok, msg = engine.attack(state, pid, nando.instance_id, enemy.instance_id)
    assert ok, msg
    assert "STEALTH" in nando.tags


def test_vini_flamejante_burns_opponent_at_start_of_opponent_turn():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    ch = _add_hand(state, pid, "vini_flamejante")
    before = state.players[foe].hero_health

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    engine.end_turn(state, pid)  # começo do turno do oponente
    assert state.players[foe].hero_health == before - 2


def test_cultista_flamejante_burns_minion_each_turn_start():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    enemy = _force_minion(state, foe, card_id="gusneba", health=20)
    ch = _add_hand(state, pid, "cultista_do_vini_flamejante")

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=enemy.instance_id)
    assert ok, msg

    engine.end_turn(state, pid)
    assert enemy.health == 15
    engine.end_turn(state, foe)
    assert enemy.health == 10


def test_perfeitinha_returns_target_and_freezes_original_adjacents():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    p = state.players[pid]
    p.mana = 10
    p.hand.clear()
    left = _force_minion(state, foe, card_id="vini_zumbi", health=3)
    middle = _force_minion(state, foe, card_id="pizza", health=3)
    right = _force_minion(state, foe, card_id="gusneba", health=3)
    ch = _add_hand(state, pid, "perfeitinha")

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=middle.instance_id)
    assert ok, msg

    assert state.find_minion(middle.instance_id) is None
    assert any(c.card_id == "pizza" for c in state.players[foe].hand)
    assert left.frozen is True
    assert right.frozen is True
