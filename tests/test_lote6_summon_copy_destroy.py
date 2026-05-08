"""Lote 6 — invocação, cópias, destruição e auras simples."""
from __future__ import annotations

from game import engine, effects
from game.cards import get_card
from game.state import Minion, CardInHand, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"] * 30, "B", ["vini_zumbi"] * 30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="test", name="Test", attack=2, health=2,
                  tags=None, tribes=None, effects_list=None, ready=True):
    card = get_card(card_id) or {}
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=name if name != "Test" else card.get("name", name),
        attack=attack,
        health=health,
        max_health=health,
        tags=list(tags if tags is not None else (card.get("tags") or [])),
        tribes=list(tribes if tribes is not None else (card.get("tribes") or [])),
        effects=list(effects_list if effects_list is not None else (card.get("effects") or [])),
        owner=pid,
        summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in (tags or []),
    )
    state.players[pid].board.append(m)
    return m


def test_pizzaiolo_invoca_pizza_ao_morrer():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    pizzaiolo_card = get_card("pizzaiolo")
    pizzaiolo = _force_minion(
        state, pid, card_id="pizzaiolo", attack=pizzaiolo_card["attack"],
        health=1, tags=pizzaiolo_card["tags"], tribes=pizzaiolo_card["tribes"],
        effects_list=pizzaiolo_card["effects"],
    )
    before = len(p.board)
    pizzaiolo.health = 0
    engine.cleanup(state)
    assert len(p.board) == before  # Pizzaiolo saiu, Pizza entrou
    assert any(m.card_id == "pizza" for m in p.board)


def test_kiwi_copia_nao_mantem_deathrattle_infinito():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    kiwi_card = get_card("kiwi")
    kiwi = _force_minion(
        state, pid, card_id="kiwi", attack=2, health=2,
        tags=kiwi_card["tags"], tribes=kiwi_card["tribes"], effects_list=kiwi_card["effects"],
    )
    kiwi.health = 0
    engine.cleanup(state)
    copies = [m for m in p.board if m.card_id == "kiwi"]
    assert len(copies) == 1
    copy = copies[0]
    assert "DEATHRATTLE" not in copy.tags
    assert not any(e.get("trigger") == "ON_DEATH" for e in copy.effects)

    copy.health = 0
    engine.cleanup(state)
    assert not any(m.card_id == "kiwi" for m in p.board)


def test_lamboinha_destroi_comida_e_ganha_atributos_e_rapidez():
    state = _new_blank_match()
    pid = state.current_player
    lamboinha = _force_minion(state, pid, card_id="lamboinha_ma_cozinheiro", attack=3, health=2,
                              tags=["BATTLECRY"], tribes=[])
    comida = _force_minion(state, pid, card_id="peixe", attack=1, health=3,
                           tags=[], tribes=["COMIDA", "FERA"])
    eff = get_card("lamboinha_ma_cozinheiro")["effects"][0]
    effects.resolve_effect(state, eff, pid, lamboinha, {"chosen_target": comida.instance_id})
    assert lamboinha.attack == 4
    assert lamboinha.health == 5
    assert lamboinha.max_health == 5
    assert "RUSH" in lamboinha.tags
    assert comida.health <= 0
    engine.cleanup(state)
    assert state.find_minion(comida.instance_id) is None


def test_gusnabo_de_negocios_troca_mao_por_cartas_do_deck():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand = [CardInHand(instance_id=gen_id("h_"), card_id="camarao"),
              CardInHand(instance_id=gen_id("h_"), card_id="peixe")]
    p.deck = ["pizza", "vini_zumbi", "banana", "donut"]
    eff = get_card("gusnabo_de_negocios")["effects"][0]
    effects.resolve_effect(state, eff, pid, None, {})
    assert len(p.hand) == 2
    assert {c.card_id for c in p.hand}.isdisjoint({"camarao", "peixe"})
    # As cartas antigas voltaram para o deck antes do draw/shuffle.
    assert "camarao" in p.deck or "peixe" in p.deck


def test_niurau_recebe_rapidez_com_dois_outros_lacaios():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = p.max_mana = 10
    _force_minion(state, pid)
    _force_minion(state, pid)
    ch = CardInHand(instance_id=gen_id("h_"), card_id="niurau")
    p.hand.append(ch)
    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    played = next(m for m in p.board if m.card_id == "niurau")
    assert "RUSH" in played.tags


def test_niurau_nao_recebe_rapidez_com_um_outro_lacaio():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = p.max_mana = 10
    _force_minion(state, pid)
    ch = CardInHand(instance_id=gen_id("h_"), card_id="niurau")
    p.hand.append(ch)
    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    played = next(m for m in p.board if m.card_id == "niurau")
    assert "RUSH" not in played.tags


def test_memes_buffa_apenas_adjacentes():
    state = _new_blank_match()
    pid = state.current_player
    left = _force_minion(state, pid, attack=2, health=2, tags=[], effects_list=[])
    memes_card = get_card("memes")
    memes = _force_minion(state, pid, card_id="memes", attack=2, health=2,
                          tags=memes_card["tags"], tribes=memes_card["tribes"],
                          effects_list=memes_card["effects"])
    right = _force_minion(state, pid, attack=3, health=2, tags=[], effects_list=[])
    far = _force_minion(state, pid, attack=4, health=2, tags=[], effects_list=[])
    engine.cleanup(state)
    assert left.attack == 3
    assert right.attack == 4
    assert far.attack == 4
    # Recalcular não empilha.
    engine.cleanup(state)
    assert left.attack == 3
    assert right.attack == 4


def test_vini_ilusorio_invoca_copia_um_um_do_alvo():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    target = _force_minion(state, foe, card_id="pizza", attack=8, health=8)
    eff = get_card("vini_ilusorio")["effects"][0]
    before = len(state.players[pid].board)
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": target.instance_id})
    assert len(state.players[pid].board) == before + 1
    copy = state.players[pid].board[-1]
    assert copy.card_id == "pizza"
    assert copy.attack == 1
    assert copy.health == 1


def test_saudades_devolve_lacaio_com_custo_modificado():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    target = _force_minion(state, foe, card_id="pizza", attack=5, health=5)
    eff = get_card("saudades")["effects"][0]
    before_hand = len(state.players[foe].hand)
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": target.instance_id})
    assert state.find_minion(target.instance_id) is None
    assert len(state.players[foe].hand) == before_hand + 1
    returned = state.players[foe].hand[-1]
    assert returned.card_id == "pizza"
    assert returned.cost_modifier == 3
