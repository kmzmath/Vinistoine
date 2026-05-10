"""Testes dos bugs de cartas e UI reportados no playtest da rodada 2.

- Cereja (DAMAGE em ALL_MINIONS_EXCEPT_TRIBE) realmente atinge inimigos.
- Nandinho concede STEALTH durando o turno do oponente.
- Blitz pede 1 alvo (excluded_target=CHOSEN) e poupa o escolhido.
- Baiano com tag CANT_ATTACK não pode atacar.
- Spiid 3 Anos: o desconto aparece no effective_cost da próxima COMIDA da mão.
- Igão Chave / Rica / Kiwi cobertos em test_card_bugs_playtest e
  test_lote23 (atualizado).
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


def _force_minion(state, pid, *, attack=1, health=1, max_health=None,
                  tags=None, ready=True, card_id="t", name="t",
                  tribes=None):
    m = Minion(
        instance_id=gen_id("m_"), card_id=card_id, name=name,
        attack=attack, health=health,
        max_health=max_health if max_health is not None else health,
        tags=list(tags or []), tribes=list(tribes or []),
        owner=pid, summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in (tags or []),
    )
    state.players[pid].board.append(m)
    return m


# ============ Cereja ============

def test_cereja_aoe_atinge_nao_comidas():
    """Bug: ALL_MINIONS_EXCEPT_TRIBE estava reaplicando 'tribe' como
    required_tribe, então NINGUÉM passava no _filter_minions."""
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid

    cereja = _force_minion(state, pid, attack=4, health=3, ready=True,
                            card_id="cereja", name="Cereja", tribes=["FRUTA"])
    not_food = _force_minion(state, foe, attack=2, health=3, ready=True,
                              card_id="vini_zumbi", name="Z", tribes=["VINI"])
    other_fruit = _force_minion(state, foe, attack=2, health=2, ready=True,
                                 card_id="banana", name="Banana", tribes=["FRUTA"])

    eff = {"action": "DAMAGE", "amount": 1,
           "target": {"mode": "ALL_MINIONS_EXCEPT_TRIBE", "tribe": "COMIDA"}}
    effects.resolve_effect(state, eff, pid, cereja, ctx={})
    engine.cleanup(state)

    # Não-comida sofre dano. Frutas (que são COMIDA derivada) não sofrem.
    assert not_food.health == 2, f"Z deveria ter 2 hp, tem {not_food.health}"
    assert other_fruit.health == 2, "Frutas não devem sofrer dano"
    # Cereja é FRUTA → COMIDA, não atinge a si mesma.
    assert cereja.health == 3


# ============ Nandinho ============

def test_nandinho_stealth_dura_pelo_turno_do_oponente():
    """Furtividade temporária de Nandinho deve sobreviver ao turno do
    oponente (semântica 'por 1 turno')."""
    state = _new_match()
    pid = state.current_player
    other = 1 - pid

    target = _force_minion(state, pid, attack=2, health=2, ready=True)

    eff = {"action": "ADD_TAG", "tag": "STEALTH",
           "duration": "UNTIL_OPPONENT_TURN_END",
           "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]}}
    effects.resolve_effect(state, eff, pid, None,
                            ctx={"chosen_target": target.instance_id})
    assert "STEALTH" in target.tags

    # Caster termina turno: stealth deve permanecer (passa para o turno do oponente).
    engine.end_turn(state, pid)
    assert "STEALTH" in target.tags, "Stealth não deveria sumir no fim do turno do caster"

    # Oponente termina o próprio turno: agora sim a stealth expira.
    engine.end_turn(state, other)
    assert "STEALTH" not in target.tags


# ============ Blitz ============

def test_blitz_pede_um_alvo():
    """Bug: Blitz não pedia alvo porque _chosen_targets_in_effect só
    olhava 'target', não 'excluded_target'."""
    card = get_card("blitz")
    descs = targeting.chosen_targets_for_card(card)
    assert len(descs) == 1, (
        f"Blitz deveria pedir 1 alvo, pediu {len(descs)}"
    )
    assert "ENEMY_MINION" in (descs[0].get("valid") or [])


def test_blitz_poupa_o_alvo_e_trava_os_demais():
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid

    spared = _force_minion(state, foe, attack=2, health=3, ready=True)
    other_enemy = _force_minion(state, foe, attack=2, health=3, ready=True)
    ally = _force_minion(state, pid, attack=2, health=3, ready=True)

    p = state.players[pid]
    p.hand.clear()
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="blitz"))
    p.mana = 10

    ok, msg = engine.play_card(state, pid, p.hand[0].instance_id,
                                chosen_target=spared.instance_id)
    assert ok, msg
    assert "ATTACK_LOCKED" not in spared.tags, "Alvo escolhido não deve ser travado"
    assert "ATTACK_LOCKED" in other_enemy.tags
    assert "ATTACK_LOCKED" in ally.tags


# ============ Baiano (CANT_ATTACK) ============

def test_baiano_cant_attack_nao_pode_atacar():
    state = _new_match()
    pid = state.current_player

    baiano = _force_minion(state, pid, attack=5, health=12, ready=True,
                            tags=["TAUNT", "CANT_ATTACK"])
    assert baiano.can_attack() is False


def test_minion_sem_cant_attack_pode_atacar():
    """Sanidade: a checagem CANT_ATTACK não vaza para outros lacaios."""
    state = _new_match()
    pid = state.current_player

    m = _force_minion(state, pid, attack=2, health=2, ready=True)
    assert m.can_attack() is True


# ============ Spiid 3 Anos: desconto visível no UI ============

def test_spiid_3_anos_desconto_visivel():
    """O custo exibido na mão (effective_cost via compute_displayed_cost)
    reflete a redução pendente para a próxima COMIDA."""
    state = _new_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="cereja"))

    # Sem modifier: custo display = base.
    cereja = p.hand[0]
    card = get_card("cereja")
    assert engine.compute_displayed_cost(state, p, cereja, card) == card["cost"]

    # Adiciona o modifier (como Spiid 3 Anos faria).
    state.pending_modifiers.append({
        "kind": "next_card_cost_reduction", "owner": pid, "amount": 1,
        "valid": ["CARD_WITH_TRIBE_COMIDA"], "expires_on": "end_of_turn",
        "consumed": False,
    })
    assert engine.compute_displayed_cost(state, p, cereja, card) == card["cost"] - 1
    # compute_dynamic_cost (usado por play_card) NÃO deve incluir o desconto
    # pra evitar pagamento duplo.
    assert engine.compute_dynamic_cost(state, p, cereja, card) == card["cost"]


def test_spiid_3_anos_paga_corretamente():
    """play_card aplica o desconto uma só vez ao pagar."""
    state = _new_match()
    pid = state.current_player
    p = state.players[pid]
    p.hand.clear()
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="spiid_3_anos"))
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="cereja"))
    p.mana = 10
    p.max_mana = 10

    ok, _ = engine.play_card(state, pid, p.hand[0].instance_id)
    assert ok
    cereja = next(c for c in p.hand if c.card_id == "cereja")
    mana_before = p.mana
    ok, _ = engine.play_card(state, pid, cereja.instance_id)
    assert ok
    # Custo original 4, com desconto 3.
    assert mana_before - p.mana == get_card("cereja")["cost"] - 1


# ============ Rica counts OTHERS only ============

def test_rica_health_buff_exclui_self():
    """Rica recebe +1 de vida apenas para cada OUTRO lacaio aliado."""
    state = _new_match()
    pid = state.current_player
    p = state.players[pid]

    _force_minion(state, pid, ready=True)
    _force_minion(state, pid, ready=True)
    p.hand.clear()
    p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="rica"))
    p.mana = 10

    ok, _ = engine.play_card(state, pid, p.hand[0].instance_id)
    assert ok
    rica = p.board[-1]
    base = get_card("rica")["health"]
    # 2 outros aliados, então +2 de vida (não +3 incluindo self).
    assert rica.max_health == base + 2


# ============ Gusnabito reveal logs cards ============

def test_gusnabito_logs_reveal_hand_edges():
    """O handler deve logar reveal_hand_edges com as cartas; o frontend
    consome esse evento."""
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid
    foe_p = state.players[foe]
    foe_p.hand.clear()
    foe_p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="cereja"))
    foe_p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="banana"))
    foe_p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id="rica"))

    eff = {"action": "REVEAL_LEFTMOST_AND_RIGHTMOST_HAND_CARDS",
           "target": {"mode": "OPPONENT_HAND"}}
    effects.resolve_effect(state, eff, pid, None, ctx={})

    log = [e for e in state.event_log if e.get("type") == "reveal_hand_edges"]
    assert log
    cards = log[-1].get("cards") or []
    ids = [c["card_id"] for c in cards]
    assert "cereja" in ids
    assert "rica" in ids
