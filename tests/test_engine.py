"""
Testes da engine. Roda com `pytest` a partir da raiz do projeto.

Cobertura básica:
- Carregamento das 240 cartas + token Moeda
- Inicialização de partida (mulligan, mãos, primeiro jogador)
- Mana progressiva
- Compra de carta no início do turno
- Jogar lacaio paga mana e ocupa campo
- Limite de 7 lacaios no campo
- Ataque entre lacaios (dano simultâneo)
- Ataque ao herói reduz vida
- TAUNT força ataque ao lacaio com provocar
- DIVINE_SHIELD absorve primeira instância de dano
- CHARGE permite atacar no turno em que entra
- Vitória quando vida do herói chega a zero
"""
from __future__ import annotations
import pytest

from game import engine
from game.cards import all_cards, get_card, load_cards
from game.state import (
    GameState, Minion, MAX_BOARD_SIZE, STARTING_HEALTH,
    STARTING_HAND_FIRST, STARTING_HAND_SECOND, gen_id, CardInHand,
)


# --------- helpers ---------

def _cheap_minion_pool():
    return [c for c in all_cards() if c.get("type") == "MINION" and c.get("cost", 99) <= 4]


def _build_deck(pool, n=30):
    deck = []
    for c in pool:
        deck.append(c["id"])
        deck.append(c["id"])
        if len(deck) >= n:
            break
    return deck[:n]


def _new_match(seed=42):
    pool = _cheap_minion_pool()
    deck_a = _build_deck(pool)
    deck_b = _build_deck(pool[5:] + pool[:5])
    state = engine.new_game("Alice", deck_a, "Bob", deck_b, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state: GameState, pid: int, *,
                  attack: int = 1, health: int = 1, max_health: int | None = None,
                  card_id: str = "test_minion", name: str = "Test",
                  tags: list[str] | None = None,
                  ready: bool = True, divine_shield: bool = False) -> Minion:
    """Insere um lacaio diretamente no campo, ignorando regras de jogada.

    `ready=True`  -> pronto pra atacar (summoning_sick=False).
    `ready=False` -> recém-entrou, com sickness.
    """
    tag_list = list(tags or [])
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=name,
        attack=attack,
        health=health,
        max_health=max_health if max_health is not None else health,
        tags=tag_list,
        owner=pid,
        summoning_sick=not ready,
        divine_shield=divine_shield or ("DIVINE_SHIELD" in tag_list),
    )
    state.players[pid].board.append(m)
    return m


# --------- testes ---------

def test_carga_cartas():
    load_cards()
    cards = all_cards()
    assert len(cards) >= 240
    ids = {c["id"] for c in cards}
    assert "coin" in ids


def test_novo_jogo_maos_iniciais():
    state = _new_match()
    assert state.phase == "PLAYING"
    p_first = state.current_player
    p_second = 1 - p_first
    assert len(state.players[p_first].hand) == STARTING_HAND_FIRST + 1
    assert len(state.players[p_second].hand) == STARTING_HAND_SECOND + 1
    has_coin = any(h.card_id == "coin" for h in state.players[p_second].hand)
    assert has_coin, "segundo jogador deve receber a Moeda"


def test_mana_progressiva():
    """Cada jogador tem sua própria progressão: 1, 2, 3... independente."""
    state = _new_match()
    pid = state.current_player
    assert state.players[pid].max_mana == 1

    engine.end_turn(state, pid)
    other = state.current_player
    assert state.players[other].max_mana == 1
    engine.end_turn(state, other)

    assert state.current_player == pid
    assert state.players[pid].max_mana == 2


def test_jogar_lacaio_paga_mana():
    state = _new_match()
    pid = state.current_player
    p = state.players[pid]
    mana_before = p.mana
    target = None
    for h in p.hand:
        c = get_card(h.card_id)
        if c["type"] == "MINION" and c["cost"] <= mana_before:
            target = h
            break
    assert target is not None
    cost = get_card(target.card_id)["cost"]
    board_before = len(p.board)

    ok, msg = engine.play_card(state, pid, target.instance_id)
    assert ok, f"falha jogando lacaio: {msg}"
    assert p.mana == mana_before - cost
    assert len(p.board) == board_before + 1


def test_limite_campo_7_lacaios():
    state = _new_match()
    pid = state.current_player
    for _ in range(MAX_BOARD_SIZE):
        _force_minion(state, pid)
    assert len(state.players[pid].board) == MAX_BOARD_SIZE
    blocked = False
    for h in state.players[pid].hand:
        c = get_card(h.card_id)
        if c["type"] == "MINION" and c["cost"] <= state.players[pid].mana:
            ok, _ = engine.play_card(state, pid, h.instance_id)
            if not ok:
                blocked = True
            break
    assert blocked, "não deveria conseguir jogar lacaio com campo cheio"


def test_ataque_entre_lacaios_dano_simultaneo():
    state = _new_match()
    pid = state.current_player
    other = 1 - pid
    a = _force_minion(state, pid, attack=3, health=4, ready=True)
    b = _force_minion(state, other, attack=2, health=5)

    ok, msg = engine.attack(state, pid, a.instance_id, b.instance_id)
    assert ok, msg
    assert a.health == 2
    assert b.health == 2
    assert a.attacks_this_turn == 1


def test_ataque_ao_heroi_reduz_vida():
    state = _new_match()
    pid = state.current_player
    other = 1 - pid
    a = _force_minion(state, pid, attack=4, health=2, ready=True)

    hp_before = state.players[other].hero_health
    ok, msg = engine.attack(state, pid, a.instance_id, f"hero:{other}")
    assert ok, msg
    assert state.players[other].hero_health == hp_before - 4


def test_taunt_obriga_alvo():
    state = _new_match()
    pid = state.current_player
    other = 1 - pid
    a = _force_minion(state, pid, attack=2, health=4, ready=True)
    nontaunt = _force_minion(state, other, attack=1, health=3)
    taunt = _force_minion(state, other, attack=1, health=4, tags=["TAUNT"])

    ok, _ = engine.attack(state, pid, a.instance_id, f"hero:{other}")
    assert not ok
    ok, _ = engine.attack(state, pid, a.instance_id, nontaunt.instance_id)
    assert not ok
    ok, _ = engine.attack(state, pid, a.instance_id, taunt.instance_id)
    assert ok


def test_divine_shield_absorve_primeiro_dano():
    state = _new_match()
    pid = state.current_player
    other = 1 - pid
    a = _force_minion(state, pid, attack=3, health=5, ready=True)
    b = _force_minion(state, other, attack=1, health=4,
                      tags=["DIVINE_SHIELD"], divine_shield=True)

    ok, _ = engine.attack(state, pid, a.instance_id, b.instance_id)
    assert ok
    assert b.divine_shield is False
    assert b.health == 4
    assert a.health == 4


def test_charge_permite_atacar_no_turno():
    state = _new_match()
    pid = state.current_player
    m = _force_minion(state, pid, attack=2, health=2,
                      tags=["CHARGE"], ready=False)
    other = 1 - pid

    hp_before = state.players[other].hero_health
    ok, _ = engine.attack(state, pid, m.instance_id, f"hero:{other}")
    assert ok
    assert state.players[other].hero_health == hp_before - 2


def test_summoning_sickness_sem_charge():
    state = _new_match()
    pid = state.current_player
    other = 1 - pid
    m = _force_minion(state, pid, attack=3, health=3, ready=False)
    ok, _ = engine.attack(state, pid, m.instance_id, f"hero:{other}")
    assert not ok


def test_vitoria_quando_heroi_chega_a_zero():
    state = _new_match()
    pid = state.current_player
    other = 1 - pid
    state.players[other].hero_health = 1
    a = _force_minion(state, pid, attack=10, health=1, ready=True)

    ok, _ = engine.attack(state, pid, a.instance_id, f"hero:{other}")
    assert ok
    assert state.phase == "ENDED"
    assert state.winner == pid


def test_partida_simulada_nao_quebra():
    """Joga uma partida automatizada por até 80 turnos sem encontrar exceções."""
    state = _new_match(seed=7)
    for _ in range(80):
        if state.phase == "ENDED":
            break
        pid = state.current_player
        p = state.players[pid]
        progressed = True
        while progressed:
            progressed = False
            for h in list(p.hand):
                c = get_card(h.card_id)
                if c["cost"] <= p.mana:
                    ok, _ = engine.play_card(state, pid, h.instance_id)
                    if ok:
                        progressed = True
                        break
        progressed = True
        while progressed:
            progressed = False
            for m in list(p.board):
                if m.can_attack() and not m.frozen and m.attack > 0:
                    ok, _ = engine.attack(state, pid, m.instance_id, f"hero:{1-pid}")
                    if ok:
                        progressed = True
                        break
        engine.end_turn(state, pid)
    assert state.event_log


def test_battlecry_sem_alvo_valido_joga_sem_alvo():
    """Carta com battlecry CHOSEN FRIENDLY_MINION deve poder ser jogada sem alvo
    quando não há lacaios aliados em campo. O efeito vira no-op, mas a carta
    entra normalmente em campo e a mana é gasta.
    """
    # Encontra uma carta com effect ON_PLAY + target.mode CHOSEN + valid FRIENDLY_MINION
    target_card = None
    for c in all_cards():
        for ef in c.get("effects") or []:
            if ef.get("trigger") != "ON_PLAY":
                continue
            tgt = ef.get("target") or {}
            if tgt.get("mode") == "CHOSEN":
                valid = tgt.get("valid") or []
                if "FRIENDLY_MINION" in valid or "OTHER_FRIENDLY_MINION" in valid:
                    target_card = c
                    break
        if target_card:
            break
    assert target_card is not None, "esperava existir ao menos uma carta com battlecry de FRIENDLY_MINION"

    cid = target_card["id"]
    state = engine.new_game("A", [cid] * 30, "B", [cid] * 30, seed=1)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])

    pid = state.current_player
    p = state.players[pid]
    # Garante campo vazio (sem aliados pra afetar)
    p.board = []

    target_hand = next((h for h in p.hand if h.card_id == cid), None)
    assert target_hand is not None
    cost = target_card.get("cost", 0)
    if p.mana < cost:
        # se a carta custa mais do que 1, pula turnos pra ter mana
        while p.mana < cost and state.phase == "PLAYING":
            engine.end_turn(state, state.current_player)
            if state.current_player == pid:
                p.board = []  # mantém vazio

    # Joga sem alvo — deve funcionar agora
    ok, msg = engine.play_card(state, pid, target_hand.instance_id, chosen_target=None)
    assert ok, f"esperava sucesso, falhou: {msg}"
    # se for MINION, agora há 1 lacaio em campo
    if target_card.get("type") == "MINION":
        assert len(p.board) == 1
    # Evento de no_targets_available foi logado
    no_target_evs = [e for e in state.event_log if e.get("type") == "no_targets_available"]
    assert len(no_target_evs) >= 1


def test_minion_to_dict_inclui_can_attack_e_keywords():
    """Cliente precisa de can_attack e keywords pra renderizar visuais."""
    state = _new_match()
    pid = state.current_player
    m = _force_minion(state, pid, attack=2, health=3, ready=True,
                      tags=["TAUNT", "DIVINE_SHIELD", "POISONOUS"],
                      divine_shield=True)
    d = m.to_dict()
    assert "can_attack" in d
    assert "can_attack_hero" in d
    assert "keywords" in d
    assert d["can_attack"] is True
    assert "TAUNT" in d["keywords"]
    assert "DIVINE_SHIELD" in d["keywords"]
    assert "POISONOUS" in d["keywords"]
    # Quando silenciado, keywords vira []
    m.silenced = True
    d2 = m.to_dict()
    assert d2["keywords"] == []
