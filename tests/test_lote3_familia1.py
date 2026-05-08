"""Testes da Família 1 do Lote 3 — Manipulação de Deck."""
from __future__ import annotations
import pytest
import game.cards as _cards_mod
from game import engine, effects, targeting
from game.cards import get_card
from game.state import GameState, Minion, CardInHand, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"]*30, "B", ["vini_zumbi"]*30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="test", attack=2, health=2, tags=None, ready=True):
    m = Minion(
        instance_id=gen_id("m_"), card_id=card_id, name="Test",
        attack=attack, health=health, max_health=health,
        tags=list(tags or []), owner=pid, summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


# ============ REORDER_TOP_CARDS ============

def test_reorder_top_cards_heuristica_sort():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    # Topo: cards de custo 5, 1, 3 (heurística → 1, 3, 5)
    p.deck = ["stonks", "camarao", "vini_zumbi"] + p.deck  # custos 1, 1, 2
    # Forçando custos diferentes: usa nomes dummy
    p.deck = []
    # Simula 3 cartas com custos 5, 1, 3
    fake_high = "f_high"; fake_low = "f_low"; fake_mid = "f_mid"
    _cards_mod._CARDS_BY_ID[fake_high] = {"id": fake_high, "type": "MINION", "cost": 5, "attack": 1, "health": 1, "name": "H"}
    _cards_mod._CARDS_BY_ID[fake_low]  = {"id": fake_low,  "type": "MINION", "cost": 1, "attack": 1, "health": 1, "name": "L"}
    _cards_mod._CARDS_BY_ID[fake_mid]  = {"id": fake_mid,  "type": "MINION", "cost": 3, "attack": 1, "health": 1, "name": "M"}
    p.deck = [fake_high, fake_low, fake_mid, "filler1", "filler2"]

    eff = {"action": "REORDER_TOP_CARDS", "amount": 3, "target": {"mode": "SELF_DECK"}}
    effects.resolve_effect(state, eff, pid, None, {})
    # Esperado: ordenação por custo asc → low(1), mid(3), high(5)
    assert p.deck[0] == fake_low
    assert p.deck[1] == fake_mid
    assert p.deck[2] == fake_high
    # Resto do deck preservado
    assert p.deck[3] == "filler1"
    assert p.deck[4] == "filler2"


# ============ DRAW_HIGHEST_COST_REVEALED_CARD ============

def test_stonks_compra_se_tiver_topo_mais_caro():
    """Stonks: revela topo de cada deck; o dono do mais caro compra-o."""
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    # Pizza custa 5, camarao custa 1
    me.deck = ["pizza"] + me.deck       # custo 5 (vai ganhar)
    opp.deck = ["camarao"] + opp.deck   # custo 1
    me_hand_before = len(me.hand)

    eff = {
        "action": "DRAW_HIGHEST_COST_REVEALED_CARD",
        "target": {"mode": "OWNER_OF_HIGHEST_COST_REVEALED_CARD"},
        "tie_behavior": "NO_EFFECT",
    }
    effects.resolve_effect(state, eff, pid, None, {})
    assert len(me.hand) == me_hand_before + 1
    assert me.hand[-1].card_id == "pizza"


def test_stonks_oponente_ganha_se_topo_mais_caro():
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    me.deck = ["camarao"] + me.deck     # 1
    opp.deck = ["pizza"] + opp.deck     # 5 (vai ganhar)
    opp_hand_before = len(opp.hand)
    eff = {
        "action": "DRAW_HIGHEST_COST_REVEALED_CARD",
        "tie_behavior": "NO_EFFECT",
    }
    effects.resolve_effect(state, eff, pid, None, {})
    assert len(opp.hand) == opp_hand_before + 1
    assert opp.hand[-1].card_id == "pizza"


def test_stonks_empate_no_effect():
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    # Ambos topo custo 2 (vini_zumbi)
    me.deck = ["vini_zumbi"] + me.deck
    opp.deck = ["vini_zumbi"] + opp.deck
    me_hand_before = len(me.hand)
    opp_hand_before = len(opp.hand)

    eff = {
        "action": "DRAW_HIGHEST_COST_REVEALED_CARD",
        "tie_behavior": "NO_EFFECT",
    }
    effects.resolve_effect(state, eff, pid, None, {})
    # Empate → ninguém compra
    assert len(me.hand) == me_hand_before
    assert len(opp.hand) == opp_hand_before


# ============ DISCARD_LOWEST_COST_REVEALED_CARD ============

def test_discard_lowest_revealed():
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    me.deck = ["vini_zumbi"] + me.deck  # custo 2
    opp.deck = ["camarao"] + opp.deck   # custo 1

    eff_reveal = {"action": "REVEAL_TOP_CARD_EACH_DECK", "amount": 1,
                   "target": {"mode": "BOTH_DECKS"}}
    eff_disc = {"action": "DISCARD_LOWEST_COST_REVEALED_CARD",
                 "target": {"mode": "REVEALED_CARDS"}}
    ctx = {}
    effects.resolve_effect(state, eff_reveal, pid, None, ctx)
    effects.resolve_effect(state, eff_disc, pid, None, ctx)
    # opponent perdeu camarao (menor custo)
    assert opp.deck[0] != "camarao"


# ============ PLAY_TOP_CARD_FROM_DECK ============

def test_play_top_card_from_deck_lacaio():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    board_before = len(p.board)
    p.deck = ["camarao"] + p.deck  # camarao é MINION custo 1
    eff = {"action": "PLAY_TOP_CARD_FROM_DECK", "amount": 1,
           "target": {"mode": "SELF_DECK"}}
    effects.resolve_effect(state, eff, pid, None, {})
    # Camarão entrou em campo de graça
    assert len(p.board) == board_before + 1
    assert p.board[-1].card_id == "camarao"


# ============ PLAY_FROM_DECK (Fome) ============

def test_play_from_deck_filtro_tribo_e_custo():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    # ravioli é COMIDA custo 4, camarao é COMIDA custo 1, vini_zumbi NÃO é comida
    # Topo: ravioli (4) — pula, camarao (1) — passa
    p.deck = ["ravioli", "camarao"] + p.deck
    eff = {
        "action": "PLAY_FROM_DECK", "amount": 1,
        "target": {"mode": "SELF_DECK",
                    "filter": {"type": "MINION", "tribe": "COMIDA", "max_cost": 3},
                    "selection": "FIRST_MATCH"},
    }
    effects.resolve_effect(state, eff, pid, None, {})
    # Camarão (custo 1, COMIDA) deveria ter entrado em campo
    assert any(m.card_id == "camarao" for m in p.board), \
        f"Esperava camarao em campo. Board: {[m.card_id for m in p.board]}"
    # Ravioli não foi jogado (excedeu max_cost)
    assert not any(m.card_id == "ravioli" for m in p.board)


# ============ ADD_CARD_TO_DECK_POSITION_AND_SET_COST ============

def test_muriel_adiciona_hello_world_custo_1():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]

    # Carta fake "hello_world" com custo original 5
    _cards_mod._CARDS_BY_ID["hello_world"] = {
        "id": "hello_world", "type": "MINION", "cost": 5,
        "attack": 5, "health": 5, "name": "Hello World",
        "tags": [], "tribes": [], "effects": [],
    }
    deck_before = len(p.deck)
    eff = {
        "action": "ADD_CARD_TO_DECK_POSITION_AND_SET_COST",
        "card_id": "hello_world", "position": "MIDDLE", "cost": 1,
        "target": {"mode": "SELF_DECK"},
    }
    effects.resolve_effect(state, eff, pid, None, {})
    assert len(p.deck) == deck_before + 1
    # O marker está no deck
    markers = [c for c in p.deck if c.startswith("hello_world__mod__")]
    assert len(markers) == 1
    # Deck modifiers tem a entrada
    mods = state.deck_card_modifiers
    assert markers[0] in mods
    assert mods[markers[0]]["card_id"] == "hello_world"
    assert mods[markers[0]]["cost_override"] == 1


def test_muriel_carta_chega_a_mao_com_custo_modificado():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    # Limpa fake card
    _cards_mod._CARDS_BY_ID["hello_world"] = {
        "id": "hello_world", "type": "MINION", "cost": 5,
        "attack": 5, "health": 5, "name": "Hello World",
        "tags": [], "tribes": [], "effects": [],
    }
    # Coloca o marker direto no topo do deck
    state.deck_card_modifiers = {}
    marker = "hello_world__mod__test123"
    state.deck_card_modifiers[marker] = {"card_id": "hello_world", "cost_override": 1}
    p.deck = [marker] + p.deck

    hand_before = len(p.hand)
    effects.draw_card(state, p, 1)
    assert len(p.hand) == hand_before + 1
    drawn = p.hand[-1]
    # card_id é o original
    assert drawn.card_id == "hello_world"
    # cost_override foi aplicado
    assert drawn.cost_override == 1
    # marker foi consumido
    assert marker not in state.deck_card_modifiers


# ============ SHUFFLE_THIS_INTO_DECK ============

def test_shuffle_this_into_deck():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    deck_before = len(p.deck)
    eff = {"action": "SHUFFLE_THIS_INTO_DECK", "position": "MIDDLE",
           "target": {"mode": "SELF_DECK"}}
    effects.resolve_effect(state, eff, pid, None,
                            {"source_card_id": "moeda_perdida"})
    assert len(p.deck) == deck_before + 1
    assert "moeda_perdida" in p.deck


# ============ TRANSFORM_THIS_CARD ============

def test_transform_this_card_via_on_draw():
    """Moeda Perdida: vira coin quando comprada."""
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    # Coloca moeda_perdida no topo do deck
    p.deck = ["moeda_perdida"] + p.deck
    hand_before_ids = {c.instance_id for c in p.hand}

    effects.draw_card(state, p, 1)
    # A carta original (moeda_perdida) virou "coin" — procurar entre as
    # novas cartas da mão. Não devemos achar moeda_perdida.
    new_cards = [c for c in p.hand if c.instance_id not in hand_before_ids]
    assert any(c.card_id == "coin" for c in new_cards), \
        f"Esperava 'coin' nas novas: {[c.card_id for c in new_cards]}"
    assert not any(c.card_id == "moeda_perdida" for c in new_cards)


# ============ REVEAL_TOP_CARD_AND_CHOOSE_DRAW (Mario) ============

def test_mario_pega_carta_revelada_se_mais_cara():
    """Mario: ao ser comprada, revela top do deck. Se mais cara, pega ela
    no lugar de Mario."""
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    # Coloca Mario no topo, e logo abaixo uma carta cara
    cara = "stonks"  # cost 1 — vou usar uma cara real
    cara_card = get_card("stonks")
    # Mario custo 6. Vou usar custo maior pra ser pega
    # Não tem nada mais caro que mario disponível; vou injetar um fake
    _cards_mod._CARDS_BY_ID["super_caro"] = {
        "id": "super_caro", "type": "MINION", "cost": 10,
        "attack": 1, "health": 1, "name": "Super",
        "tags": [], "tribes": [], "effects": [],
    }
    p.deck = ["mario", "super_caro"] + p.deck
    hand_before = len(p.hand)

    effects.draw_card(state, p, 1)
    # Mario disparou ON_DRAW e como super_caro custa 10 > 6, pegou super_caro
    # Mario deveria ter sido removida da mão
    has_mario = any(c.card_id == "mario" for c in p.hand)
    has_super = any(c.card_id == "super_caro" for c in p.hand)
    assert has_super
    assert not has_mario


# ============ REPLACE_DRAW_WITH_PLAY_TOP_CARD (Portal) ============

def test_portal_passivo_joga_topo_no_inicio_do_turno():
    """Portal em campo: no início do PRÓXIMO turno do dono, joga top card."""
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    # Cria um Portal sintético em campo
    portal = _force_minion(state, pid, card_id="portal", ready=True)
    portal.effects = [{
        "trigger": "ON_TURN_START",
        "action": "REPLACE_DRAW_WITH_PLAY_TOP_CARD",
        "target": {"mode": "SELF_DECK"},
    }]
    # Coloca camarao no topo do deck
    p.deck = ["camarao"] + p.deck
    p_board_size = len(p.board)
    # Avança turno: meu fim → vez do oponente → fim oponente → meu turno de novo
    engine.end_turn(state, pid)
    foe = 1 - pid
    engine.end_turn(state, foe)
    # Agora é meu turno de novo. start_turn chamou ON_TURN_START → pendente
    # registrado, depois o draw foi substituído por play_top.
    # camarao DEVE ter entrado em campo
    assert any(m.card_id == "camarao" for m in p.board)
    assert state.current_player == pid
