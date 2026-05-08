"""
Testes para as ações implementadas no Lote 1.
Cada teste foca numa mecânica isolada — usa cartas reais do JSON quando
possível, ou monta um lacaio sintético se a carta de exemplo não está disponível.
"""
from __future__ import annotations
import pytest

from game import engine
from game.cards import all_cards, get_card
from game.state import (
    GameState, Minion, CardInHand, gen_id, MAX_HAND_SIZE, MAX_BOARD_SIZE,
)
from game import effects, targeting


# ============ helpers ============

def _new_blank_match(seed: int = 1, deck_card: str = "vini_zumbi"):
    """Cria um match com decks de 30 cópias da mesma carta. Retorna state pronto pra jogar."""
    state = engine.new_game("A", [deck_card]*30, "B", [deck_card]*30, seed=seed)
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


def _add_to_hand(state, pid, card_id):
    """Adiciona carta na mão do jogador. Retorna a CardInHand."""
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    state.players[pid].hand.append(ch)
    return ch


# ============ REDUCE_COST com NEXT_CARD_PLAYED_THIS_TURN ============

def test_reduce_cost_next_card_played():
    """A carta 'spiid_3_anos' (Spiid 3 Anos) reduz custo da próxima COMIDA em 1.
    Vamos disparar manualmente e verificar pending_modifiers."""
    state = _new_blank_match()
    pid = state.current_player

    # Aplica o efeito manualmente (simulando battlecry)
    eff = {
        "action": "REDUCE_COST",
        "amount": 1,
        "target": {
            "mode": "NEXT_CARD_PLAYED_THIS_TURN",
            "valid": ["CARD_WITH_TRIBE_COMIDA"],
        }
    }
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": None})

    # pending_modifiers deve ter um entry
    assert len(state.pending_modifiers) == 1
    pm = state.pending_modifiers[0]
    assert pm["kind"] == "next_card_cost_reduction"
    assert pm["owner"] == pid
    assert pm["amount"] == 1
    assert "CARD_WITH_TRIBE_COMIDA" in pm["valid"]


def test_reduce_cost_consumed_when_matching_card_played():
    """Pending reduction com filtro tribo COMIDA é consumido quando jogamos uma comida.
    Carta camarão (1 mana, tribo COMIDA): com a redução, custa 0 e some o pending."""
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]

    # Garante mana e mão
    p.mana = 5; p.max_mana = 5
    ch = _add_to_hand(state, pid, "camarao")  # custa 1, tribo COMIDA

    # Cria pending reduction de 1 para COMIDA
    state.pending_modifiers.append({
        "kind": "next_card_cost_reduction", "owner": pid, "amount": 1,
        "valid": ["CARD_WITH_TRIBE_COMIDA"], "expires_on": "end_of_turn",
        "consumed": False,
    })
    mana_before = p.mana

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    # Custo era 1 - 1 = 0, então mana não muda
    assert p.mana == mana_before, f"Esperava mana inalterada, deu {p.mana}"
    # Pending consumido
    assert all(not pm.get("consumed") for pm in state.pending_modifiers)
    assert len([pm for pm in state.pending_modifiers
                if pm["kind"] == "next_card_cost_reduction"]) == 0


def test_reduce_cost_does_not_apply_to_non_matching_card():
    """Pending reduction COMIDA não afeta SPELL ou outra tribo."""
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 5; p.max_mana = 5

    # Adiciona uma carta SPELL (troca_justa, 0 de custo)
    ch = _add_to_hand(state, pid, "troca_justa")
    state.pending_modifiers.append({
        "kind": "next_card_cost_reduction", "owner": pid, "amount": 1,
        "valid": ["CARD_WITH_TRIBE_COMIDA"], "expires_on": "end_of_turn",
        "consumed": False,
    })

    ok, _ = engine.play_card(state, pid, ch.instance_id)
    assert ok
    # Pending NÃO foi consumido
    assert len([pm for pm in state.pending_modifiers
                if pm["kind"] == "next_card_cost_reduction"]) == 1


# ============ RETURN_TO_HAND ============

def test_return_to_hand_devolve_lacaio():
    state = _new_blank_match()
    pid = state.current_player
    enemy_pid = 1 - pid
    m = _force_minion(state, enemy_pid, card_id="vini_zumbi", attack=2, health=3)
    hand_size_before = len(state.players[enemy_pid].hand)
    board_before = len(state.players[enemy_pid].board)

    eff = {"action": "RETURN_TO_HAND",
           "target": {"mode": "CHOSEN", "valid": ["ANY_MINION"]}}
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": m.instance_id})

    # Lacaio sumiu do campo
    assert len(state.players[enemy_pid].board) == board_before - 1
    # E voltou para mão do dono original
    assert len(state.players[enemy_pid].hand) == hand_size_before + 1
    # A carta na mão é a card_id correto
    assert state.players[enemy_pid].hand[-1].card_id == "vini_zumbi"


# ============ AWAKEN / BECOME_DORMANT ============

def test_become_dormant_e_awaken():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid)

    effects.resolve_effect(state, {"action": "BECOME_DORMANT",
                                    "target": {"mode": "SELF"}},
                           pid, m, {"chosen_target": None})
    assert "DORMANT" in m.tags
    assert m.cant_attack
    assert m.immune

    effects.resolve_effect(state, {"action": "AWAKEN",
                                    "target": {"mode": "SELF"}},
                           pid, m, {"chosen_target": None})
    assert "DORMANT" not in m.tags
    assert not m.cant_attack
    assert not m.immune
    assert m.summoning_sick


# ============ DRAW_MINION com preferred_tribe ============

def test_draw_minion_prefere_tribo():
    """Cria um deck misto e pede DRAW_MINION com preferred_tribe=COMIDA.
    Espera que pegue uma comida primeiro."""
    cards = all_cards()
    # Acha uma comida e algo que não é
    comida = next(c for c in cards if "COMIDA" in (c.get("tribes") or []))
    other = next(c for c in cards if c.get("type") == "MINION"
                 and "COMIDA" not in (c.get("tribes") or []) and c["id"] != "coin")

    deck = [other["id"]] * 5 + [comida["id"]] * 5

    state = engine.new_game("A", deck * 3, "B", [other["id"]] * 30, seed=1)
    engine.confirm_mulligan(state, 0, [])
    engine.confirm_mulligan(state, 1, [])
    pid = state.current_player
    p = state.players[pid]
    initial_hand = len(p.hand)

    eff = {"action": "DRAW_MINION", "amount": 1,
           "target": {"mode": "SELF_DECK", "preferred_tribe": "COMIDA"}}
    effects.resolve_effect(state, eff, pid, None, {})

    assert len(p.hand) == initial_hand + 1
    last = p.hand[-1]
    drawn_card = get_card(last.card_id)
    assert "COMIDA" in (drawn_card.get("tribes") or []), \
        f"esperava comida, comprou {drawn_card['name']}"


# ============ DRAW_FROM_OPPONENT_DECK ============

def test_draw_from_opponent_deck():
    state = _new_blank_match()
    pid = state.current_player
    me = state.players[pid]
    opp = state.opponent_of(pid)
    opp_deck_before = len(opp.deck)
    me_hand_before = len(me.hand)
    opp_top_card = opp.deck[0]  # vai pra minha mão

    eff = {"action": "DRAW_FROM_OPPONENT_DECK", "amount": 1,
           "target": {"mode": "OPPONENT_DECK"}}
    effects.resolve_effect(state, eff, pid, None, {})

    assert len(opp.deck) == opp_deck_before - 1
    assert len(me.hand) == me_hand_before + 1
    assert me.hand[-1].card_id == opp_top_card


# ============ DAMAGE_ADJACENT_MINIONS ============

def test_damage_adjacent_minions():
    state = _new_blank_match()
    pid = state.current_player
    enemy_pid = 1 - pid
    a = _force_minion(state, enemy_pid, attack=1, health=5, name="A")
    b = _force_minion(state, enemy_pid, attack=1, health=5, name="B")  # alvo central
    c = _force_minion(state, enemy_pid, attack=1, health=5, name="C")

    # Aplica DAMAGE_ADJACENT_MINIONS com chosen=B (B é o pivô)
    eff = {"action": "DAMAGE_ADJACENT_MINIONS", "amount": 2,
           "target": {"mode": "ADJACENT_TO_PREVIOUS_TARGET"}}
    effects.resolve_effect(state, eff, pid, None,
                           {"chosen_target": b.instance_id})

    # A e C tomaram 2; B intacto
    assert a.health == 3
    assert b.health == 5
    assert c.health == 3


# ============ SET_STATS ============

def test_set_stats():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, attack=5, health=5)
    eff = {"action": "SET_STATS", "attack": 0, "health": 1,
           "target": {"mode": "CHOSEN", "valid": ["MINION"]}}
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": m.instance_id})
    assert m.attack == 0
    assert m.health == 1
    assert m.max_health == 1


# ============ ADD_TRIBE ============

def test_add_tribe():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid)
    eff = {"action": "ADD_TRIBE", "tribe": "COMIDA",
           "target": {"mode": "SELF"}}
    effects.resolve_effect(state, eff, pid, m, {})
    assert "COMIDA" in m.tribes


# ============ ADD_COPY_TO_DECK ============

def test_add_copy_to_deck():
    state = _new_blank_match()
    pid = state.current_player
    m = _force_minion(state, pid, card_id="camarao")
    deck_before = len(state.players[pid].deck)
    eff = {"action": "ADD_COPY_TO_DECK", "amount": 2,
           "target": {"mode": "SELF"}}
    effects.resolve_effect(state, eff, pid, m, {})
    assert len(state.players[pid].deck) == deck_before + 2
    assert state.players[pid].deck.count("camarao") >= 2


# ============ RESURRECT ============

def test_resurrect_last_friendly_dead_minion():
    state = _new_blank_match()
    pid = state.current_player
    # Simula que houve uma morte aliada
    state.graveyard.append({"card_id": "vini_zumbi", "owner": pid,
                            "name": "Vini Zumbi"})
    board_before = len(state.players[pid].board)

    eff = {"action": "RESURRECT_LAST_FRIENDLY_DEAD_MINION",
           "target": {"mode": "SELF"}}
    effects.resolve_effect(state, eff, pid, None, {})

    assert len(state.players[pid].board) == board_before + 1
    new_m = state.players[pid].board[-1]
    assert new_m.card_id == "vini_zumbi"


# ============ ADD_MODIFIED_COPY_TO_HAND ============

def test_add_modified_copy_to_hand():
    """Capataz: adiciona à mão uma cópia 1/1 que custa 1 do lacaio escolhido."""
    state = _new_blank_match()
    pid = state.current_player
    # Lacaio com stats originais 5/5 e custo 5
    m = _force_minion(state, pid, card_id="vini_zumbi", attack=5, health=5)
    base = get_card("vini_zumbi")  # tem cost real

    hand_before = len(state.players[pid].hand)
    eff = {"action": "ADD_MODIFIED_COPY_TO_HAND",
           "copy_modifiers": {"cost": 1, "attack": 1, "health": 1},
           "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]}}
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": m.instance_id})

    assert len(state.players[pid].hand) == hand_before + 1
    new_card = state.players[pid].hand[-1]
    assert new_card.card_id == "vini_zumbi"
    assert new_card.cost_override == 1
    # Stat modifier deve resultar em 1/1 quando jogada
    expected_atk_mod = 1 - (base.get("attack") or 0)
    expected_hp_mod = 1 - (base.get("health") or 0)
    assert new_card.stat_modifier.get("attack") == expected_atk_mod
    assert new_card.stat_modifier.get("health") == expected_hp_mod


# ============ Stat modifier aplicado ao invocar ============

def test_card_with_stat_modifier_summons_with_modified_stats():
    state = _new_blank_match()
    pid = state.current_player
    p = state.players[pid]
    p.mana = 10; p.max_mana = 10

    base = get_card("vini_zumbi")
    base_atk = base["attack"]  # 2
    base_hp = base["health"]   # 3

    # Adiciona uma cópia "1/1 que custa 1"
    ch = CardInHand(instance_id=gen_id("h_"), card_id="vini_zumbi",
                     cost_override=1,
                     stat_modifier={"attack": 1 - base_atk, "health": 1 - base_hp})
    p.hand.append(ch)

    ok, msg = engine.play_card(state, pid, ch.instance_id)
    assert ok, msg
    # O lacaio entrou em campo com 1/1 (não 2/3)
    new_m = p.board[-1]
    assert new_m.attack == 1
    assert new_m.health == 1


# ============ APPLY_START_OF_TURN_DAMAGE_STATUS ============

def test_apply_sot_damage_status_dispara_no_inicio_do_turno():
    state = _new_blank_match()
    pid = state.current_player
    enemy_pid = 1 - pid
    m = _force_minion(state, enemy_pid, attack=2, health=5)
    initial_hp = m.health

    # Aplica status: 1 de dano por turno do dono
    eff = {"action": "APPLY_START_OF_TURN_DAMAGE_STATUS", "amount": 1,
           "target": {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}}
    effects.resolve_effect(state, eff, pid, None, {"chosen_target": m.instance_id})

    assert any(pm["kind"] == "minion_sot_damage" and pm["minion_id"] == m.instance_id
               for pm in state.pending_modifiers)

    # Termina turno do pid; entra turno do enemy_pid → SoT damage dispara
    engine.end_turn(state, pid)
    # Agora é turno do enemy. start_turn rodou. m deve ter perdido 1 HP.
    f = state.find_minion(m.instance_id)
    assert f is not None, "Lacaio deveria ainda estar vivo"
    m_after = f[0]
    assert m_after.health == initial_hp - 1
