"""
Testes do Lote 2 — 30 ações novas.
"""
from __future__ import annotations
import pytest
from game import engine, effects, targeting
from game.cards import get_card
from game.state import GameState, Minion, CardInHand, gen_id


def _new_blank_match(seed: int = 1):
    state = engine.new_game("A", ["vini_zumbi"]*30, "B", ["vini_zumbi"]*30, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, card_id="test", name="Test", attack=2, health=2,
                  tags=None, ready=True):
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=name,
        attack=attack,
        health=health,
        max_health=health,
        tags=list(tags or []),
        owner=pid,
        summoning_sick=not ready,
    )
    state.players[pid].board.append(m)
    return m


# ============ DAMAGE CALCULADO ============

def test_damage_equal_to_target_health():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    enemy = _force_minion(state, foe, attack=2, health=7)
    me_minion = _force_minion(state, pid, attack=1, health=10)
    eff = {
        "action": "DAMAGE_EQUAL_TO_TARGET_HEALTH",
        "reference_target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]},
        "target": {"mode": "SELF"},
    }
    effects.resolve_effect(state, eff, pid, me_minion,
                            {"chosen_target": enemy.instance_id})
    # Self levou dano = 7 (vida do enemy)
    assert me_minion.health == 10 - 7


def test_damage_equal_to_target_attack():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    enemy = _force_minion(state, foe, attack=5, health=3)
    me_minion = _force_minion(state, pid, attack=1, health=10)
    eff = {
        "action": "DAMAGE_EQUAL_TO_TARGET_ATTACK",
        "reference_target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]},
        "target": {"mode": "SELF"},
    }
    effects.resolve_effect(state, eff, pid, me_minion,
                            {"chosen_target": enemy.instance_id})
    assert me_minion.health == 10 - 5


def test_damage_equal_to_hand_size():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    me = state.players[pid]
    while len(me.hand) > 4:
        me.hand.pop()  # exata 4 cartas
    enemy = _force_minion(state, foe, attack=1, health=10)
    eff = {
        "action": "DAMAGE_EQUAL_TO_HAND_SIZE",
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, None,
                            {"chosen_target": enemy.instance_id})
    assert enemy.health == 10 - 4


def test_damage_with_excess_to_self():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    me = state.players[pid]
    me_hp_before = me.hero_health
    enemy = _force_minion(state, foe, attack=1, health=2)  # vida 2, dano 6 → 4 de excesso
    eff = {
        "action": "DAMAGE_WITH_EXCESS_TO_SELF",
        "amount": 6,
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, None,
                            {"chosen_target": enemy.instance_id})
    # Enemy morre, eu tomo 4 de dano
    assert me.hero_health == me_hp_before - 4


# ============ ROUBO ============

def test_steal_health():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    me = state.players[pid]
    me.hero_health = 20
    enemy = _force_minion(state, foe, attack=1, health=5)
    eff = {
        "action": "STEAL_HEALTH", "amount": 3,
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, None,
                            {"chosen_target": enemy.instance_id})
    assert enemy.health == 5 - 3
    assert me.hero_health == 20 + 3


def test_steal_stats():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    enemy = _force_minion(state, foe, attack=4, health=4)
    me_minion = _force_minion(state, pid, attack=1, health=1)
    eff = {
        "action": "STEAL_STATS", "attack": 1, "health": 1,
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, me_minion,
                            {"chosen_target": enemy.instance_id})
    assert enemy.attack == 3
    assert enemy.health == 3
    assert me_minion.attack == 2
    assert me_minion.health == 2


# ============ SET / DOUBLE / SWAP / REFRESH ============

def test_set_attack_on_all_enemies():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    a = _force_minion(state, foe, attack=5, health=5)
    b = _force_minion(state, foe, attack=8, health=2)
    eff = {
        "action": "SET_ATTACK", "amount": 1,
        "target": {"mode": "ALL_ENEMY_MINIONS"},
    }
    effects.resolve_effect(state, eff, pid, None, {})
    assert a.attack == 1
    assert b.attack == 1


def test_double_attack():
    state = _new_blank_match()
    pid = state.current_player
    me_minion = _force_minion(state, pid, attack=3, health=5)
    eff = {"action": "DOUBLE_ATTACK", "target": {"mode": "SELF"}}
    effects.resolve_effect(state, eff, pid, me_minion, {})
    assert me_minion.attack == 6


def test_swap_attack_health():
    state = _new_blank_match()
    pid = state.current_player
    m1 = _force_minion(state, pid, attack=5, health=2)
    eff = {"action": "SWAP_ATTACK_HEALTH", "target": {"mode": "ALL_FRIENDLY_MINIONS"}}
    effects.resolve_effect(state, eff, pid, None, {})
    assert m1.attack == 2
    assert m1.health == 5


def test_refresh_attack():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, attack=3, health=5)
    m.attacks_this_turn = 1  # já atacou
    assert not m.can_attack()  # bloqueado
    eff = {"action": "REFRESH_ATTACK", "target": {"mode": "SELF"}}
    effects.resolve_effect(state, eff, pid, m, {})
    assert m.attacks_this_turn == 0
    assert m.can_attack()


# ============ DEFESA ============

def test_permanent_stealth_nao_quebra_atacando():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    nando = _force_minion(state, pid, attack=2, health=2,
                           tags=["STEALTH", "PERMANENT_STEALTH"])
    enemy = _force_minion(state, foe, attack=1, health=5)
    ok, _ = engine.attack(state, pid, nando.instance_id, enemy.instance_id)
    assert ok
    # Stealth não foi removido
    assert "STEALTH" in nando.tags


def test_reflect_damage():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    tronco = _force_minion(state, pid, attack=1, health=5, tags=["REFLECT", "TAUNT"])
    attacker = _force_minion(state, foe, attack=3, health=8)
    # Oponente ataca tronco → tronco reflete 3 de dano de volta
    state.current_player = foe
    ok, _ = engine.attack(state, foe, attacker.instance_id, tronco.instance_id)
    assert ok
    # Tronco tomou 3 (do ataque) + atacante levou 1 do tronco + 3 refletidos = 4
    # Atacante 8 - 1 (atk do tronco) - 3 (reflect) = 4
    assert attacker.health == 4


def test_attack_damage_immune():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    monge = _force_minion(state, pid, attack=2, health=2,
                           tags=["ATTACK_DAMAGE_IMMUNE"])
    enemy = _force_minion(state, foe, attack=5, health=5)
    state.current_player = foe
    ok, _ = engine.attack(state, foe, enemy.instance_id, monge.instance_id)
    assert ok
    # Monge não recebeu dano de ataque
    assert monge.health == 2


def test_gain_attack_on_damage():
    """Baiano: ganha atk por dano levado."""
    state = _new_blank_match()
    pid = state.current_player
    baiano = _force_minion(state, pid, attack=2, health=10,
                            tags=["GAIN_ATTACK_ON_DAMAGE"])
    effects.damage_character(state, baiano, 3, source_owner=1-pid)
    assert baiano.attack == 5


def test_poisonous_against_tribe():
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    grama = _force_minion(state, pid, attack=1, health=3,
                           tags=["POISONOUS_VS_VINI"], ready=True)
    # Vini target — deve morrer
    vini = _force_minion(state, foe, attack=1, health=10)
    vini.tribes = ["VINI"]
    # Não-Vini target — não morre
    other = _force_minion(state, foe, attack=1, health=10)
    other.tribes = ["BRASILEIRO"]

    # Grama ataca Vini → veneno mata
    ok, _ = engine.attack(state, pid, grama.instance_id, vini.instance_id)
    assert ok
    assert state.find_minion(vini.instance_id) is None  # morreu

    # Reseta grama (refresh)
    grama.attacks_this_turn = 0
    # Grama ataca outro brasileiro — não morre por veneno (mas leva o atk)
    ok, _ = engine.attack(state, pid, grama.instance_id, other.instance_id)
    assert ok
    f = state.find_minion(other.instance_id)
    assert f is not None
    assert f[0].health == 10 - 1  # só perdeu 1 de atk normal


# ============ BUFFS POR CONDIÇÃO ============

def test_buff_attack_per_friendly_minion():
    state = _new_blank_match()
    pid = state.current_player
    # 3 outros aliados em campo
    _force_minion(state, pid)
    _force_minion(state, pid)
    _force_minion(state, pid)
    rica = _force_minion(state, pid, attack=2, health=3)
    eff = {"action": "BUFF_ATTACK_PER_FRIENDLY_MINION", "amount": 1}
    effects.resolve_effect(state, eff, pid, rica, {})
    assert rica.attack == 2 + 3


def test_buff_self_per_friendly_minion():
    state = _new_blank_match()
    pid = state.current_player
    _force_minion(state, pid)
    _force_minion(state, pid)
    rica = _force_minion(state, pid, attack=1, health=3)
    eff = {
        "action": "BUFF_SELF_PER_FRIENDLY_MINION",
        "attack": 1, "health": 1,
    }
    effects.resolve_effect(state, eff, pid, rica, {})
    assert rica.attack == 3
    assert rica.health == 5


# ============ DRAWS FILTRADOS ============

def test_draw_lowest_cost_minion():
    """Deck com lacaios de custo 1, 3, 5 → compra o de custo 1."""
    state = engine.new_game("A", ["vini_zumbi"]*30, "B", ["vini_zumbi"]*30, seed=1)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    pid = state.current_player
    p = state.players[pid]
    # Limpa o deck e injeta cartas com custos diferentes
    p.deck = ["camarao", "vini_zumbi", "stonks"]  # custos 1, 2, 5
    initial_hand_size = len(p.hand)
    eff = {"action": "DRAW_LOWEST_COST_MINION", "amount": 1}
    effects.resolve_effect(state, eff, pid, None, {})
    assert len(p.hand) == initial_hand_size + 1
    last = p.hand[-1]
    assert last.card_id == "camarao"  # menor custo (1)


def test_draw_minion_with_tag():
    state = engine.new_game("A", ["vini_zumbi"]*30, "B", ["vini_zumbi"]*30, seed=1)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    pid = state.current_player
    p = state.players[pid]
    # Coloca uma carta com DEATHRATTLE no deck
    deathrattle_minions = [
        c for c in [get_card("ramoni"), get_card("morango"),
                     get_card("muriel"), get_card("investidor")]
        if c and "DEATHRATTLE" in (c.get("tags") or [])
    ]
    if not deathrattle_minions:
        pytest.skip("Nenhuma carta com DEATHRATTLE no JSON")
    p.deck = ["camarao"] * 5 + [deathrattle_minions[0]["id"]]

    eff = {
        "action": "DRAW_MINION_WITH_TAG",
        "target": {"required_tag": "DEATHRATTLE"},
    }
    initial = len(p.hand)
    effects.resolve_effect(state, eff, pid, None, {})
    assert len(p.hand) == initial + 1
    drawn = p.hand[-1]
    drawn_card = get_card(drawn.card_id)
    assert "DEATHRATTLE" in (drawn_card.get("tags") or [])


def test_opponent_steals_random_hand_card():
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    me_hand_before = len(me.hand)
    opp_hand_before = len(opp.hand)
    eff = {"action": "OPPONENT_STEALS_RANDOM_HAND_CARD", "amount": 1}
    effects.resolve_effect(state, eff, pid, None, {})
    assert len(me.hand) == me_hand_before - 1
    assert len(opp.hand) == opp_hand_before + 1


# ============ COST REDUCTION DINÂMICO ============

def test_cost_reduction_per_friendly_minion():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 5; p.max_mana = 5

    # Carta fake "garcom test" custando 3
    from game.cards import _CARDS_BY_ID
    _CARDS_BY_ID["test_garcom"] = {
        "id": "test_garcom",
        "name": "Test Garçom",
        "type": "MINION",
        "cost": 3,
        "attack": 2, "health": 2,
        "tags": [], "tribes": [],
        "effects": [{
            "trigger": "IN_HAND",
            "action": "COST_REDUCTION_PER_FRIENDLY_MINION",
            "amount": 1,
            "target": {"mode": "SELF_CARD"},
        }],
    }
    ch = CardInHand(instance_id=gen_id("h_"), card_id="test_garcom")
    p.hand.append(ch)

    # Sem aliados: custo 3
    cost = engine.compute_dynamic_cost(state, p, ch, _CARDS_BY_ID["test_garcom"])
    assert cost == 3

    # 2 aliados → custo 3 - 2 = 1
    _force_minion(state, pid)
    _force_minion(state, pid)
    cost = engine.compute_dynamic_cost(state, p, ch, _CARDS_BY_ID["test_garcom"])
    assert cost == 1
