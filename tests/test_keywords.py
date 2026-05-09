"""
Testes para as mecânicas de keywords corrigidas:
- Tribos derivadas (FRUTA → COMIDA)
- Congelar com perda de 1 turno (não descongela imediatamente)
- ECHO devolve carta como temporária e descarta no fim do turno
- CHOOSE_ONE usa chose_index do cliente
- Furtividade bloqueia targeting de feitiços (mas não AOE)
- Silenciar remove buffs e tags adicionadas
- POISONOUS é bloqueado por DIVINE_SHIELD
"""
from __future__ import annotations
import pytest

from game import engine, effects, targeting
from game.cards import all_cards, get_card, card_has_tribe
from game.state import (
    GameState, Minion, CardInHand, gen_id, MAX_HAND_SIZE,
)


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


# ============ TRIBOS ============

def test_tribo_fruta_implica_comida():
    """Helper card_has_tribe e Minion.has_tribe consideram FRUTA→COMIDA."""
    banana = get_card("banana")
    assert "FRUTA" in (banana.get("tribes") or [])
    # Embora "COMIDA" não esteja explícita
    assert "COMIDA" not in (banana.get("tribes") or [])
    # mas card_has_tribe enxerga
    assert card_has_tribe(banana, "FRUTA")
    assert card_has_tribe(banana, "COMIDA")
    # Minion também
    state = _new_blank_match()
    m = _force_minion(state, 0, card_id="banana")
    m.tribes = ["FRUTA"]
    assert m.has_tribe("FRUTA")
    assert m.has_tribe("COMIDA")
    assert not m.has_tribe("BRASIL")


def test_reduce_cost_comida_aplica_em_fruta():
    """Pending reduction com filtro CARD_WITH_TRIBE_COMIDA também afeta FRUTAs."""
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 5; p.max_mana = 5

    # Banana é FRUTA (e portanto COMIDA derivada)
    ch = CardInHand(instance_id=gen_id("h_"), card_id="banana")
    p.hand.append(ch)

    state.pending_modifiers.append({
        "kind": "next_card_cost_reduction", "owner": pid, "amount": 1,
        "valid": ["CARD_WITH_TRIBE_COMIDA"], "expires_on": "end_of_turn",
        "consumed": False,
    })

    base_cost = (get_card("banana") or {}).get("cost", 0)
    mana_before = p.mana
    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    # Custou base - 1 (devido à reduction aplicada via FRUTA→COMIDA)
    assert p.mana == mana_before - max(0, base_cost - 1)


# ============ CONGELAR ============

def test_freeze_perde_proximo_turno_de_ataque():
    """Congelamento consome apenas a próxima oportunidade de ataque."""
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    m = _force_minion(state, pid, attack=3, health=3, ready=True)

    eff = {"action": "FREEZE", "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}}
    effects.resolve_effect(state, eff, foe, None, {"chosen_target": m.instance_id})

    assert m.frozen is True
    assert m.can_attack() is False

    # Ao fim do próximo turno do dono, já descongela.
    engine.end_turn(state, pid)
    assert m.frozen is False


def test_freeze_aliado_propria_aplicacao_descongela_no_fim_do_turno():
    """Se o próprio dono congela seu lacaio (caso raro), freeze_pending=False
    e descongela no fim do mesmo turno."""
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, attack=2, health=2, ready=True)
    eff = {"action": "FREEZE", "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}}
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": m.instance_id})
    assert m.frozen is True
    assert m.freeze_pending is False  # auto-aplicado
    engine.end_turn(state, pid)
    assert m.frozen is False  # descongelou no mesmo end_turn


# ============ ECHO ============

def test_echo_carta_volta_a_mao_como_temporaria():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 3; p.max_mana = 3

    # Mock de uma carta SPELL com tag ECHO (não usamos uma do JSON pra não
    # depender de ações específicas implementadas)
    fake = {
        "id": "test_echo_spell",
        "name": "Eco Test",
        "type": "SPELL",
        "cost": 1,
        "tags": ["ECHO"],
        "tribes": [],
        "effects": [{"trigger": "ON_PLAY", "action": "DRAW_CARD", "amount": 1,
                     "target": {"mode": "SELF_PLAYER"}}],
    }
    # Injeta no cards loader
    from game.cards import _CARDS_BY_ID
    _CARDS_BY_ID["test_echo_spell"] = fake

    ch = CardInHand(instance_id=gen_id("h_"), card_id="test_echo_spell")
    p.hand.append(ch)
    hand_before = len(p.hand)

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg

    # Carta original sumiu, mas uma cópia ECHO temporária apareceu
    assert ch not in p.hand
    echo_copies = [c for c in p.hand if c.echo_temporary]
    assert len(echo_copies) == 1
    assert echo_copies[0].card_id == "test_echo_spell"


def test_echo_temporary_descartada_no_fim_do_turno():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    # Adiciona uma echo_temporary direto
    ch = CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi", echo_temporary=True)
    p.hand.append(ch)

    engine.end_turn(state, pid)

    # Deve ter sumido
    assert ch not in p.hand
    assert not any(c.echo_temporary for c in p.hand)


# ============ CHOOSE_ONE ============

def test_choose_one_usa_chose_index():
    """O handler escolhe a opção indicada pelo cliente."""
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid

    # Coloca 1 lacaio inimigo pra ser alvo da opção 0 (DAMAGE)
    enemy = _force_minion(state, foe, attack=1, health=5)

    eff = {
        "action": "CHOOSE_ONE",
        "choices": [
            {"action": "DAMAGE", "amount": 3,
             "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}},
            {"action": "BUFF_STATS", "amount": {"attack": 5, "health": 5},
             "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION", "ENEMY_MINION"]}},
        ],
    }

    # Cliente escolhe opção 0 (DAMAGE)
    effects.resolve_effect(state, eff, pid, None,
                           {"chosen_target": enemy.instance_id, "chose_index": 0})
    assert enemy.health == 5 - 3  # tomou 3 de dano

    # Reseta e tenta opção 1 (BUFF)
    enemy.health = 5
    enemy.attack = 1
    effects.resolve_effect(state, eff, pid, None,
                           {"chosen_target": enemy.instance_id, "chose_index": 1})
    assert enemy.attack == 1 + 5
    assert enemy.health == 5 + 5


def test_choose_one_fallback_primeira_opcao_sem_chose_index():
    """Se cliente não enviar chose_index, escolhe a opção 0."""
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    enemy = _force_minion(state, foe, attack=1, health=5)

    eff = {
        "action": "CHOOSE_ONE",
        "choices": [
            {"action": "DAMAGE", "amount": 2,
             "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}},
            {"action": "DAMAGE", "amount": 4,
             "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}},
        ],
    }
    # Sem chose_index → fallback é 0 (dano 2)
    effects.resolve_effect(state, eff, pid, None,
                           {"chosen_target": enemy.instance_id})
    assert enemy.health == 5 - 2


# ============ STEALTH (FURTIVIDADE) ============

def test_stealth_bloqueia_targeting_chosen():
    """Lacaio inimigo com STEALTH não pode ser alvo de feitiço com CHOSEN."""
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    stealth_m = _force_minion(state, foe, attack=2, health=3, tags=["STEALTH"])

    eff = {"action": "DAMAGE", "amount": 2,
           "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}}

    # Tenta atingir o lacaio stealth → não passa o filtro de targeting
    effects.resolve_effect(state, eff, pid, None,
                           {"chosen_target": stealth_m.instance_id})
    assert stealth_m.health == 3, "stealth deveria bloquear o targeting CHOSEN"


def test_stealth_nao_bloqueia_aoe():
    """STEALTH NÃO protege de AOE (ALL_ENEMY_MINIONS)."""
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    stealth_m = _force_minion(state, foe, attack=2, health=3, tags=["STEALTH"])
    other_m = _force_minion(state, foe, attack=1, health=3)

    eff = {"action": "DAMAGE_ALL_ENEMY_MINIONS", "amount": 1}
    effects.resolve_effect(state, eff, pid, None, {})
    assert stealth_m.health == 2, "AOE deveria atingir stealth"
    assert other_m.health == 2


# ============ SILENCIAR ============

def test_silence_remove_buffs():
    """SILENCE reseta atk/health para valores originais da carta."""
    state = _new_blank_match()
    pid = state.current_player
    # Vini Zumbi original é 2/3
    base = get_card("vini_zumbi")
    base_atk = base.get("attack", 2)
    base_hp = base.get("health", 3)

    m = _force_minion(state, pid, card_id="vini_zumbi",
                       attack=base_atk + 5, health=base_hp + 5,
                       tags=list(base.get("tags") or []) + ["TAUNT", "DIVINE_SHIELD"])
    m.max_health = base_hp + 5
    m.divine_shield = True

    eff = {"action": "SILENCE", "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}}
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": m.instance_id})

    assert m.silenced is True
    assert m.attack == base_atk
    assert m.max_health == base_hp
    assert m.divine_shield is False
    # Tags resetadas para originais (não tem TAUNT/DIVINE_SHIELD na carta original)
    assert "TAUNT" not in m.tags
    assert "DIVINE_SHIELD" not in m.tags


def test_silence_mantem_dano_proporcional():
    """Se o lacaio já tinha levado dano, silenciar não cura."""
    state = _new_blank_match()
    pid = state.current_player
    base = get_card("vini_zumbi")
    base_hp = base.get("health", 3)

    # Lacaio com max=base+2 (buffado) e damage=1
    m = _force_minion(state, pid, card_id="vini_zumbi", health=base_hp + 1)
    m.max_health = base_hp + 2  # buffado em +2 no max
    # Já tomou 1 de dano

    eff = {"action": "SILENCE", "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}}
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": m.instance_id})

    assert m.max_health == base_hp
    # Ainda com 1 de dano: vida = base - 1
    assert m.health == max(1, base_hp - 1)


# ============ POISONOUS + DIVINE_SHIELD ============

def test_divine_shield_bloqueia_poisonous():
    """Veneno depende de causar dano > 0. Divine Shield zera o dano,
    portanto veneno não dispara."""
    state = _new_blank_match()
    pid = state.current_player
    foe = 1 - pid
    poisonous_m = _force_minion(state, pid, attack=1, health=3,
                                  tags=["POISONOUS"], ready=True)
    target = _force_minion(state, foe, attack=1, health=10)
    target.divine_shield = True

    ok, msg = engine.attack(state, pid, poisonous_m.instance_id, target.instance_id)
    assert ok, msg

    # Divine shield absorveu - target ainda em jogo
    f = state.find_minion(target.instance_id)
    assert f is not None, "target não deveria morrer porque o shield bloqueou"
    assert f[0].divine_shield is False  # shield consumido
    assert f[0].health == 10  # vida intacta
