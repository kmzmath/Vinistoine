"""Testes que fixam os bugs encontrados na auditoria geral.

Cada bloco aqui carrega regressões reais identificadas em `engine.py`,
`state.py`, `targeting.py`, `effects.py` e nos schemas Pydantic do servidor.
"""
from __future__ import annotations
import pytest

from game import engine
from game import targeting
from game.cards import all_cards, load_cards
from game.state import (
    GameState, Minion, PlayerState, MAX_BOARD_SIZE, gen_id,
)


# ===== helpers minimal =====

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
    load_cards()
    pool = _cheap_minion_pool()
    deck_a = _build_deck(pool)
    deck_b = _build_deck(pool[5:] + pool[:5])
    state = engine.new_game("Alice", deck_a, "Bob", deck_b, seed=seed)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    return state


def _force_minion(state, pid, *, attack=1, health=1, max_health=None,
                  tags=None, ready=True):
    tag_list = list(tags or [])
    m = Minion(
        instance_id=gen_id("m_"),
        card_id="t",
        name="t",
        attack=attack,
        health=health,
        max_health=max_health if max_health is not None else health,
        tags=tag_list,
        owner=pid,
        summoning_sick=not ready,
        divine_shield="DIVINE_SHIELD" in tag_list,
    )
    state.players[pid].board.append(m)
    return m


# ===== state.py: has_tag e DORMANT vs silenced =====

def test_silenced_dormant_minion_continua_dormente():
    """Bug auditado: silence apagava DORMANT em has_tag, criando zumbi targetável."""
    m = Minion(instance_id="x", card_id="c", name="C", attack=1, health=1,
               max_health=1, tags=["DORMANT"])
    m.silenced = True

    # Silenciado mas DORMANT precede silence: ainda é dormente.
    assert m.has_tag("DORMANT") is True
    # Outras tags continuam mascaradas pelo silence.
    m.tags.append("TAUNT")
    assert m.has_tag("TAUNT") is False


def test_to_dict_inclui_hero_attacks_e_fatigue():
    """Bug auditado: cliente não conseguia mostrar herói exausto / fadiga."""
    p = PlayerState(player_id=0, name="A")
    p.hero_attacks_this_turn = 1
    p.fatigue_counter = 3
    d = p.to_dict()
    assert d["hero_attacks_this_turn"] == 1
    assert d["fatigue_counter"] == 3


# ===== targeting.py: filtros nos modos colectivos =====

def test_minions_with_tribe_exclui_dormente():
    """Bug auditado: MINIONS_WITH_TRIBE não passava por _filter_minions."""
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid

    _force_minion(state, foe, tags=["DORMANT"]).tribes = ["FRUTA"]
    _force_minion(state, foe).tribes = ["FRUTA"]

    pool = targeting.resolve_targets(state, {"mode": "MINIONS_WITH_TRIBE",
                                              "tribe": "FRUTA"}, pid)
    assert len(pool) == 1
    assert not pool[0].has_tag("DORMANT")


def test_damaged_minion_sem_id_nao_inclui_dormente():
    """Bug auditado: DAMAGED_MINION fallback ignorava _filter_minions."""
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid

    a = _force_minion(state, foe, health=1, max_health=3)
    b = _force_minion(state, foe, health=2, max_health=3, tags=["DORMANT"])

    pool = targeting.resolve_targets(state, {"mode": "DAMAGED_MINION"}, pid)
    assert a in pool
    assert b not in pool


def test_random_enemy_character_ignora_heroi_imune():
    """Bug auditado: alvo aleatório podia 'queimar' no herói imune."""
    state = _new_match()
    pid = state.current_player
    foe_p = state.opponent_of(pid)

    foe_p.hero_immune = True
    # único minion sem dormant e sem stealth
    m = _force_minion(state, foe_p.player_id, attack=1, health=1)

    pool = targeting.resolve_targets(state, {"mode": "RANDOM_ENEMY_CHARACTER"}, pid)
    assert pool == [m]


# ===== engine.py: freeze dura UM turno =====

def test_freeze_dura_apenas_um_turno_do_dono():
    """Bug auditado: lacaio congelado pelo oponente perdia DOIS turnos."""
    state = _new_match()
    caster = state.current_player
    owner = 1 - caster

    m = _force_minion(state, owner, attack=2, health=5, ready=True)

    # Caster congela durante o próprio turno.
    m.frozen = True
    m.freeze_pending = True

    # Caster termina turno -> deve marcar freeze_pending=False (amadurece).
    engine.end_turn(state, caster)
    assert m.frozen is True
    assert m.freeze_pending is False

    # Owner termina o próprio turno -> deve descongelar (perdeu 1 turno).
    engine.end_turn(state, owner)
    assert m.frozen is False, (
        "Lacaio deveria descongelar após perder um turno, não dois"
    )


# ===== engine.py: provocar imune não trava ataques =====

def test_provocar_imune_nao_trava_ataque():
    """Bug auditado: ataque travava quando todos os taunts eram imunes."""
    state = _new_match()
    pid = state.current_player
    foe = 1 - pid

    attacker = _force_minion(state, pid, attack=2, health=2, ready=True)
    foe_p = state.opponent_of(pid)
    foe_p.hero_immune = False

    # provocar imune não deve impedir ataque ao herói
    immune_taunt = _force_minion(state, foe, attack=1, health=10,
                                  tags=["TAUNT"], ready=True)
    immune_taunt.immune = True

    legal = engine.list_legal_attack_targets(state, pid, attacker.instance_id)
    assert f"hero:{foe}" in legal

    ok, _ = engine.attack(state, pid, attacker.instance_id, f"hero:{foe}")
    assert ok is True


# ===== engine.py: aura +HP removida não mata aliado machucado =====

def test_aura_health_removida_nao_mata_aliado_machucado():
    """Bug auditado: ao remover aura de +HP, health era subtraído cegamente."""
    state = _new_match()
    pid = state.current_player

    target = _force_minion(state, pid, attack=1, health=1, max_health=1)
    # Simula aplicação da aura: +0/+2 de outra fonte.
    target.tags.append("_AURA_STAT:src1:0:2")
    target.max_health = 3
    target.health = 3
    # Sofre 2 de dano: 3 -> 1 hp atual, max=3.
    target.health = 1

    # Fonte morre: o helper interno é chamado dentro de apply_continuous_effects,
    # mas podemos invocar a remoção via aplicação completa (sem nova aura ativa).
    engine.apply_continuous_effects(state)

    # Após remoção: max=1, health não deve ter ficado <= 0.
    assert target.health >= 1, (
        f"Aliado machucado não deveria morrer ao remover aura de +HP "
        f"(ficou com health={target.health})"
    )
    assert target.max_health == 1


# ===== Pydantic: limite de tamanho dos campos =====

def test_pydantic_recusa_campos_gigantes():
    pytest.importorskip("sqlalchemy")
    from server.main import RegisterIn, DeckIn

    # Senha de 1MB deve ser recusada (limite 128).
    with pytest.raises(Exception):
        RegisterIn(nickname="a", password="x" * 200_000)

    # Nickname acima do limite.
    with pytest.raises(Exception):
        RegisterIn(nickname="a" * 200, password="senha")

    # Deck com tamanho absurdo deve ser recusado pelo conlist antes mesmo
    # da validação de regras.
    with pytest.raises(Exception):
        DeckIn(name="x", cards=["vini_zumbi"] * 1000)


# ===== effects.py: ADD_CARD_TO_HAND aceita card_id top-level =====

def test_add_card_to_hand_aceita_card_id_top_level():
    """Bug auditado: original lia só eff['card']['id'], lotes posteriores
    padronizaram para card_id. Sem fallback, cartas como Cardume falhariam
    se a ordem de registro mudasse."""
    from game import effects

    state = _new_match()
    pid = state.current_player
    p = state.players[pid]
    initial = len(p.hand)

    eff = {"action": "ADD_CARD_TO_HAND", "card_id": "coin", "amount": 1,
           "target": {"mode": "SELF_PLAYER"}}
    effects.resolve_effect(state, eff, pid, source_minion=None, ctx={})

    assert len(p.hand) == initial + 1
    assert p.hand[-1].card_id == "coin"


# ===== effects.py: registro guarda histórico =====

def test_handler_registrations_guardam_historico():
    from game import effects
    # APPLY_PERMANENT_ATTACK_HALF_STATUS é registrado em pelo menos 2 lotes.
    history = effects.HANDLER_REGISTRATIONS.get("APPLY_PERMANENT_ATTACK_HALF_STATUS")
    assert history is not None and len(history) >= 2, (
        "Esperava ver overrides registrados para auditoria"
    )


# ===== server: WS aceita mesma-origem em qualquer host =====

def test_ws_origin_aceita_mesma_origem():
    """Regressão: a checagem de Origin não pode bloquear deploy quando o
    domínio não está em ALLOWED_CORS_ORIGINS - basta Origin == Host."""
    pytest.importorskip("sqlalchemy")
    from server.main import _is_origin_allowed

    class _FakeWS:
        def __init__(self, headers):
            self.headers = headers

    # Render-like: domínio fora de ALLOWED_CORS_ORIGINS, mas Origin == Host.
    ws = _FakeWS({
        "origin": "https://vinistoine.onrender.com",
        "host": "vinistoine.onrender.com",
    })
    assert _is_origin_allowed(ws) is True

    # Sem Origin (curl/teste): aceita.
    ws = _FakeWS({"host": "vinistoine.onrender.com"})
    assert _is_origin_allowed(ws) is True

    # Cross-origin com host diferente do listado: nega.
    ws = _FakeWS({
        "origin": "https://attacker.com",
        "host": "vinistoine.onrender.com",
    })
    assert _is_origin_allowed(ws) is False

    # localhost dev: porta padrão coincide com Host.
    ws = _FakeWS({
        "origin": "http://localhost:8000",
        "host": "localhost:8000",
    })
    assert _is_origin_allowed(ws) is True
