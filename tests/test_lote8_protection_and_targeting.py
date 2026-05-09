"""Lote 8 - proteção, targeting e prevenção de ataque/dano."""
from __future__ import annotations

import game.cards as _cards_mod
from game import engine, effects
from game.state import Minion, CardInHand, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    state.manual_choices = False
    return state


def _force_minion(state, pid, *, card_id="test", name="Test", attack=2, health=2,
                  tags=None, effects_list=None, ready=True):
    m = Minion(
        instance_id=gen_id("m_"), card_id=card_id, name=name,
        attack=attack, health=health, max_health=health,
        tags=list(tags or []), effects=list(effects_list or []),
        owner=pid, summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


def _add_fake_spell_to_hand(state, pid, card_id="fake_damage_spell", *, valid=None):
    valid = valid or ["ANY_MINION"]
    _cards_mod._CARDS_BY_ID[card_id] = {
        "id": card_id,
        "name": "Fake Spell",
        "type": "SPELL",
        "cost": 0,
        "tags": [],
        "tribes": [],
        "effects": [{
            "trigger": "ON_PLAY",
            "action": "DAMAGE",
            "amount": 1,
            "target": {"mode": "CHOSEN", "valid": valid},
        }],
    }
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


def test_cannot_be_targeted_by_spells_blocks_enemy_spell_targeting():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    target = _force_minion(state, foe, tags=["SPELL_TARGET_IMMUNITY"])
    ch = _add_fake_spell_to_hand(state, pid)
    state.players[pid].mana = 10

    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=target.instance_id)
    assert not ok
    assert "alvo" in msg.lower()
    assert target.health == target.max_health


def test_enemy_spell_target_immunity_blocks_enemy_but_allows_owner_spell():
    state = _new_blank_match()
    pid = state.current_player
    own = _force_minion(state, pid, tags=["FRIENDLY_SPELL_TARGET_ONLY"])

    # O próprio dono pode alvejar.
    ch = _add_fake_spell_to_hand(state, pid, "own_spell", valid=["FRIENDLY_MINION"])
    state.players[pid].mana = 10
    ok, msg = engine.play_card(state, pid, ch.instance_id, chosen_target=own.instance_id)
    assert ok, msg
    assert own.health == own.max_health - 1

    # O oponente não pode alvejar o mesmo lacaio com feitiço.
    state.current_player = 1 - pid
    foe = state.current_player
    ch2 = _add_fake_spell_to_hand(state, foe, "enemy_spell", valid=["ENEMY_MINION"])
    state.players[foe].mana = 10
    hp_before = own.health
    ok, msg = engine.play_card(state, foe, ch2.instance_id, chosen_target=own.instance_id)
    assert not ok
    assert own.health == hp_before


def test_grant_temporary_spell_target_immunity_for_hero_and_minion_until_enemy_turn_end():
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    ally = _force_minion(state, pid)

    eff = {
        "action": "GRANT_TEMPORARY_SPELL_TARGET_IMMUNITY",
        "duration": "UNTIL_NEXT_TURN_END",
        "target": {"mode": "FRIENDLY_CHARACTERS"},
    }
    effects.resolve_effect(state, eff, pid, None, {})
    assert ally.has_tag("ENEMY_SPELL_TARGET_IMMUNITY")
    assert me.hero_spell_target_immune is True

    engine.end_turn(state, pid)      # passa para o oponente: ainda deve valer
    assert ally.has_tag("ENEMY_SPELL_TARGET_IMMUNITY")
    assert me.hero_spell_target_immune is True

    engine.end_turn(state, state.current_player)  # fim do turno inimigo: expira
    assert not ally.has_tag("ENEMY_SPELL_TARGET_IMMUNITY")
    assert me.hero_spell_target_immune is False


def test_prevent_attack_against_self_blocks_specific_attacker():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    el_luca = _force_minion(state, pid, name="El Luca", attack=2, health=5)
    enemy = _force_minion(state, foe, attack=3, health=3, ready=True)

    eff = {
        "action": "PREVENT_ATTACK_AGAINST_SELF",
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, el_luca, {"chosen_target": enemy.instance_id})
    state.current_player = foe

    ok, msg = engine.attack(state, foe, enemy.instance_id, el_luca.instance_id)
    assert not ok
    assert "não pode" in msg.lower()


def test_prevent_damage_this_turn_makes_minion_deal_zero_damage():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    attacker = _force_minion(state, foe, attack=5, health=5, ready=True)
    defender = _force_minion(state, pid, attack=2, health=10)

    eff = {
        "action": "PREVENT_DAMAGE_THIS_TURN",
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": attacker.instance_id})
    state.current_player = foe

    ok, msg = engine.attack(state, foe, attacker.instance_id, defender.instance_id)
    assert ok, msg
    assert defender.health == 10  # atacante causou 0
    assert attacker.health == 3   # ainda recebeu a retaliação do defensor


def test_redirect_attack_to_self_receives_attack_instead_of_ally():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    attacker = _force_minion(state, pid, attack=3, health=5, ready=True)
    original = _force_minion(state, foe, name="Ally", attack=1, health=5)
    lucas = _force_minion(state, foe, name="Lucas", attack=1, health=6,
                          effects_list=[{"trigger": "ON_FRIENDLY_CHARACTER_ATTACKED",
                                         "action": "REDIRECT_ATTACK_TO_SELF",
                                         "target": {"mode": "SELF"}}])

    ok, msg = engine.attack(state, pid, attacker.instance_id, original.instance_id)
    assert ok, msg
    assert original.health == 5
    assert lucas.health == 3


def test_skip_next_attack_blocks_until_end_of_owner_turn():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, attack=3, health=3, ready=True)
    effects.resolve_effect(state, {"action": "SKIP_NEXT_ATTACK", "target": {"mode": "SELF"}},
                           pid, m, {})
    assert m.skip_next_attack is True
    assert not m.can_attack()

    engine.end_turn(state, pid)
    engine.end_turn(state, state.current_player)
    assert m.skip_next_attack is False
    assert m.can_attack()
