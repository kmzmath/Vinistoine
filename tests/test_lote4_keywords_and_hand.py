from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import Minion, CardInHand, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"]*30, "B", ["vini_zumbi"]*30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="test", name="Test", attack=2, health=2,
                  tags=None, effects_list=None, ready=True):
    m = Minion(
        instance_id=gen_id("m_"), card_id=card_id, name=name,
        attack=attack, health=health, max_health=health,
        tags=list(tags or []), effects=list(effects_list or []),
        owner=pid, summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in list(tags or []),
    )
    state.players[pid].board.append(m)
    return m


def test_echo_temporario_pode_ser_jogado_varias_vezes_no_mesmo_turno():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10; p.max_mana = 10

    ch = CardInHand(instance_id=gen_id("h_"), card_id="mundo_dos_negocios")
    p.hand = [ch]

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    temp = next((c for c in p.hand if c.card_id == "mundo_dos_negocios" and c.echo_temporary), None)
    assert temp is not None

    ok, msg = engine.play_card(state, pid, temp.instance_id)
    assert ok, msg
    temp2 = next((c for c in p.hand if c.card_id == "mundo_dos_negocios" and c.echo_temporary), None)
    assert temp2 is not None
    assert temp2.instance_id != temp.instance_id

    engine.end_turn(state, pid)
    assert not any(c.echo_temporary for c in p.hand)


def test_resistant_reduz_dano_antes_de_quebrar_escudo_divino():
    state = _new_blank_match()
    pid = state.current_player
    tata = _force_minion(state, pid, card_id="tata", name="Tatá", attack=1, health=3,
                         tags=["RESISTANT", "DIVINE_SHIELD"])

    dealt = effects.damage_character(state, tata, 1, source_owner=1-pid)
    assert dealt == 0
    assert tata.health == 3
    assert tata.divine_shield is True

    dealt = effects.damage_character(state, tata, 2, source_owner=1-pid)
    assert dealt == 0  # 2 -> 1, escudo absorve
    assert tata.health == 3
    assert tata.divine_shield is False


def test_pizza_custa_menos_com_comida_aliada_em_campo():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    _force_minion(state, pid, card_id="peixe", name="Peixe", attack=1, health=3,
                  tags=[], effects_list=[], ready=True).tribes = ["COMIDA"]
    ch = CardInHand(instance_id=gen_id("h_"), card_id="pizza")
    p.hand.append(ch)
    assert engine.compute_dynamic_cost(state, p, ch, get_card("pizza")) == 1


def test_after_you_play_card_dispara_para_lacaio_e_spell():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10; p.max_mana = 10
    combo_card = get_card("gusnabinho_mestre_do_combo")
    combo = _force_minion(state, pid, card_id="gusnabinho_mestre_do_combo",
                          name="Gusnabinho", attack=1, health=1,
                          effects_list=combo_card.get("effects", []))

    minion = CardInHand(instance_id=gen_id("h_"), card_id="peixe")
    spell = CardInHand(instance_id=gen_id("h_"), card_id="bencao_do_vini_da_luz")
    p.hand.extend([minion, spell])

    ok, msg = engine.play_card(state, pid, minion.instance_id)
    assert ok, msg
    assert combo.attack == 2 and combo.health == 2

    ok, msg = engine.play_card(state, pid, spell.instance_id)
    assert ok, msg
    assert combo.attack == 3 and combo.health == 3


def test_tag_ate_fim_do_turno_e_removida():
    state = _new_blank_match()
    pid = state.current_player
    ally = _force_minion(state, pid, attack=1, health=1)
    eff = {"action": "ADD_TAG", "tag": "STEALTH", "duration": "UNTIL_END_OF_TURN",
           "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]}}
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": ally.instance_id})
    assert "STEALTH" in ally.tags
    engine.end_turn(state, pid)
    assert "STEALTH" not in ally.tags


def test_while_damaged_buff_nao_empilha_e_remove_ao_curar():
    state = _new_blank_match()
    pid = state.current_player
    card = get_card("edu_putasso")
    edu = _force_minion(state, pid, card_id="edu_putasso", name="Edu", attack=2, health=3,
                        effects_list=card.get("effects", []))
    effects.damage_character(state, edu, 1, source_owner=1-pid)
    engine.cleanup(state)
    assert edu.attack == 5
    engine.cleanup(state)
    assert edu.attack == 5
    effects.heal_character(state, edu, 1)
    engine.cleanup(state)
    assert edu.attack == 2
