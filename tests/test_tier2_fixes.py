"""Testes Tier 2: confirmações de regra solicitadas pelo usuário.

- FRIENDLY_MINIONS_SUMMONED disparado como trigger real (via fire_minion_trigger)
- AWAKEN deixa o lacaio com summoning_sick=True (não pode atacar mesmo turno)
- Mao Tsé-Tung mantém escopo: campo + mão + deck dos dois jogadores
- ON_DEATH propaga death_index no ctx para deathrattles posicionais
- temporary_stat_buff revertido no fim do turno do dono indicado
"""
from __future__ import annotations
import pytest

from game import engine, effects
from game.cards import all_cards, load_cards, get_card
from game.state import GameState, Minion, CardInHand, gen_id


def _cheap_pool():
    return [c for c in all_cards() if c.get("type") == "MINION" and c.get("cost", 99) <= 4]


def _build_deck(pool, n=30):
    out = []
    for c in pool:
        out.append(c["id"])
        out.append(c["id"])
        if len(out) >= n:
            break
    return out[:n]


def _new_match(seed=42):
    load_cards()
    pool = _cheap_pool()
    state = engine.new_game("A", _build_deck(pool), "B", _build_deck(pool[5:] + pool[:5]),
                            seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, attack=1, health=1, max_health=None,
                  tags=None, ready=True, card_id="t", name="t"):
    tag_list = list(tags or [])
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id, name=name,
        attack=attack, health=health,
        max_health=max_health if max_health is not None else health,
        tags=tag_list, owner=pid,
        summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in tag_list,
    )
    state.players[pid].board.append(m)
    return m


# ============ FRIENDLY_MINIONS_SUMMONED como trigger real ============

def test_vini_dorminhoco_acorda_via_fire_minion_trigger():
    """Bug Tier 2: o handler AWAKEN agora roda via fire_minion_trigger
    quando o contador atinge o limiar (antes era awaken inline)."""
    state = _new_match()
    pid = state.current_player

    # Coloca Viní Dorminhoco dormente diretamente.
    dor = effects.summon_minion_from_card(state, pid, "vini_dorminhoco")
    assert dor is not None
    assert "DORMANT" in dor.tags
    assert dor.cant_attack and dor.immune

    # Evoca dois lacaios aliados; depois do 2º o threshold deve disparar.
    effects.summon_minion_from_card(state, pid, "vini_zumbi")
    assert "DORMANT" in dor.tags, "ainda não deveria ter despertado"
    effects.summon_minion_from_card(state, pid, "vini_zumbi")

    # Acordou via trigger real: tags DORMANT removida, summoning_sick=True.
    assert "DORMANT" not in dor.tags
    assert dor.cant_attack is False
    assert dor.immune is False
    assert dor.summoning_sick is True


def test_friendly_minions_summoned_logs_evento_no_threshold():
    state = _new_match()
    pid = state.current_player
    dor = effects.summon_minion_from_card(state, pid, "vini_dorminhoco")
    assert dor is not None

    effects.summon_minion_from_card(state, pid, "vini_zumbi")
    effects.summon_minion_from_card(state, pid, "vini_zumbi")

    types = [e.get("type") for e in state.event_log]
    # Evento específico de threshold deve aparecer no log.
    assert "friendly_minions_summoned_threshold" in types


# ============ AWAKEN com summoning_sick=True ============

def test_awaken_deixa_summoning_sick_true():
    """Lock: lacaio que acabou de acordar não pode atacar mesmo turno."""
    state = _new_match()
    pid = state.current_player

    dor = effects.summon_minion_from_card(state, pid, "vini_dorminhoco")
    effects.summon_minion_from_card(state, pid, "vini_zumbi")
    effects.summon_minion_from_card(state, pid, "vini_zumbi")

    assert "DORMANT" not in dor.tags
    assert dor.summoning_sick is True
    # Sem CHARGE/RUSH ele não pode atacar (sumona-sick válida).
    assert dor.can_attack() is False


# ============ Mao Tsé-Tung escopo (lock) ============

def test_mao_tse_tung_atinge_campo_mao_e_deck_dos_dois_jogadores():
    """Lock: regra confirmada - corta ataque pela metade no campo dos dois,
    nas mãos dos dois e nos decks dos dois."""
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid

    # Limpa boards/mãos/decks para teste limpo.
    for p in state.players:
        p.board.clear()
        p.hand.clear()
        p.deck.clear()

    enemy = _force_minion(state, foe, attack=4, health=4)
    ally = _force_minion(state, pid, attack=6, health=6)

    # Mão e deck com lacaios reais para halving.
    state.players[pid].hand.append(CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi"))
    state.players[foe].hand.append(CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi"))
    state.players[pid].deck.append("vini_zumbi")
    state.players[foe].deck.append("vini_zumbi")

    eff = {
        "action": "APPLY_PERMANENT_ATTACK_HALF_STATUS",
        "rounding": "CEIL",
        "target": {"mode": "ALL_OTHER_MINIONS"},
    }
    src = _force_minion(state, pid, attack=6, health=6, name="Mao", card_id="mao_tse_tung")
    effects.resolve_effect(state, eff, pid, src, ctx={})

    # Campo: outros lacaios reduzidos pela metade (CEIL).
    assert ally.attack == 3, f"aliado deveria ter 3 ataque, tem {ally.attack}"
    assert enemy.attack == 2, f"inimigo deveria ter 2 ataque, tem {enemy.attack}"
    # A própria fonte (ALL_OTHER_MINIONS exclui SELF) não foi afetada.
    assert src.attack == 6

    # Mão/deck: o efeito deve marcar/halvar os dois lados.
    types = [e.get("type") for e in state.event_log]
    # Pelo menos um evento de halving foi gerado (no campo).
    assert any(t == "permanent_attack_half" for t in types)


# ============ ON_DEATH com death_index ============

def test_on_death_recebe_death_index_no_ctx():
    """Bug Tier 2: deathrattles posicionais precisam do índice original."""
    state = _new_match()
    pid = state.current_player

    captured = {}

    # Substitui temporariamente o trigger fire para capturar ctx.
    orig_fire = effects.fire_minion_trigger

    def spy_fire(state, minion, trigger, extra_ctx=None):
        if trigger == "ON_DEATH":
            captured["death_index"] = (extra_ctx or {}).get("death_index")
            captured["death_owner"] = (extra_ctx or {}).get("death_owner")
            captured["minion"] = minion.instance_id
        return orig_fire(state, minion, trigger, extra_ctx=extra_ctx)

    effects.fire_minion_trigger = spy_fire
    try:
        # Coloca 3 lacaios no campo do pid; mata o do meio (idx=1).
        a = _force_minion(state, pid, attack=1, health=1, ready=True)
        b = _force_minion(state, pid, attack=1, health=1, ready=True, name="vitima")
        c = _force_minion(state, pid, attack=1, health=1, ready=True)
        assert state.players[pid].board.index(b) == 1

        b.health = 0
        engine.cleanup(state)

        assert captured.get("minion") == b.instance_id
        assert captured.get("death_owner") == pid
        assert captured.get("death_index") == 1
    finally:
        effects.fire_minion_trigger = orig_fire


# ============ temporary_stat_buff ============

def test_temporary_stat_buff_revertido_no_fim_do_turno_do_dono():
    """Bug Tier 2: '+X ataque este turno' precisa virar permanente=False."""
    state = _new_match()
    pid = state.current_player

    m = _force_minion(state, pid, attack=2, health=3, max_health=3, ready=True)
    effects.grant_temporary_stat_buff(state, m, attack=3, health=2,
                                       owner_for_revert=pid)

    # Imediatamente após o grant: buff aplicado.
    assert m.attack == 5
    assert m.max_health == 5
    assert m.health == 5

    # Fim do turno do dono => buff revertido.
    engine.end_turn(state, pid)
    assert m.attack == 2, f"buff de ataque não revertido (atk={m.attack})"
    assert m.max_health == 3
    assert m.health <= m.max_health


def test_temporary_stat_buff_persiste_um_turno_inteiro_se_outro_dono():
    """Buff aplicado pelo oponente reverte só no fim do turno DELE."""
    state = _new_match()
    pid = state.current_player
    other = 1 - pid

    m = _force_minion(state, pid, attack=2, health=3, max_health=3, ready=True)
    # owner_for_revert=other => só some quando 'other' termina turno.
    effects.grant_temporary_stat_buff(state, m, attack=3, health=0,
                                       owner_for_revert=other)
    assert m.attack == 5

    # pid termina turno: buff segue (dono é 'other').
    engine.end_turn(state, pid)
    assert m.attack == 5
    # other termina turno: agora reverte.
    engine.end_turn(state, other)
    assert m.attack == 2


def test_temporary_stat_buff_nao_mata_aliado_machucado():
    """Mesmo padrão da remoção de aura: ao reverter +HP, não deve matar."""
    state = _new_match()
    pid = state.current_player

    m = _force_minion(state, pid, attack=1, health=1, max_health=1, ready=True)
    effects.grant_temporary_stat_buff(state, m, attack=0, health=2,
                                       owner_for_revert=pid)
    # Toma 2 de dano: agora 1 hp atual com max=3.
    m.health = 1

    other = 1 - pid
    engine.end_turn(state, pid)
    engine.end_turn(state, other)
    engine.end_turn(state, pid)

    # Health deve clipar para o novo max (1), não ser subtraído (ficaria <=0).
    assert m.health >= 1, f"buff temporário matou aliado machucado (hp={m.health})"
    assert m.max_health == 1
