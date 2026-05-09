"""Testes dos bugs de cartas reportados em playtest.

- Igão Chave: STEAL_STATS deve poder matar lacaio com 1 hp.
- Kiwi: SUMMON_COPY com use_base_stats deve gerar token base 2/2 sem buffs.
- Niuraozão: REDUCE_COST com FRIENDLY_MINION_TRIBE_EXISTS deve aplicar.
- Bloquear: deve pedir apenas 1 alvo (mesmo lacaio recebe SET_STATS e TAUNT).
- Tomo Amaldiçoado: state expõe next_spell_costs_health para a UI.
"""
from __future__ import annotations
import pytest

from game import engine, effects, targeting
from game.cards import all_cards, load_cards, get_card
from game.state import GameState, Minion, CardInHand, gen_id


def _cheap_pool():
    load_cards()
    return [c for c in all_cards() if c.get("type") == "MINION" and c.get("cost", 99) <= 4]


def _build_deck(pool, n=30):
    out = []
    for c in pool:
        out.extend([c["id"]] * 2)
        if len(out) >= n:
            break
    return out[:n]


def _new_match(seed=42):
    pool = _cheap_pool()
    state = engine.new_game("A", _build_deck(pool), "B", _build_deck(pool[5:] + pool[:5]),
                            seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, attack=1, health=1, max_health=None, tags=None,
                  ready=True, card_id="t", name="t"):
    tag_list = list(tags or [])
    m = Minion(
        instance_id=gen_id("m_"), card_id=card_id, name=name,
        attack=attack, health=health,
        max_health=max_health if max_health is not None else health,
        tags=tag_list, owner=pid,
        summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in tag_list,
    )
    state.players[pid].board.append(m)
    return m


# ===== Igão Chave =====

def test_igao_chave_mata_lacaio_de_1hp():
    """STEAL_STATS aplicado a um 1/1 deve causar morte (não clipar em 1)."""
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid

    igao = _force_minion(state, pid, attack=3, health=3, ready=True)
    igao.card_id = "igao_chave"
    igao.name = "Igão Chave"
    target = _force_minion(state, foe, attack=1, health=1, ready=True)

    eff = {
        "action": "STEAL_STATS",
        "attack_amount": 1,
        "health_amount": 1,
        "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]},
    }
    effects.resolve_effect(state, eff, pid, igao,
                           ctx={"chosen_target": target.instance_id})
    engine.cleanup(state)

    # Alvo deve estar morto e fora do campo.
    assert target not in state.players[foe].board, (
        "Alvo de 1/1 deveria morrer ao perder 1/1 com STEAL_STATS"
    )
    # Igão fica 4/4.
    assert igao.attack == 4
    assert igao.max_health == 4
    assert igao.health == 4


# ===== Kiwi =====

def test_kiwi_summon_copy_use_base_stats():
    """Kiwi morre buffed e o token resultante deve ser base 2/2 sem deathrattle."""
    state = _new_match()
    pid = state.current_player

    kiwi = effects.summon_minion_from_card(state, pid, "kiwi")
    assert kiwi is not None
    assert kiwi.attack == 2 and kiwi.max_health == 2
    # Buff o Kiwi: agora 5/4
    kiwi.attack += 3
    kiwi.max_health += 2
    kiwi.health = kiwi.max_health
    assert (kiwi.attack, kiwi.max_health) == (5, 4)

    # Mata o Kiwi → deathrattle SUMMON_COPY use_base_stats=true
    kiwi.health = 0
    engine.cleanup(state)

    # Algum lacaio com card_id=kiwi deve estar no campo, base 2/2, sem DEATHRATTLE.
    kiwis = [m for m in state.players[pid].board if m.card_id == "kiwi"]
    assert len(kiwis) == 1, f"Esperava 1 token Kiwi, achei {len(kiwis)}"
    token = kiwis[0]
    assert token.attack == 2, f"Token deveria ter 2 ataque, tem {token.attack}"
    assert token.max_health == 2, f"Token deveria ter 2 vida, tem {token.max_health}"
    assert "DEATHRATTLE" not in token.tags, "Token não deveria ter Último Suspiro"
    # Sem trigger ON_DEATH no token (não respawna).
    assert not any(e.get("trigger") == "ON_DEATH" for e in (token.effects or []))


# ===== Niuraozão =====

def test_niuraozao_recebe_desconto_com_fera_aliada():
    """Niuraozão custa 5 normalmente, 4 se houver outra Fera aliada."""
    state = _new_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="niuraozao"))
    ch = p.hand[0]
    card = get_card("niuraozao")

    # Sem feras aliadas: custo 5.
    assert engine.compute_dynamic_cost(state, p, ch, card) == 5

    # Com uma fera aliada no campo: custo 4.
    fera = _force_minion(state, pid, attack=2, health=2, ready=True)
    fera.tribes = ["FERA"]
    assert engine.compute_dynamic_cost(state, p, ch, card) == 4

    # Fera no inimigo NÃO conta.
    state.players[pid].board.remove(fera)
    fera_inimiga = _force_minion(state, 1 - pid, attack=2, health=2)
    fera_inimiga.tribes = ["FERA"]
    assert engine.compute_dynamic_cost(state, p, ch, card) == 5


# ===== Bloquear =====

def test_bloquear_pede_apenas_um_alvo():
    """Bloquear tem 2 efeitos (SET_STATS + ADD_TAG) que devem reusar o mesmo
    alvo escolhido. chosen_targets_for_card deve retornar 1 desc."""
    card = get_card("bloquear")
    descs = targeting.chosen_targets_for_card(card)
    assert len(descs) == 1, (
        f"Bloquear deveria pedir 1 alvo, pediu {len(descs)}"
    )


def test_bloquear_aplica_set_stats_e_taunt_no_mesmo_alvo():
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid

    target = _force_minion(state, foe, attack=4, health=4, ready=True)
    p = state.players[pid]
    p.hand.clear()
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="bloquear"))
    p.mana = 10

    ok, msg = engine.play_card(state, pid, p.hand[0].instance_id,
                                chosen_target=target.instance_id)
    assert ok, f"Bloquear não jogado: {msg}"

    assert target.attack == 0
    assert target.max_health == 1
    assert "TAUNT" in target.tags


# ===== Tomo Amaldiçoado =====

def test_tomo_amaldicoado_expoe_flag_no_state():
    """Backend já paga vida; faltava expor o flag para a UI."""
    state = _new_match()
    pid = state.current_player

    state.pending_modifiers.append({
        "kind": "next_spell_costs_health_instead_of_mana",
        "owner": pid,
        "expires_on": "end_of_turn",
        "consumed": False,
    })

    snapshot = state.to_dict(viewer_id=pid)
    assert snapshot["you"].get("next_spell_costs_health") is True
    assert snapshot["opponent"].get("next_spell_costs_health") is False


def test_tomo_amaldicoado_paga_vida_no_proximo_feitico():
    state = _new_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="tomo_amaldiçoado"))
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="ataque_de_niurau"))
    p.mana = 9
    p.max_mana = 10

    ok, _ = engine.play_card(state, pid, p.hand[0].instance_id)
    assert ok
    # Mana zerada após Tomo (custo 9). HP intacto.
    assert p.mana == 0
    assert p.hero_health == 30

    spell = next((c for c in p.hand if c.card_id == "ataque_de_niurau"), None)
    assert spell is not None
    ok, _ = engine.play_card(state, pid, spell.instance_id)
    assert ok
    # Mana continua 0 (não pagou mana). HP caiu pelo custo do feitiço (2).
    assert p.mana == 0
    assert p.hero_health == 28
