"""
Engine principal do jogo. Encapsula o fluxo das partidas, valida ações dos jogadores
e dispara efeitos. Esta é a "fonte da verdade" do estado de cada partida.
"""
from __future__ import annotations
import random
from typing import Optional
from .state import (
    GameState, PlayerState, Minion, CardInHand, gen_id,
    STARTING_HEALTH, MAX_MANA, MAX_HAND_SIZE, MAX_BOARD_SIZE, DECK_SIZE,
    STARTING_HAND_FIRST, STARTING_HAND_SECOND,
)
from .cards import get_card, load_cards
from . import effects
from . import targeting


def new_game(player_a_name: str, deck_a: list[str],
             player_b_name: str, deck_b: list[str],
             seed: Optional[int] = None,
             manual_choices: bool = False) -> GameState:
    """Cria um novo jogo. deck_a e deck_b são listas de card_ids (já validadas).

    manual_choices=True habilita escolhas pendentes reais para partidas via
    servidor. Testes unitários podem manter False para usar fallbacks determinísticos.
    """
    load_cards()
    rng = random.Random(seed)

    # decide quem começa
    first = rng.randint(0, 1)

    p0 = PlayerState(player_id=0, name=player_a_name, deck=list(deck_a))
    p1 = PlayerState(player_id=1, name=player_b_name, deck=list(deck_b))
    rng.shuffle(p0.deck)
    rng.shuffle(p1.deck)

    state = GameState(
        game_id=gen_id("g_"),
        players=[p0, p1],
        current_player=first,
        turn_number=0,
        rng=rng,
        phase="MULLIGAN",
        manual_choices=manual_choices,
    )

    # mão inicial: primeiro jogador 3 cartas, segundo 4 cartas + a Moeda no início do jogo (turno 1)
    n_first = STARTING_HAND_FIRST
    n_second = STARTING_HAND_SECOND
    for _ in range(n_first):
        if state.players[first].deck:
            cid = state.players[first].deck.pop(0)
            state.players[first].hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
    second = 1 - first
    for _ in range(n_second):
        if state.players[second].deck:
            cid = state.players[second].deck.pop(0)
            state.players[second].hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))

    state.log_event({"type": "game_start", "first_player": first})
    return state


def confirm_mulligan(state: GameState, player_id: int, swap_instance_ids: list[str]):
    """Troca cartas escolhidas no mulligan. Após ambos confirmarem, o jogo começa."""
    if state.phase != "MULLIGAN":
        return
    if state.mulligan_done[player_id]:
        return
    p = state.players[player_id]
    # remove as escolhidas e devolve ao deck
    swapped: list[CardInHand] = []
    new_hand: list[CardInHand] = []
    for c in p.hand:
        if c.instance_id in swap_instance_ids:
            swapped.append(c)
        else:
            new_hand.append(c)
    # adiciona swapped ao deck e embaralha
    for c in swapped:
        p.deck.append(c.card_id)
    state.rng.shuffle(p.deck)
    # compra novas
    for _ in range(len(swapped)):
        if p.deck:
            cid = p.deck.pop(0)
            new_hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
    p.hand = new_hand
    state.mulligan_done[player_id] = True
    state.log_event({"type": "mulligan_done", "player": player_id, "swapped": len(swapped)})

    if all(state.mulligan_done):
        # dá a Moeda pro segundo jogador e começa o turno 1
        second = 1 - state.current_player
        if len(state.players[second].hand) < MAX_HAND_SIZE:
            state.players[second].hand.append(CardInHand(instance_id=gen_id("h_"), card_id="coin"))
        start_turn(state)


def start_turn(state: GameState):
    state.phase = "PLAYING"
    state.turn_number += 1
    p = state.players[state.current_player]

    # Limpa modificadores pendentes que expiraram (do turno anterior)
    # Modificadores com expires_on=end_of_turn sempre são do turno do dono
    state.pending_modifiers = [
        pm for pm in state.pending_modifiers
        if not (pm.get("expires_on") == "end_of_turn"
                and pm.get("owner") == state.current_player)
    ]

    # ganha mana
    p.max_mana = min(MAX_MANA, p.max_mana + 1)
    p.mana = p.max_mana

    # Ativa penalidades/benefícios programados para este turno.
    _activate_next_turn_modifiers(state, p.player_id)

    # Funkeiro: reduz a mana disponível apenas neste turno, sem reduzir
    # permanentemente o máximo de cristais.
    keep_modifiers = []
    for pm in state.pending_modifiers:
        if pm.get("kind") == "reduce_mana_this_turn" and pm.get("owner") == p.player_id:
            amount = int(pm.get("amount", 0) or 0)
            p.mana = max(0, p.mana - amount)
            state.log_event({"type": "mana_reduced_this_turn", "player": p.player_id, "amount": amount})
            continue
        keep_modifiers.append(pm)
    state.pending_modifiers = keep_modifiers

    # AliExpress: cartas compradas com atraso chegam no início do próximo turno.
    _deliver_delayed_draws(state, p)

    # Reset de contadores e summoning sickness.
    # IMPORTANTE: NÃO descongela aqui - o freeze é uma penalidade de "perder
    # 1 turno". Lacaio congelado tem o turno desperdiçado; descongela no FIM
    # do turno (em end_turn), porque aí já provou que não atacou.
    for m in p.board:
        m.attacks_this_turn = 0
        m.activated_abilities_this_turn = 0
        m.summoning_sick = False
    p.hero_attacks_this_turn = 0
    p.cards_played_this_turn = 0

    # Dormente por turnos: conta somente no começo dos turnos do dono.
    dormant_keep = []
    for pm in list(state.pending_modifiers):
        if pm.get("kind") != "dormant_turns":
            dormant_keep.append(pm)
            continue
        if pm.get("owner") != state.current_player:
            dormant_keep.append(pm)
            continue
        found = state.find_minion(pm.get("minion_id"))
        if not found:
            continue
        m = found[0]
        remaining = int(pm.get("turns_remaining", 0) or 0) - 1
        if remaining <= 0:
            try:
                from .effects_lote25_requested_fixes import wake_dormant_minion
                wake_dormant_minion(state, m)
            except Exception:
                if "DORMANT" in m.tags:
                    m.tags.remove("DORMANT")
                m.cant_attack = False
                m.immune = False
                m.summoning_sick = True
            continue
        pm["turns_remaining"] = remaining
        dormant_keep.append(pm)
    state.pending_modifiers = dormant_keep

    # Aplica APPLY_START_OF_TURN_DAMAGE_STATUS (debuff de dano contínuo)
    sot_to_remove = []
    for pm in list(state.pending_modifiers):
        kind = pm.get("kind")
        timing = pm.get("timing", "START_OF_TARGET_TURN")
        from .effects import damage_character

        if kind == "minion_sot_damage":
            mid = pm.get("minion_id")
            f = state.find_minion(mid)
            if f is None:
                sot_to_remove.append(pm)  # lacaio morreu - limpa
                continue
            m, owner_pid = f
            if timing == "START_OF_EACH_TURN":
                should_trigger = True
            else:
                should_trigger = owner_pid == state.current_player
            if not should_trigger:
                continue
            damage_character(state, m, pm.get("amount", 1), state.current_player, None,
                             is_spell=False)

        elif kind == "hero_sot_damage":
            target_pid = pm.get("player_id")
            if target_pid is None:
                sot_to_remove.append(pm)
                continue
            if timing == "START_OF_EACH_TURN":
                should_trigger = True
            else:
                should_trigger = int(target_pid) == state.current_player
            if not should_trigger:
                continue
            damage_character(state, state.players[int(target_pid)], pm.get("amount", 1),
                             state.current_player, None, is_spell=False)

    state.pending_modifiers = [pm for pm in state.pending_modifiers if pm not in sot_to_remove]

    # Dispara ON_TURN_START dos lacaios ANTES da compra, pois Portal precisa
    # registrar o "pending: substituir compra por play_top_card" antes de
    # acontecer a compra.
    for m in list(p.board):
        effects.fire_minion_trigger(state, m, "START_OF_TURN")
        effects.fire_minion_trigger(state, m, "ON_TURN_START")

    # === Portal: substitui a compra pelo "jogar carta do topo" ===
    replace_pending = next((pm for pm in state.pending_modifiers
                            if pm.get("kind") == "replace_next_draw_with_play"
                            and pm.get("owner") == p.player_id
                            and not pm.get("consumed")), None)
    if replace_pending:
        replace_pending["consumed"] = True
        if p.deck:
            cid = p.deck.pop(0)
            card = effects.get_card(cid) if hasattr(effects, "get_card") else None
            if card is None:
                from .cards import get_card as _gc
                card = _gc(cid)
            if card is not None:
                from .effects_lote3_familia1 import _play_card_free
                _play_card_free(state, p.player_id, card)
                state.log_event({
                    "type": "play_top_instead_of_draw", "card_id": cid,
                })
    else:
        # compra carta normalmente. Marca o motivo para a UI usar animação lenta
        # só na compra automática de início do turno.
        state._draw_reason = "turn_start"
        effects.draw_card(state, p, 1)
        state._draw_reason = None

    state.log_event({"type": "turn_start", "player": p.player_id, "turn": state.turn_number})

    cleanup(state)



def _has_active_freeze_until_source(state: GameState, target_id: str) -> bool:
    """True se o alvo está congelado por uma fonte ainda viva."""
    keep = []
    active = False
    for pm in state.pending_modifiers:
        if pm.get("kind") != "freeze_until_source_dies":
            keep.append(pm)
            continue
        target_found = state.find_minion(pm.get("target_id"))
        source_found = state.find_minion(pm.get("source_minion_id"))
        if target_found is None:
            # alvo morreu/saiu; remove modifier
            continue
        if source_found is None:
            # fonte morreu/saiu; descongela alvo agora
            target = target_found[0]
            target.frozen = False
            target.freeze_pending = False
            state.log_event({"type": "freeze_until_source_released",
                             "target": target.instance_id,
                             "source": pm.get("source_minion_id")})
            continue
        if pm.get("target_id") == target_id:
            active = True
        keep.append(pm)
    state.pending_modifiers = keep
    return active


def _advance_attack_locks_for_player(state: GameState, player_id: int):
    """Conta uma oportunidade de ataque perdida para locks temporários."""
    keep = []
    for pm in state.pending_modifiers:
        if pm.get("kind") != "attack_lock":
            keep.append(pm)
            continue
        if pm.get("owner") != player_id:
            keep.append(pm)
            continue
        found = state.find_minion(pm.get("target_id"))
        if found is None:
            continue
        target = found[0]
        remaining = int(pm.get("turns_remaining", 0) or 0) - 1
        if remaining <= 0:
            if "ATTACK_LOCKED" in target.tags:
                target.tags.remove("ATTACK_LOCKED")
            state.log_event({"type": "attack_lock_expired", "minion": target.instance_id})
            continue
        pm["turns_remaining"] = remaining
        keep.append(pm)
    state.pending_modifiers = keep


END_TURN_TRIGGERS = ("END_OF_TURN", "END_OF_YOUR_TURN", "ON_END_TURN")


def _run_end_turn_triggers(state: GameState, player_id: int,
                           start_minion_index: int = 0,
                           start_trigger_index: int = 0,
                           board_ids: list[str] | None = None) -> bool:
    """Dispara gatilhos de fim de turno e pausa se algum abrir escolha.

    Quando um efeito como Viní, o Iluminado abre ``pending_choice``, guardamos
    o próximo ponto de execução. Após a resposta do jogador, a engine continua
    dali em vez de reiniciar todos os gatilhos e criar escolhas infinitas.
    """
    p = state.players[player_id]
    board_ids = list(board_ids) if board_ids is not None else [m.instance_id for m in p.board]
    for i in range(start_minion_index, len(board_ids)):
        found = state.find_minion(board_ids[i])
        if not found or found[1] != player_id:
            continue
        m = found[0]
        first_trigger = start_trigger_index if i == start_minion_index else 0
        for j in range(first_trigger, len(END_TURN_TRIGGERS)):
            effects.fire_minion_trigger(state, m, END_TURN_TRIGGERS[j])
            if state.pending_choice is not None:
                next_j = j + 1
                next_i = i
                if next_j >= len(END_TURN_TRIGGERS):
                    next_i += 1
                    next_j = 0
                state.pending_choice["resume_end_turn"] = {
                    "player_id": player_id,
                    "next_minion_index": next_i,
                    "next_trigger_index": next_j,
                    "board_ids": board_ids,
                }
                return False
    return True


def _finish_end_turn_after_triggers(state: GameState, player_id: int) -> bool:
    p = state.players[player_id]
    # Descongela no FIM do turno do dono, mas APENAS se o lacaio veio
    # congelado de antes (não foi congelado AGORA durante este turno).
    # Marcamos congelados "frescos" com freeze_pending. No fim do turno:
    #   - freeze_pending=True  → vira freeze "antigo" (será descongelado no
    #                            próximo end_turn do dono).
    #   - freeze_pending=False → de fato perdeu o turno, descongela.
    for m in p.board:
        if m.frozen:
            if _has_active_freeze_until_source(state, m.instance_id):
                # Congelamento sustentado por outro lacaio: não descongela pelo fluxo normal.
                pass
            elif m.freeze_pending:
                m.freeze_pending = False  # marcou como "esperando o turno"
            else:
                m.frozen = False  # já passou um turno congelado, descongela
        # SKIP_NEXT_ATTACK consome a próxima oportunidade de ataque. No fim do
        # turno do dono, essa oportunidade já passou, então limpamos.
        if m.skip_next_attack:
            m.skip_next_attack = False
            state.log_event({"type": "skip_next_attack_consumed", "minion": m.instance_id})
    if p.hero_frozen:
        if getattr(p, "hero_freeze_pending", False):
            p.hero_freeze_pending = False
        else:
            p.hero_frozen = False

    # Congelados aplicados DURANTE este turno (no inimigo) ficam com
    # freeze_pending=True. No fim do nosso turno marcamos esses como
    # "amadurecidos" (pending=False) para que descongelem no fim do
    # próximo turno do dono - assim o lacaio perde apenas UM ataque.
    # IMPORTANTE: não tocar em alvos sustentados por FREEZE_UNTIL_SELF_DIES
    # (Viní Geladinho), que dependem de freeze_pending continuar True
    # enquanto a fonte está viva.
    foe_state = state.opponent_of(player_id)
    for m in foe_state.board:
        if (m.frozen and m.freeze_pending
                and not _has_active_freeze_until_source(state, m.instance_id)):
            m.freeze_pending = False
    if foe_state.hero_frozen and getattr(foe_state, "hero_freeze_pending", False):
        foe_state.hero_freeze_pending = False

    # Reverte custos temporários de cartas, como Estrategista.
    keep_modifiers_for_cost = []
    for pm in state.pending_modifiers:
        if pm.get("kind") == "temporary_card_cost_override" and pm.get("owner") == player_id:
            cid = pm.get("card_instance_id")
            for ch in state.players[player_id].hand:
                if ch.instance_id == cid:
                    ch.cost_override = pm.get("previous_cost_override")
                    ch.cost_modifier = int(pm.get("previous_cost_modifier", 0) or 0)
                    state.log_event({"type": "temporary_cost_reverted",
                                     "player": player_id, "card": cid})
                    break
            continue
        keep_modifiers_for_cost.append(pm)
    state.pending_modifiers = keep_modifiers_for_cost

    # Tags temporárias como Furtividade de Nandinho duram só até o fim
    # do turno de quem as concedeu.
    _remove_temporary_tags_at_end_of_turn(state, player_id)

    # Locks temporários de ataque consomem uma oportunidade no fim do turno do dono.
    _advance_attack_locks_for_player(state, player_id)

    # === Limpa cartas com ECHO ainda na mão ao fim do turno ===
    # ECHO faz a carta voltar à mão após uso. No fim do turno, é descartada.
    expired_echo = [c for c in p.hand if c.echo_temporary]
    for c in expired_echo:
        state.log_event({
            "type": "echo_expire",
            "instance_id": c.instance_id,
            "card_id": c.card_id,
            "player": player_id,
        })
    if expired_echo:
        p.hand = [c for c in p.hand if not c.echo_temporary]

    apply_continuous_effects(state)
    cleanup(state)

    # passa o turno
    state.current_player = 1 - state.current_player
    start_turn(state)
    return True


def _continue_end_turn_after_choice(state: GameState, resume: dict | None) -> bool:
    if not resume:
        return False
    player_id = int(resume.get("player_id"))
    if state.current_player != player_id or state.phase != "PLAYING":
        return False
    if not _run_end_turn_triggers(
        state,
        player_id,
        int(resume.get("next_minion_index", 0) or 0),
        int(resume.get("next_trigger_index", 0) or 0),
        list(resume.get("board_ids") or []),
    ):
        return True
    return _finish_end_turn_after_triggers(state, player_id)


def end_turn(state: GameState, player_id: int):
    if state.pending_choice is not None:
        return False
    if state.current_player != player_id or state.phase != "PLAYING":
        return False
    if not _run_end_turn_triggers(state, player_id):
        return True
    return _finish_end_turn_after_triggers(state, player_id)

def _remove_temporary_tags_at_end_of_turn(state: GameState, player_id: int):
    """Remove efeitos temporários no fim do turno do dono/turno-alvo."""
    keep = []
    for pm in state.pending_modifiers:
        kind = pm.get("kind")
        if kind == "temporary_tag" and pm.get("owner") == player_id:
            found = state.find_minion(pm.get("minion_id"))
            if found:
                minion, _ = found
                tag = pm.get("tag")
                if tag in minion.tags:
                    minion.tags.remove(tag)
                    if tag == "DIVINE_SHIELD":
                        minion.divine_shield = False
                    state.log_event({"type": "temporary_tag_removed",
                                     "minion": minion.instance_id, "tag": tag})
            continue

        # Buff de stats temporário: "+X ataque/vida este turno". Reverte stat
        # bruto registrando exatamente o quanto foi adicionado, evitando que
        # buffs efêmeros virem permanentes.
        if kind == "temporary_stat_buff" and pm.get("owner") == player_id:
            found = state.find_minion(pm.get("minion_id"))
            if found:
                minion, _ = found
                atk = int(pm.get("attack", 0) or 0)
                hp = int(pm.get("health", 0) or 0)
                if atk:
                    minion.attack = max(0, minion.attack - atk)
                if hp > 0:
                    new_max = max(1, minion.max_health - hp)
                    minion.health = min(minion.health, new_max)
                    minion.max_health = new_max
                elif hp < 0:
                    minion.max_health = max(1, minion.max_health - hp)
                    minion.health = minion.health - hp
                    if minion.health > minion.max_health:
                        minion.health = minion.max_health
                state.log_event({
                    "type": "temporary_stat_buff_reverted",
                    "minion": minion.instance_id,
                    "attack": atk, "health": hp,
                })
            continue

        # Efeito de Zé Droguinha: dura até o fim do próximo turno do oponente
        # (ou do turno definido em remove_on_turn_owner).
        if kind == "temporary_spell_target_immunity" and pm.get("remove_on_turn_owner") == player_id:
            tag = pm.get("tag", "ENEMY_SPELL_TARGET_IMMUNITY")
            target_kind = pm.get("target_kind")
            if target_kind == "minion":
                found = state.find_minion(pm.get("target_id"))
                if found:
                    minion, _ = found
                    if tag in minion.tags:
                        minion.tags.remove(tag)
                        state.log_event({"type": "temporary_spell_target_immunity_removed",
                                         "target_kind": "minion",
                                         "target_id": minion.instance_id})
            elif target_kind == "hero":
                pid = pm.get("target_id")
                if isinstance(pid, int) and 0 <= pid < len(state.players):
                    state.players[pid].hero_spell_target_immune = False
                    state.log_event({"type": "temporary_spell_target_immunity_removed",
                                     "target_kind": "hero", "target_id": pid})
            continue

        keep.append(pm)
    state.pending_modifiers = keep


def apply_continuous_effects(state: GameState):
    """Recalcula efeitos contínuos/aura sem empilhar permanentemente."""
    def _parse_int(v, default=0):
        try:
            return int(v)
        except Exception:
            return default

    def _remove_aura_markers():
        for m in list(state.all_minions()):
            for tag in list(m.tags):
                if tag.startswith("_AURA_STAT:"):
                    parts = tag.split(":")
                    if len(parts) >= 4:
                        atk = _parse_int(parts[-2])
                        hp = _parse_int(parts[-1])
                        m.attack = max(0, m.attack - atk)
                        # Retira exatamente o delta de vida máxima/vida atual
                        # aplicado por _mark_stat. Isso evita que auras de +vida
                        # curem o lacaio a cada recálculo e também evita que
                        # debuffs negativos voltem como cura indevida.
                        m.max_health = max(1, m.max_health - hp)
                        m.health = m.health - hp
                        if m.health > m.max_health:
                            m.health = m.max_health
                    m.tags.remove(tag)
                elif tag.startswith("_AURA_TAG:"):
                    parts = tag.split(":", 2)
                    if len(parts) == 3:
                        aura_tag = parts[2]
                        if aura_tag in m.tags:
                            m.tags.remove(aura_tag)
                        if aura_tag == "DIVINE_SHIELD":
                            m.divine_shield = False
                    m.tags.remove(tag)
                elif tag.startswith("_AURA_REMOVED_TAG:"):
                    parts = tag.split(":", 2)
                    if len(parts) == 3:
                        restored = parts[2]
                        if restored not in m.tags:
                            m.tags.append(restored)
                        if restored == "DIVINE_SHIELD":
                            m.divine_shield = True
                    m.tags.remove(tag)
                elif tag.startswith("_AURA_TRIBE:"):
                    parts = tag.split(":", 2)
                    if len(parts) == 3:
                        tribe = parts[2]
                        if tribe in m.tribes:
                            m.tribes.remove(tribe)
                    m.tags.remove(tag)

    def _mark_stat(target: Minion, source: Minion, atk: int, hp: int):
        if atk == 0 and hp == 0:
            return
        old_attack = target.attack
        old_max_health = target.max_health
        old_health = target.health
        target.attack = max(0, target.attack + atk)
        target.max_health = max(1, target.max_health + hp)
        # Auras de vida não devem curar novamente em cada recálculo (ataques,
        # dano e cleanup chamam apply_continuous_effects várias vezes). O delta
        # de vida atual aplicado é exatamente o delta de vida máxima efetivo;
        # ao retirar e reaplicar a aura, o dano pré-existente fica preservado.
        health_delta = target.max_health - old_max_health
        target.health = max(0, old_health + health_delta)
        # Guarde o delta efetivamente aplicado após clamps. Sem isso, uma aura
        # -1 de ataque em lacaio 0/1 seria removida como +1 de ataque.
        applied_atk = target.attack - old_attack
        applied_hp = target.max_health - old_max_health
        if applied_atk == 0 and applied_hp == 0:
            return
        marker = f"_AURA_STAT:{source.instance_id}:{applied_atk}:{applied_hp}"
        target.tags.append(marker)

    def _mark_tag(target: Minion, source: Minion, tag: str):
        if not tag or tag in target.tags:
            return
        target.tags.append(tag)
        target.tags.append(f"_AURA_TAG:{source.instance_id}:{tag}")
        if tag == "DIVINE_SHIELD":
            target.divine_shield = True

    def _remove_tag_temporarily(target: Minion, source: Minion, tag: str):
        if not tag or tag not in target.tags:
            return
        target.tags.remove(tag)
        target.tags.append(f"_AURA_REMOVED_TAG:{source.instance_id}:{tag}")
        if tag == "DIVINE_SHIELD":
            target.divine_shield = False

    def _mark_tribe(target: Minion, source: Minion, tribe: str):
        if not tribe or tribe in target.tribes:
            return
        target.tribes.append(tribe)
        target.tags.append(f"_AURA_TRIBE:{source.instance_id}:{tribe}")

    def _count_minions(source: Minion, count_desc: dict):
        mode = count_desc.get("mode", "ALL_MINIONS")
        tribes = count_desc.get("tribes") or []
        tribe = count_desc.get("tribe")
        if tribe:
            tribes.append(tribe)
        if isinstance(tribes, str):
            tribes = [tribes]
        if mode == "FRIENDLY_MINIONS":
            pool = state.players[source.owner].board
        elif mode == "ENEMY_MINIONS":
            pool = state.opponent_of(source.owner).board
        else:
            pool = state.all_minions()
        if not tribes:
            return len(pool)
        return sum(1 for m in pool if any(m.has_tribe(t) for t in tribes))

    def _apply_aura_effect(source: Minion, eff: dict):
        action = eff.get("action")
        if action == "CONDITIONAL_EFFECTS":
            cond = eff.get("condition") or {}
            if not effects.check_condition(state, cond, source.owner, source, {"chosen_target": source.instance_id}):
                return
            for sub in eff.get("effects") or []:
                merged = dict(sub)
                if "target" not in merged:
                    merged["target"] = {"mode": "SELF"}
                _apply_aura_effect(source, merged)
            return

        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = [
            t for t in targeting.resolve_targets(state, target_desc, source.owner, source, None)
            if isinstance(t, Minion)
        ]

        if action == "BUFF_ATTACK":
            atk = int(eff.get("amount", 0) or 0)
            for t in targets:
                _mark_stat(t, source, atk, 0)
        elif action == "BUFF_HEALTH":
            hp = int(eff.get("amount", 0) or 0)
            for t in targets:
                _mark_stat(t, source, 0, hp)
        elif action == "BUFF_STATS":
            amount = eff.get("amount")
            if isinstance(amount, dict):
                atk = int(amount.get("attack", 0) or 0)
                hp = int(amount.get("health", 0) or 0)
            else:
                atk = int(eff.get("attack_bonus", eff.get("attack", 0)) or 0)
                hp = int(eff.get("health_bonus", eff.get("health", 0)) or 0)
            for t in targets:
                _mark_stat(t, source, atk, hp)
        elif action == "BUFF_ATTACK_PER_MINION_TRIBE":
            count_desc = dict(eff.get("count") or {})
            if not count_desc:
                count_desc = {"mode": "ALL_MINIONS", "tribes": eff.get("tribes") or [eff.get("tribe")]}
            n = _count_minions(source, count_desc)
            atk = int(eff.get("amount", 1) or 1) * n
            for t in targets:
                _mark_stat(t, source, atk, 0)
        elif action == "ADD_TAG":
            tag = eff.get("tag")
            for t in targets:
                _mark_tag(t, source, tag)
            # Auras como Caverna: "Seus lacaios possuem Eco" também devem
            # afetar lacaios aliados enquanto estão na mão.
            target_mode = (target_desc or {}).get("mode")
            if target_mode == "FRIENDLY_MINIONS" and tag:
                marker = f"_AURA_HAND_TAG:{source.instance_id}:{tag}"
                for ch in state.players[source.owner].hand:
                    card = effects.get_card(ch.card_id) if hasattr(effects, "get_card") else None
                    if card is None:
                        from .cards import get_card as _get_card
                        card = _get_card(ch.card_id) or {}
                    if card.get("type") == "MINION" and marker not in ch.extra_tags:
                        ch.extra_tags.append(marker)
                        if tag not in ch.extra_tags:
                            ch.extra_tags.append(tag)
        elif action == "REMOVE_TAG":
            tag = eff.get("tag")
            for t in targets:
                _remove_tag_temporarily(t, source, tag)
        elif action == "ADD_TRIBE":
            tribe = eff.get("tribe")
            for t in targets:
                _mark_tribe(t, source, tribe)

    _remove_aura_markers()

    # Remove marcadores de aura da mão antes de recalcular, inclusive a tag concedida.
    for p in state.players:
        for ch in p.hand:
            aura_tags_to_remove = []
            for t in list(ch.extra_tags or []):
                if str(t).startswith("_AURA_HAND_TAG:"):
                    parts = str(t).split(":", 2)
                    if len(parts) == 3:
                        aura_tags_to_remove.append(parts[2])
            ch.extra_tags = [
                t for t in (ch.extra_tags or [])
                if not str(t).startswith("_AURA_HAND_TAG:") and t not in aura_tags_to_remove
            ]

    # Recalcula todas as auras declaradas no JSON.
    for source in list(state.all_minions()):
        if source.silenced:
            continue
        for eff in source.effects or []:
            if eff.get("trigger") == "AURA":
                _apply_aura_effect(source, eff)

    for m in list(state.all_minions()):
        if m.silenced:
            continue

        # PASSIVE/AURA: aplica marcadores defensivos que devem existir enquanto o
        # lacaio não estiver silenciado. Handlers são idempotentes.
        for eff in m.effects or []:
            if eff.get("trigger") == "PASSIVE" and eff.get("action") in (
                "CANNOT_BE_TARGETED_BY_SPELLS",
                "CANNOT_BE_TARGETED_BY_ENEMY_SPELLS",
                "IMMUNE_TO_TRIGGERED_EFFECTS",
                "PERMANENT_STEALTH",
            ):
                effects.resolve_effect(state, eff, m.owner, m, {"chosen_target": None})

        # AURA: não pode atacar enquanto for o único lacaio aliado.
        has_only_friendly_aura = any(
            eff.get("trigger") == "AURA" and eff.get("action") == "CANT_ATTACK_WHILE_ONLY_FRIENDLY_MINION"
            for eff in (m.effects or [])
        )
        if has_only_friendly_aura:
            only_friendly = len(state.players[m.owner].board) == 1
            if only_friendly and "CANT_ATTACK_ONLY_FRIENDLY" not in m.tags:
                m.tags.append("CANT_ATTACK_ONLY_FRIENDLY")
            elif not only_friendly and "CANT_ATTACK_ONLY_FRIENDLY" in m.tags:
                m.tags.remove("CANT_ATTACK_ONLY_FRIENDLY")

        # WHILE_DAMAGED: Edu Putasso (+3 ataque enquanto ferido).
        for eff in m.effects or []:
            if eff.get("trigger") == "WHILE_DAMAGED" and eff.get("action") == "BUFF_ATTACK":
                amount = int(eff.get("amount", 0) or 0)
                marker = f"_WHILE_DAMAGED_ATTACK_{amount}"
                is_damaged = m.health < m.max_health
                has_marker = marker in m.tags
                if is_damaged and not has_marker:
                    m.attack += amount
                    m.tags.append(marker)
                    state.log_event({"type": "while_damaged_on", "minion": m.instance_id,
                                     "attack_delta": amount})
                elif (not is_damaged) and has_marker:
                    m.attack = max(0, m.attack - amount)
                    m.tags.remove(marker)
                    state.log_event({"type": "while_damaged_off", "minion": m.instance_id,
                                     "attack_delta": -amount})

            # WHILE_STEALTHED: Nando 3 Anos imune enquanto furtivo.
            if eff.get("trigger") == "WHILE_STEALTHED" and eff.get("action") == "SET_IMMUNE":
                if m.has_tag("STEALTH"):
                    m.immune = True
                    if "IMMUNE_WHILE_STEALTH" not in m.tags:
                        m.tags.append("IMMUNE_WHILE_STEALTH")
                elif "IMMUNE_WHILE_STEALTH" in m.tags:
                    m.immune = False
                    m.tags.remove("IMMUNE_WHILE_STEALTH")


def _advance_friendly_summon_awaken_counters(state: GameState, owner: int, summoned_minion_id: str):
    """Atualiza contadores de Viní Dorminhoco quando um lacaio aliado é evocado/jogado."""
    keep_modifiers = []
    for pm in state.pending_modifiers:
        if pm.get("kind") != "friendly_summons_awaken_counter":
            keep_modifiers.append(pm)
            continue
        if pm.get("owner") != owner:
            keep_modifiers.append(pm)
            continue
        target_id = pm.get("target_id")
        if target_id == summoned_minion_id:
            keep_modifiers.append(pm)
            continue
        found = state.find_minion(target_id)
        if not found:
            continue
        target = found[0]
        if "DORMANT" not in target.tags:
            continue
        pm["count"] = int(pm.get("count", 0) or 0) + 1
        required = int(pm.get("required", 2) or 2)
        if pm["count"] >= required:
            try:
                from .effects_lote25_requested_fixes import wake_dormant_minion
                wake_dormant_minion(state, target)
            except Exception:
                target.tags = [t for t in target.tags if t != "DORMANT"]
                target.cant_attack = False
                target.immune = False
                target.summoning_sick = True
            state.log_event({"type": "awaken", "minion": target.instance_id,
                             "reason": "friendly_minions_summoned", "count": pm["count"]})
            continue
        keep_modifiers.append(pm)
    state.pending_modifiers = keep_modifiers


def compute_dynamic_cost(state: GameState, p: PlayerState, card_in_hand: CardInHand,
                          card: dict) -> int:
    """Calcula custo final considerando reduções IN_HAND dinâmicas (Garçom,
    Ferragui, Justiça). Não inclui pending_modifiers (esses são aplicados em
    play_card)."""
    base = card_in_hand.effective_cost()
    foe = state.opponent_of(p.player_id)
    discount = 0
    for aura_source in list(p.board):
        if aura_source.silenced:
            continue
        for eff in aura_source.effects or []:
            if eff.get("trigger") != "AURA" or eff.get("action") != "INCREASE_COST":
                continue
            valid = (eff.get("target") or {}).get("valid") or []
            applies = not valid or ("MINION" in valid and card.get("type") == "MINION") or ("SPELL" in valid and card.get("type") == "SPELL")
            if applies:
                discount -= int(eff.get("amount", 0) or 0)

    for eff in card.get("effects") or []:
        if eff.get("trigger") != "IN_HAND":
            continue
        action = eff.get("action")
        if action == "COST_REDUCTION_PER_FRIENDLY_MINION":
            n = len(p.board)
            discount += n * eff.get("amount", 1)
        elif action == "COST_REDUCTION_PER_DAMAGED_MINION":
            n = sum(1 for m in state.all_minions() if m.health < m.max_health)
            discount += n * eff.get("amount_per_minion", eff.get("amount", 1))
        elif action == "COST_REDUCTION":
            cond = eff.get("condition") or {}
            ctype = cond.get("type")
            applies = False
            if ctype == "OPPONENT_HAS_MORE_CARDS_IN_HAND":
                applies = len(foe.hand) > len(p.hand)
            if applies:
                discount += eff.get("amount", 0)
        elif action == "REDUCE_COST":
            cond = eff.get("condition") or {}
            applies = True
            if cond:
                applies = effects.check_condition(state, cond, p.player_id, None,
                                                  {"source_card_id": card.get("id")})
            if applies:
                discount += eff.get("amount", 0)
        elif action == "INCREASE_COST":
            cond = eff.get("condition") or {}
            applies = True
            if cond:
                applies = effects.check_condition(state, cond, p.player_id, None,
                                                  {"source_card_id": card.get("id")})
            if applies:
                discount -= eff.get("amount", 0)
    return max(0, base - discount)


def compute_displayed_cost(state: "GameState", p: "PlayerState",
                            card_in_hand: "CardInHand", card: dict) -> int:
    """Custo final mostrado na UI: inclui IN_HAND (compute_dynamic_cost) +
    descontos pendentes que reduzem o próximo CARD jogado (ex: Spiid 3 Anos).

    NÃO usar em play_card - a engine consome os pending_modifiers ao pagar
    e somar de novo aqui causaria desconto duplo."""
    base = compute_dynamic_cost(state, p, card_in_hand, card)
    extra = 0
    for pm in state.pending_modifiers:
        if pm.get("consumed") or pm.get("owner") != p.player_id:
            continue
        if pm.get("kind") != "next_card_cost_reduction":
            continue
        if not _card_matches_pending_filter(card, pm.get("valid") or []):
            continue
        extra += int(pm.get("amount", 0) or 0)
    return max(0, base - extra)




def _card_matches_pending_filter(card: dict, valid: list[str]) -> bool:
    """Interpreta filtros usados por modificadores pendentes de carta."""
    if not valid:
        return True
    from .cards import card_has_tribe
    for v in valid:
        if v == "MINION" and card.get("type") == "MINION":
            return True
        if v == "SPELL" and card.get("type") == "SPELL":
            return True
        if v.startswith("CARD_WITH_TRIBE_"):
            needed = v[len("CARD_WITH_TRIBE_"):]
            if card_has_tribe(card, needed):
                return True
    return False


def _activate_next_turn_modifiers(state: GameState, player_id: int):
    """Ativa modificadores que devem começar no início deste turno.

    Usado por La Selecione/Vinas: no próximo turno, o primeiro lacaio custa
    menos. A ativação acontece depois da limpeza inicial de start_turn.
    """
    for pm in state.pending_modifiers:
        if pm.get("kind") != "next_turn_first_minion_cost_reduction":
            continue
        if pm.get("owner") != player_id or pm.get("active") or pm.get("consumed"):
            continue
        pm["kind"] = "next_card_cost_reduction"
        pm["valid"] = ["MINION"]
        pm["active"] = True
        pm["expires_on"] = "end_of_turn"
        state.log_event({
            "type": "next_turn_minion_discount_active",
            "player": player_id,
            "amount": pm.get("amount", 0),
            "conditional_amount": pm.get("conditional_amount"),
        })


def _deliver_delayed_draws(state: GameState, player: PlayerState):
    """Entrega cartas compradas previamente por DRAW_CARD_DELAYED.

    AliExpress remove a carta do deck quando é jogada e só a coloca na mão no
    próximo turno do dono, com custo modificado.
    """
    keep = []
    for pm in state.pending_modifiers:
        if pm.get("kind") != "delayed_draw":
            keep.append(pm)
            continue
        if pm.get("owner") != player.player_id:
            keep.append(pm)
            continue
        turns = int(pm.get("own_turns_remaining", 1) or 1)
        if turns > 1:
            pm["own_turns_remaining"] = turns - 1
            keep.append(pm)
            continue

        delivered = []
        for card_id in pm.get("cards", []) or []:
            if len(player.hand) >= MAX_HAND_SIZE:
                state.log_event({"type": "burn_delayed_draw", "player": player.player_id, "card_id": card_id})
                continue
            ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
            ch.cost_modifier += int(pm.get("cost_modifier", 0) or 0)
            player.hand.append(ch)
            delivered.append(ch.instance_id)
            state.log_event({
                "type": "delayed_draw_arrived",
                "player": player.player_id,
                "card_id": card_id,
                "instance_id": ch.instance_id,
                "cost_modifier": ch.cost_modifier,
            })
        if delivered:
            state.last_drawn_card_instance_ids = delivered
    state.pending_modifiers = keep


def _play_triggers_for_card(card: dict, combo_active: bool = False,
                            empowered: bool = False) -> list[str]:
    """Triggers de resolução principal ao jogar uma carta.

    - Normal: ON_PLAY, mais ON_COMBO se a condição de combo estiver ativa.
    - Fortalecer: usa ON_PLAY_EMPOWERED / ON_EMPOWER no lugar de ON_PLAY.
      No JSON atual esses efeitos já incluem o comportamento final completo.
    """
    effects_list = card.get("effects") or []
    if empowered:
        empowered_triggers = []
        if any(e.get("trigger") == "ON_PLAY_EMPOWERED" for e in effects_list):
            empowered_triggers.append("ON_PLAY_EMPOWERED")
        if any(e.get("trigger") == "ON_EMPOWER" for e in effects_list):
            empowered_triggers.append("ON_EMPOWER")
        if empowered_triggers:
            return empowered_triggers

    triggers = ["ON_PLAY"]
    if combo_active and any(e.get("trigger") == "ON_COMBO" for e in effects_list):
        triggers.append("ON_COMBO")
    return triggers


def play_card(state: GameState, player_id: int, hand_instance_id: str,
              chosen_target: Optional[str] = None,
              chosen_targets: Optional[list[str]] = None,
              board_position: Optional[int] = None,
              chose_index: Optional[int] = None,
              empowered: bool = False,
              direction: Optional[str] = None) -> tuple[bool, str]:
    """Joga uma carta da mão. Retorna (sucesso, mensagem).
    
    chose_index: índice da opção escolhida quando a carta tem CHOOSE_ONE.
                 Cliente envia 0 ou 1 conforme a opção clicada.
    """
    if state.pending_choice is not None:
        return False, "Resolva a escolha pendente antes de agir"
    if state.current_player != player_id or state.phase != "PLAYING":
        return False, "Não é seu turno"
    p = state.players[player_id]
    card_in_hand = next((c for c in p.hand if c.instance_id == hand_instance_id), None)
    if card_in_hand is None:
        return False, "Carta não está na mão"
    card = get_card(card_in_hand.card_id)
    if card is None:
        return False, "Carta inválida"

    combo_active = p.cards_played_this_turn > 0
    main_triggers = _play_triggers_for_card(card, combo_active=combo_active,
                                            empowered=bool(empowered))

    # === Aplica modificadores de "próxima carta jogada" ===
    pending_to_consume = []
    extra_reduction = 0
    pending_stat_bonus = {"attack": 0, "health": 0}
    pay_with_health = False

    for pm in state.pending_modifiers:
        if pm.get("owner") != player_id or pm.get("consumed"):
            continue
        kind = pm.get("kind")

        if kind == "next_card_cost_reduction":
            if not _card_matches_pending_filter(card, pm.get("valid") or []):
                continue
            amount = int(pm.get("amount", 0) or 0)
            cond = pm.get("condition") or {}
            if cond and pm.get("conditional_amount") is not None:
                if effects.check_condition(state, cond, player_id, None,
                                           {"source_card_id": card.get("id"),
                                            "played_card_id": card.get("id")}):
                    amount = int(pm.get("conditional_amount") or amount)
            extra_reduction += amount
            pending_to_consume.append(pm)

        elif kind == "next_played_stat_buff":
            if not _card_matches_pending_filter(card, pm.get("valid") or []):
                continue
            pending_stat_bonus["attack"] += int(pm.get("attack", 0) or 0)
            pending_stat_bonus["health"] += int(pm.get("health", 0) or 0)
            pending_to_consume.append(pm)

        elif kind == "next_spell_costs_health_instead_of_mana":
            if card.get("type") == "SPELL":
                pay_with_health = True
                pending_to_consume.append(pm)

    cost = max(0, compute_dynamic_cost(state, p, card_in_hand, card) - extra_reduction)
    # Fortalecer: feitiço fortalecido custa 1 de mana a mais.
    if empowered and card.get("type") == "SPELL" and main_triggers != ["ON_PLAY"]:
        cost += 1
    if pay_with_health:
        # Custa vida, não mana. Armadura não absorve este pagamento.
        if p.hero_health <= 0:
            return False, "Herói sem vida para pagar o custo"
    elif p.mana < cost:
        return False, f"Mana insuficiente ({p.mana}/{cost})"

    is_minion = card.get("type") == "MINION"
    is_spell_card = card.get("type") == "SPELL"
    if is_minion and len(p.board) >= MAX_BOARD_SIZE:
        return False, "Campo cheio"

    # Valida alvo(s) antes de gastar mana/remover carta.
    if chosen_targets is None:
        chosen_targets = [chosen_target] if chosen_target is not None else []
    chosen_descs = targeting.chosen_targets_for_card(card, chose_index=chose_index,
                                                    triggers=main_triggers)
    for idx, chosen_desc in enumerate(chosen_descs):
        # Se NÃO existem alvos válidos, a carta é jogada sem alvo:
        # o efeito de targeting vira no-op, mas o restante acontece normalmente.
        if not targeting.has_valid_chosen_target(state, chosen_desc, player_id, is_spell=is_spell_card):
            state.log_event({
                "type": "no_targets_available",
                "card_name": card.get("name"),
            })
            if is_spell_card:
                return False, "Esta carta exige um alvo válido"
            continue
        target_id = chosen_targets[idx] if idx < len(chosen_targets) else None
        if target_id is None:
            return False, "Esta carta exige um alvo"
        if not targeting.resolve_targets(state, chosen_desc, player_id, None, target_id, is_spell=is_spell_card):
            return False, "Alvo inválido"
    chosen_target = chosen_targets[0] if chosen_targets else None

    # paga custo e remove da mão
    if pay_with_health:
        p.hero_health -= cost
        state.log_event({"type": "pay_health_for_spell", "player": player_id, "amount": cost})
    else:
        p.mana -= cost
    p.hand.remove(card_in_hand)
    # consome modificadores aplicados
    for pm in pending_to_consume:
        pm["consumed"] = True
    state.pending_modifiers = [pm for pm in state.pending_modifiers if not pm.get("consumed")]

    state.log_event({
        "type": "play_card",
        "player": player_id,
        "card_id": card.get("id"),
        "card_name": card.get("name"),
        "cost": cost,
        "combo_active": combo_active,
        "empowered": bool(empowered) and main_triggers != ["ON_PLAY"],
        "direction": direction,
    })

    if is_minion:
        base_atk = card.get("attack")
        if base_atk is None:
            base_atk = 0
        base_hp = card.get("health")
        if base_hp is None:
            base_hp = 1
        atk = base_atk + card_in_hand.stat_modifier.get("attack", 0) + pending_stat_bonus.get("attack", 0)
        hp = base_hp + card_in_hand.stat_modifier.get("health", 0) + pending_stat_bonus.get("health", 0)
        all_tags = list(card.get("tags") or []) + list(card_in_hand.extra_tags or [])
        new_minion = Minion(
            instance_id=gen_id("m_"),
            card_id=card.get("id"),
            name=card.get("name"),
            attack=atk,
            health=hp,
            max_health=hp,
            tags=all_tags,
            tribes=list(card.get("tribes") or []),
            effects=list(card.get("effects") or []),
            owner=player_id,
            divine_shield="DIVINE_SHIELD" in all_tags,
            summoning_sick=True,
        )
        if board_position is None or board_position < 0 or board_position > len(p.board):
            board_position = len(p.board)
        if "DORMANT" in new_minion.tags:
            new_minion.cant_attack = True
            new_minion.immune = True
            new_minion.summoning_sick = True
            fms_threshold = next((
                int(e.get("amount", 2) or 2)
                for e in (new_minion.effects or [])
                if e.get("trigger") == "FRIENDLY_MINIONS_SUMMONED"
            ), None)
            if fms_threshold is not None:
                state.pending_modifiers.append({
                    "kind": "friendly_summons_awaken_counter",
                    "target_id": new_minion.instance_id,
                    "owner": player_id,
                    "count": 0,
                    "required": fms_threshold,
                })
        p.board.insert(board_position, new_minion)
        state.log_event({"type": "summon", "owner": player_id, "minion": new_minion.to_dict()})

        _advance_friendly_summon_awaken_counters(state, player_id, new_minion.instance_id)

        # Dispara ON_FRIENDLY_SUMMON em outros lacaios aliados.
        try:
            from .effects_lote19 import fire_friendly_summon_triggers
            fire_friendly_summon_triggers(state, new_minion, player_id)
        except Exception:
            pass

        # dispara triggers principais do próprio lacaio: ON_PLAY / ON_COMBO / Fortalecer
        for trigger_name in main_triggers:
            effects.resolve_card_effects(state, card, player_id, trigger_name,
                                         source_minion=new_minion,
                                         chosen_target=chosen_target,
                                         is_spell=False,
                                         extra_ctx={"chose_index": chose_index,
                                                     "target_queue": list(chosen_targets),
                                                     "target_cursor": 0,
                                                     "source_card_id": card.get("id"),
                                                     "played_card_id": card.get("id"),
                                                     "empowered": bool(empowered),
                                                     "direction": direction,
                                                     "adjacent_direction": direction})
        # triggers em outros lacaios: AFTER_FRIENDLY_MINION_PLAY / AFTER_YOU_PLAY_MINION
        for m in list(p.board):
            if m is new_minion:
                continue
            effects.fire_minion_trigger(state, m, "AFTER_FRIENDLY_MINION_PLAY",
                                        extra_ctx={"played_minion": new_minion.instance_id})
            effects.fire_minion_trigger(state, m, "ON_FRIENDLY_MINION_PLAYED",
                                        extra_ctx={"played_minion": new_minion.instance_id,
                                                   "chosen_target": new_minion.instance_id})
            effects.fire_minion_trigger(state, m, "AFTER_YOU_PLAY_MINION",
                                        extra_ctx={"played_minion": new_minion.instance_id})
            play_card_ctx = {"card_type": "MINION",
                             "played_minion": new_minion.instance_id,
                             "played_card_id": card.get("id"),
                             "source_card_id": card.get("id")}
            effects.fire_minion_trigger(state, m, "AFTER_YOU_PLAY_CARD",
                                        extra_ctx=play_card_ctx)
            effects.fire_minion_trigger(state, m, "ON_PLAY_CARD",
                                        extra_ctx=play_card_ctx)

    else:  # SPELL
        for trigger_name in main_triggers:
            effects.resolve_card_effects(state, card, player_id, trigger_name,
                                         source_minion=None,
                                         chosen_target=chosen_target,
                                         is_spell=True,
                                         extra_ctx={"chose_index": chose_index,
                                                     "target_queue": list(chosen_targets),
                                                     "target_cursor": 0,
                                                     "source_card_id": card.get("id"),
                                                     "played_card_id": card.get("id"),
                                                     "empowered": bool(empowered),
                                                     "direction": direction,
                                                     "adjacent_direction": direction})
        # dispara em lacaios: AFTER_YOU_PLAY_CARD / ON_PLAY_CARD / OPPONENT_SPELL_PLAYED no oponente
        play_card_ctx = {"card_type": "SPELL",
                         "played_card_id": card.get("id"),
                         "source_card_id": card.get("id")}
        for m in list(p.board):
            effects.fire_minion_trigger(state, m, "AFTER_YOU_PLAY_CARD",
                                        extra_ctx=play_card_ctx)
            effects.fire_minion_trigger(state, m, "ON_PLAY_CARD",
                                        extra_ctx=play_card_ctx)
        for m in list(state.opponent_of(player_id).board):
            effects.fire_minion_trigger(state, m, "OPPONENT_SPELL_PLAYED")

    p.cards_played_this_turn += 1

    # === ECHO ===
    # Carta com tag ECHO: ao invés de descartar, volta à mão do dono como
    # temporária. Será descartada no fim do turno (em end_turn).
    # Cada vez que uma carta com ECHO é jogada, uma cópia temporária volta
    # para a mão. A cópia temporária também pode ser jogada novamente enquanto
    # houver mana; todas somem no fim do turno.
    has_echo = "ECHO" in (list(card.get("tags") or []) + list(card_in_hand.extra_tags or []))
    if has_echo:
        if len(p.hand) < MAX_HAND_SIZE:
            new_hand = CardInHand(
                instance_id=gen_id("h_"),
                card_id=card_in_hand.card_id,
                cost_override=card_in_hand.cost_override,
                cost_modifier=0,
                stat_modifier=dict(card_in_hand.stat_modifier),
                extra_tags=list(card_in_hand.extra_tags),
                echo_temporary=True,
            )
            p.hand.append(new_hand)
            state.log_event({
                "type": "echo_return",
                "card_id": card.get("id"),
                "instance_id": new_hand.instance_id,
            })

    cleanup(state)
    return True, "OK"



def _resume_choice_effects(state: GameState, choice: dict):
    """Continua a resolução de efeitos que foi pausada por pending_choice."""
    resume = choice.get("resume") if choice else None
    if not resume or resume.get("kind") != "effects":
        cleanup(state)
        return

    source_owner = resume.get("source_owner")
    if source_owner is None:
        cleanup(state)
        return
    source_minion = None
    mid = resume.get("source_minion_id")
    if mid:
        found = state.find_minion(mid)
        if found:
            source_minion = found[0]
    ctx = dict(resume.get("ctx") or {})
    remaining = list(resume.get("effects") or [])
    for i, eff in enumerate(remaining):
        effects.resolve_effect(state, eff, source_owner, source_minion, ctx)
        if state.pending_choice is not None:
            effects.attach_resume_to_pending_choice(state, remaining[i + 1:],
                                                    source_owner, source_minion, ctx)
            break
    cleanup(state)


def resolve_choice(state: GameState, player_id: int, choice_id: str, response: dict) -> tuple[bool, str]:
    """Resolve uma escolha pendente criada por algum efeito.

    Mantém a engine autoritativa: o cliente só envia a decisão, o servidor valida
    se a escolha existe, pertence ao jogador e se o payload faz sentido.
    """
    choice = state.pending_choice
    if not choice:
        return False, "Não há escolha pendente"
    if choice.get("owner") != player_id:
        return False, "Esta escolha pertence ao outro jogador"
    if choice.get("choice_id") != choice_id:
        return False, "Escolha inválida ou expirada"

    kind = choice.get("kind")
    p = state.players[player_id]

    if kind == "reorder_top_cards":
        cards = list(choice.get("cards") or [])
        n = len(cards)
        order = response.get("order")
        if not isinstance(order, list) or sorted(order) != list(range(n)):
            return False, "Ordem inválida"
        if p.deck[:n] != cards:
            return False, "O topo do deck mudou; escolha expirada"
        p.deck = [cards[i] for i in order] + p.deck[n:]
        state.pending_choice = None
        state.log_event({
            "type": "choice_resolved",
            "kind": kind,
            "player": player_id,
            "order": order,
        })
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "swap_revealed_top_cards":
        swap = bool(response.get("swap"))
        me = state.players[player_id]
        opp = state.opponent_of(player_id)
        my_top = choice.get("my_top")
        opp_top = choice.get("opponent_top")
        if not me.deck or not opp.deck or me.deck[0] != my_top or opp.deck[0] != opp_top:
            return False, "Cartas reveladas mudaram; escolha expirada"
        if swap:
            me.deck[0], opp.deck[0] = opp.deck[0], me.deck[0]
        state.pending_choice = None
        state.log_event({
            "type": "choice_resolved",
            "kind": kind,
            "player": player_id,
            "swap": swap,
        })
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "discard_hand_card":
        amount = int(choice.get("amount") or 1)
        ids = response.get("card_ids")
        single = response.get("card_id") or response.get("instance_id")
        if ids is None:
            ids = [single] if single else []
        if not isinstance(ids, list) or len(ids) != amount:
            return False, "Escolha de descarte inválida"
        allowed = {c.get("instance_id") for c in (choice.get("cards") or [])}
        if any(cid not in allowed for cid in ids):
            return False, "Carta de descarte inválida"
        for cid in ids:
            card = next((c for c in p.hand if c.instance_id == cid), None)
            if card is None:
                return False, "Carta de descarte não está mais na mão"
            p.hand.remove(card)
            state.log_event({"type": "discard",
                             "player": player_id,
                             "instance_id": card.instance_id,
                             "card_id": card.card_id})
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind, "player": player_id})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "choose_draw_discard":
        cards = list(choice.get("cards") or [])
        n = len(cards)
        idx = response.get("index", response.get("chosen_index"))
        try:
            idx = int(idx)
        except Exception:
            return False, "Índice inválido"
        if idx < 0 or idx >= n:
            return False, "Índice inválido"
        if p.deck[:n] != cards:
            return False, "O topo do deck mudou; escolha expirada"

        chosen = cards[idx]
        del p.deck[:n]
        drawn_ids = []
        if len(p.hand) < MAX_HAND_SIZE:
            new_card = CardInHand(instance_id=gen_id("h_"), card_id=chosen)
            p.hand.append(new_card)
            drawn_ids.append(new_card.instance_id)
            state.log_event({"type": "draw_from_choice",
                             "player": player_id,
                             "card_id": chosen,
                             "instance_id": new_card.instance_id})
        else:
            state.log_event({"type": "burn", "player": player_id, "card_id": chosen})
        for i, cid in enumerate(cards):
            if i != idx:
                state.log_event({"type": "discard_from_deck_choice",
                                 "player": player_id,
                                 "card_id": cid})
        state.last_drawn_card_instance_ids = drawn_ids
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "chosen_index": idx})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "reveal_card_from_hand":
        selected = response.get("card_id") or response.get("instance_id")
        allowed = {c.get("instance_id") for c in (choice.get("cards") or [])}
        if selected not in allowed:
            return False, "Carta inválida"
        card = next((c for c in p.hand if c.instance_id == selected), None)
        if card is None:
            return False, "Carta não está mais na mão"
        amount = int(choice.get("cost_modifier", -1) or -1)
        card.cost_modifier += amount
        state.pending_choice = None
        state.log_event({"type": "reveal_hand_card", "player": player_id,
                         "instance_id": card.instance_id, "card_id": card.card_id,
                         "cost_modifier": amount})
        state.log_event({"type": "choice_resolved", "kind": kind, "player": player_id})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "move_hand_card_to_opponent_deck_top":
        selected = response.get("card_id") or response.get("instance_id")
        allowed = {c.get("instance_id") for c in (choice.get("cards") or [])}
        if selected not in allowed:
            return False, "Carta inválida"
        card = next((c for c in p.hand if c.instance_id == selected), None)
        if card is None:
            return False, "Carta não está mais na mão"
        from .effects_lote10 import _move_hand_card_to_opp_deck_top_resolved
        ok = _move_hand_card_to_opp_deck_top_resolved(
            state, player_id, card, int(choice.get("cost_modifier", 0) or 0), choice.get("max_cost")
        )
        if not ok:
            return False, "Não foi possível mover a carta"
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind, "player": player_id})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "move_hand_cards_to_deck_and_heal":
        ids = response.get("card_ids")
        if ids is None:
            single = response.get("card_id") or response.get("instance_id")
            ids = [single] if single else []
        if not isinstance(ids, list):
            return False, "Lista de cartas inválida"
        allowed = {c.get("instance_id") for c in (choice.get("cards") or [])}
        if any(cid not in allowed for cid in ids):
            return False, "Carta inválida"
        position = (response.get("position") or choice.get("default_position") or "TOP").upper()
        if position not in ("TOP", "MIDDLE", "BOTTOM", "CHOSEN"):
            return False, "Posição inválida"
        if position == "CHOSEN":
            position = "TOP"
        from .effects_lote10 import _move_hand_cards_to_deck_and_heal_resolved
        _move_hand_cards_to_deck_and_heal_resolved(
            state, player_id, ids, position, int(choice.get("heal_per_card", 3) or 3)
        )
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind, "player": player_id,
                         "count": len(ids), "position": position})
        _resume_choice_effects(state, choice)
        return True, "OK"


    if kind == "resurrect_from_graveyard":
        idx = response.get("graveyard_index", response.get("index"))
        try:
            idx = int(idx)
        except Exception:
            return False, "Índice de cemitério inválido"
        options = list(choice.get("options") or [])
        allowed = {int(o.get("graveyard_index")) for o in options}
        if idx not in allowed:
            return False, "Lacaio inválido para ressuscitar"
        if idx < 0 or idx >= len(state.graveyard):
            return False, "Cemitério mudou; escolha expirada"
        rec = state.graveyard[idx]
        expected = next((o for o in options if int(o.get("graveyard_index")) == idx), None)
        if not expected or rec.get("card_id") != expected.get("card_id") or rec.get("owner") != player_id:
            return False, "Cemitério mudou; escolha expirada"
        from .effects_lote13 import _resurrect_card
        _resurrect_card(state, player_id, rec.get("card_id"))
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "graveyard_index": idx})
        _resume_choice_effects(state, choice)
        return True, "OK"


    if kind == "discard_up_to_cards":
        ids = response.get("card_ids")
        if ids is None:
            single = response.get("card_id") or response.get("instance_id")
            ids = [single] if single else []
        if not isinstance(ids, list):
            return False, "Lista de descarte inválida"
        amount = int(choice.get("amount", 1) or 1)
        if len(ids) > amount:
            return False, "Cartas demais selecionadas"
        allowed = {c.get("instance_id") for c in (choice.get("cards") or [])}
        if any(cid not in allowed for cid in ids):
            return False, "Carta inválida para descarte"
        from .effects_lote16 import resolve_discard_up_to_cards
        discarded = resolve_discard_up_to_cards(
            state, player_id, ids, amount, choice.get("filter") or {}
        )
        resume = choice.get("resume") or {}
        if resume.get("kind") == "effects":
            resume_ctx = dict(resume.get("ctx") or {})
            resume_ctx["discarded_count"] = discarded
            resume_ctx["discard_already_recruited"] = True
            resume["ctx"] = resume_ctx
            choice["resume"] = resume
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "count": discarded})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "choose_deck_destroy_threshold":
        x = response.get("x", response.get("threshold", 0))
        try:
            x = int(x)
        except Exception:
            return False, "Valor inválido"
        max_x = int(choice.get("max_x", 10) or 10)
        if x < 0 or x > max_x:
            return False, "Valor fora do limite"
        from .effects_lote16 import resolve_destroy_minions_in_deck_by_cost
        resolve_destroy_minions_in_deck_by_cost(state, player_id, x, choice.get("comparison") or "LESS_THAN_OR_EQUAL")
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "x": x})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "spend_extra_mana_buff_self":
        x = response.get("x", response.get("amount", 0))
        try:
            x = int(x)
        except Exception:
            return False, "Valor inválido"
        max_x = int(choice.get("max_x", 0) or 0)
        if x < 0 or x > max_x:
            return False, "Valor fora do limite"
        mid = choice.get("source_minion_id")
        found = state.find_minion(mid) if mid else None
        if not found:
            return False, "Lacaio fonte não está mais em campo"
        from .effects_lote16 import apply_spend_extra_mana_buff_self
        apply_spend_extra_mana_buff_self(state, player_id, found[0], x)
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "x": x})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "spend_extra_mana_add_copies_to_deck":
        x = response.get("x", response.get("amount", 0))
        try:
            x = int(x)
        except Exception:
            return False, "Valor inválido"
        max_x = int(choice.get("max_x", 0) or 0)
        if x < 0 or x > max_x:
            return False, "Valor fora do limite"
        position = (response.get("position") or choice.get("default_position") or "MIDDLE").upper()
        if position == "CHOSEN":
            position = "MIDDLE"
        if position not in ("TOP", "MIDDLE", "BOTTOM"):
            return False, "Posição inválida"
        mid = choice.get("source_minion_id")
        found = state.find_minion(mid) if mid else None
        if not found:
            return False, "Lacaio fonte não está mais em campo"
        from .effects_lote16 import apply_spend_extra_mana_add_copies_to_deck
        apply_spend_extra_mana_add_copies_to_deck(
            state, player_id, found[0], x,
            int(choice.get("copy_multiplier", 2) or 2), position
        )
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "x": x, "position": position})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "heal_opponent_and_draw_scaling":
        try:
            idx = int(response.get("index", response.get("chose_index", 0)))
        except Exception:
            return False, "Opção inválida"
        options = list(choice.get("options") or [])
        if idx < 0 or idx >= len(options):
            return False, "Opção inválida"
        opt = options[idx]
        from .effects import heal_character, draw_card
        heal_character(state, state.opponent_of(player_id), int(opt.get("heal_amount", 0) or 0))
        draw_card(state, state.players[player_id], int(opt.get("draw_amount", 0) or 0))
        resume_end_turn = choice.get("resume_end_turn")
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "index": idx})
        _resume_choice_effects(state, choice)
        if resume_end_turn and state.pending_choice is None:
            _continue_end_turn_after_choice(state, resume_end_turn)
        return True, "OK"

    if kind == "heal_or_revive_friendly":
        selected = response.get("target_id") or response.get("id") or response.get("minion_id")
        allowed = {o.get("id") or o.get("instance_id") for o in (choice.get("options") or [])}
        if selected not in allowed:
            return False, "Alvo inválido"
        amount = int(choice.get("amount", 0) or 0)
        from .effects import heal_character
        if isinstance(selected, str) and selected.startswith("dead:"):
            try:
                idx = int(selected.split(":", 1)[1])
            except Exception:
                return False, "Lacaio inválido"
            if idx < 0 or idx >= len(state.graveyard):
                return False, "Lacaio inválido"
            rec = state.graveyard[idx]
            if rec.get("owner") != player_id:
                return False, "Lacaio inválido"
            from .effects_lote13 import _dead_this_turn_heal_health, _resurrect_card
            revived_health = _dead_this_turn_heal_health(rec, amount, state.turn_number)
            if revived_health is None:
                return False, "Este lacaio não pode ser revivido por esta cura"
            _resurrect_card(state, player_id, rec.get("card_id"), health=revived_health)
        else:
            targets = targeting.resolve_targets(
                state, {"mode": "CHOSEN", "valid": ["FRIENDLY_CHARACTER"]},
                player_id, None, selected
            )
            if not targets:
                return False, "Alvo inválido"
            heal_character(state, targets[0], amount)
        resume_end_turn = choice.get("resume_end_turn")
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "target_id": selected})
        _resume_choice_effects(state, choice)
        if resume_end_turn and state.pending_choice is None:
            _continue_end_turn_after_choice(state, resume_end_turn)
        return True, "OK"

    if kind == "choose_friendly_minion_to_devour":
        selected = response.get("target_id") or response.get("minion_id") or response.get("id")
        allowed = {m.get("instance_id") for m in (choice.get("minions") or [])}
        if selected not in allowed:
            return False, "Lacaio inválido"
        mid = choice.get("source_minion_id")
        found = state.find_minion(mid) if mid else None
        if not found:
            return False, "Lacaio fonte não está mais em campo"
        source_minion = found[0]
        eff = {
            "action": "DEVOUR_FRIENDLY_MINION_GAIN_ATTRIBUTES",
            "bonus_attack": 1,
            "bonus_health": 1,
            "copy_text": True,
            "target": {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]},
        }
        resume_end_turn = choice.get("resume_end_turn")
        state.pending_choice = None
        effects.resolve_effect(state, eff, player_id, source_minion, {"victim_id": selected})
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "target_id": selected})
        _resume_choice_effects(state, choice)
        if resume_end_turn and state.pending_choice is None:
            _continue_end_turn_after_choice(state, resume_end_turn)
        return True, "OK"


    if kind == "choose_n_keywords":
        selected = response.get("selected_keywords") or response.get("keywords") or []
        if not isinstance(selected, list):
            return False, "Lista de palavras-chave inválida"
        choices = list(choice.get("choices") or [])
        choose = int(choice.get("choose", 1) or 1)
        if len(selected) != choose:
            return False, f"Escolha exatamente {choose}"
        if any(tag not in choices for tag in selected):
            return False, "Palavra-chave inválida"
        mid = choice.get("source_minion_id")
        found = state.find_minion(mid) if mid else None
        if not found:
            return False, "Lacaio fonte não está mais em campo"
        from .effects_lote17 import apply_choose_n_keywords
        apply_choose_n_keywords(state, player_id, found[0], choices, selected, choose)
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "selected_keywords": selected})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "choose_x_damage_self_player_summon":
        x = response.get("x")
        try:
            x = int(x)
        except Exception:
            return False, "Valor inválido"
        choices = [int(v) for v in (choice.get("choices") or [])]
        if x not in choices:
            return False, "Valor fora das opções"
        from .effects_lote17 import apply_choose_x_damage_self_player_summon
        apply_choose_x_damage_self_player_summon(
            state, player_id, int(choice.get("amount", 0) or 0),
            x, choice.get("summon_card_id")
        )
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "x": x})
        _resume_choice_effects(state, choice)
        cleanup(state)
        return True, "OK"

    if kind == "redistribute_self_stats":
        if response.get("skip") and choice.get("optional"):
            state.pending_choice = None
            state.log_event({"type": "choice_resolved", "kind": kind,
                             "player": player_id, "skip": True})
            _resume_choice_effects(state, choice)
            return True, "OK"
        attack_value = response.get("attack")
        if attack_value is None:
            attack_value = response.get("attack_value")
        try:
            attack_value = int(attack_value)
        except Exception:
            return False, "Ataque inválido"
        total = int(choice.get("total", 0) or 0)
        if attack_value < 0 or attack_value > total:
            return False, "Ataque fora do limite"
        mid = choice.get("source_minion_id")
        found = state.find_minion(mid) if mid else None
        if not found:
            return False, "Lacaio fonte não está mais em campo"
        from .effects_lote17 import apply_redistribute_self_stats
        apply_redistribute_self_stats(state, found[0], attack_value)
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "attack": attack_value})
        _resume_choice_effects(state, choice)
        cleanup(state)
        return True, "OK"


    if kind == "mario_reveal_top_choose_draw":
        choose_revealed = bool(response.get("choose_revealed"))
        drawn = choice.get("drawn_card") or {}
        revealed = choice.get("revealed_card_id")
        if not revealed or not drawn.get("instance_id"):
            return False, "Escolha inválida"
        if choose_revealed and (not p.deck or p.deck[0] != revealed):
            return False, "O topo do deck mudou; escolha expirada"
        from .effects_lote25_requested_fixes import resolve_mario_choice
        resolve_mario_choice(state, player_id, drawn.get("instance_id"), revealed, choose_revealed)
        state.pending_choice = None
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id,
                         "choose_revealed": choose_revealed})
        _resume_choice_effects(state, choice)
        return True, "OK"

    if kind == "choose_one_effect":
        try:
            idx = int(response.get("index", response.get("chose_index", 0)))
        except Exception:
            return False, "Opção inválida"
        choices = list(choice.get("choices") or [])
        if idx < 0 or idx >= len(choices):
            return False, "Opção inválida"
        source_minion = None
        mid = choice.get("source_minion_id")
        if mid:
            found = state.find_minion(mid)
            if found:
                source_minion = found[0]
        ctx = dict(choice.get("ctx") or {})
        ctx["chose_index"] = idx
        from .effects import resolve_effect
        state.pending_choice = None
        resolve_effect(state, choices[idx], player_id, source_minion, ctx)
        state.log_event({"type": "choice_resolved", "kind": kind,
                         "player": player_id, "index": idx})
        _resume_choice_effects(state, choice)
        cleanup(state)
        return True, "OK"

    return False, f"Tipo de escolha não suportado: {kind}"


def _find_attack_redirector(state: GameState, defender_pid: int, original_target):
    """Retorna um lacaio aliado que redireciona ataques para si.

    Lucas usa trigger ON_FRIENDLY_CHARACTER_ATTACKED + REDIRECT_ATTACK_TO_SELF.
    Em vez de depender de um segundo fluxo de decisão, o ataque consulta esse
    efeito diretamente antes de calcular dano.
    """
    for m in list(state.players[defender_pid].board):
        if m is original_target:
            continue
        if m.silenced or m.health <= 0 or m.immune or m.has_tag("STEALTH"):
            continue
        for eff in m.effects or []:
            if eff.get("action") == "REDIRECT_ATTACK_TO_SELF":
                return m
        if m.has_tag("REDIRECT_ATTACK_TO_SELF"):
            return m
    return None


def _is_attack_forbidden_by_restriction(state: GameState, attacker: Minion, target) -> bool:
    """Checa restrições do tipo 'este lacaio não pode atacar aquele alvo'."""
    if not isinstance(target, Minion):
        return False
    keep = []
    forbidden = False
    for pm in state.pending_modifiers:
        if pm.get("kind") != "cannot_attack_target":
            keep.append(pm)
            continue
        source_id = pm.get("source_minion_id")
        if source_id and state.find_minion(source_id) is None:
            # O lacaio que impôs a restrição saiu de campo; expira.
            continue
        if pm.get("attacker_id") == attacker.instance_id and pm.get("target_id") == target.instance_id:
            forbidden = True
        keep.append(pm)
    state.pending_modifiers = keep
    return forbidden


def _activation_effects_for_minion(minion: Minion) -> list[dict]:
    """Efeitos ativáveis de um lacaio em campo."""
    if minion.silenced:
        return []
    return [
        eff for eff in (minion.effects or [])
        if eff.get("trigger") in ("ACTIVATED_ABILITY", "DURING_YOUR_TURN")
    ]


def _chosen_targets_for_effect(eff: dict) -> list[dict]:
    """Versão local mínima para validar alvo de habilidade ativada."""
    out = []
    target = eff.get("target") or {}
    if isinstance(target, dict) and target.get("mode") == "CHOSEN":
        out.append(target)
    source = eff.get("source") or {}
    if isinstance(source, dict) and source.get("mode") == "CHOSEN":
        out.insert(0, source)
    for sub in eff.get("effects") or []:
        out.extend(_chosen_targets_for_effect(sub))
    for sub in eff.get("additional_effects") or []:
        out.extend(_chosen_targets_for_effect(sub))
    return out


def activate_ability(state: GameState, player_id: int, minion_instance_id: str,
                     ability_index: int = 0,
                     chosen_target: Optional[str] = None,
                     chosen_targets: Optional[list[str]] = None,
                     zone: Optional[str] = None,
                     position: Optional[int | str] = None) -> tuple[bool, str]:
    """Ativa uma habilidade de lacaio em campo.

    Cobre:
    - ACTIVATED_ABILITY: ex. Ramoninho Mestre da Nerf.
    - DURING_YOUR_TURN: ex. Rica Coelinho.
    """
    if state.pending_choice is not None:
        return False, "Resolva a escolha pendente antes de agir"
    if state.current_player != player_id or state.phase != "PLAYING":
        return False, "Não é seu turno"

    found = state.find_minion(minion_instance_id)
    if not found:
        return False, "Lacaio inválido"
    minion, owner = found
    if owner != player_id:
        return False, "Você só pode ativar habilidades dos seus lacaios"
    if minion.silenced:
        return False, "Lacaio silenciado não pode ativar habilidade"
    if minion.has_tag("DORMANT") or minion.immune:
        return False, "Lacaio dormente/imune não pode ativar habilidade"
    if minion.frozen:
        return False, "Lacaio congelado não pode ativar habilidade"

    effects_to_use = _activation_effects_for_minion(minion)
    if not effects_to_use:
        return False, "Este lacaio não tem habilidade ativável"
    try:
        ability_index = int(ability_index or 0)
    except Exception:
        ability_index = 0
    if ability_index < 0 or ability_index >= len(effects_to_use):
        return False, "Habilidade inválida"
    eff = effects_to_use[ability_index]
    trigger = eff.get("trigger")
    p = state.players[player_id]

    use_key = str(ability_index)
    # Ramoninho: o número da habilidade representa usos totais, não mana.
    has_total_uses = eff.get("activation_uses") is not None or minion.card_id == "ramoninho_mestre_da_nerf"
    if has_total_uses:
        total_uses = int(eff.get("activation_uses", 3) or 3)
        if use_key not in minion.ability_uses_remaining:
            minion.ability_uses_remaining[use_key] = total_uses
        if minion.ability_uses_remaining[use_key] <= 0:
            return False, "Habilidade sem usos restantes"
        cost = 0
    else:
        if minion.activated_abilities_this_turn > 0:
            return False, "Habilidade já usada neste turno"
        cost = int(eff.get("activation_cost", 0) or 0)

    if p.mana < cost:
        return False, f"Mana insuficiente ({p.mana}/{cost})"

    if chosen_targets is None:
        chosen_targets = [chosen_target] if chosen_target is not None else []
    chosen_descs = _chosen_targets_for_effect(eff)
    for idx, desc in enumerate(chosen_descs):
        is_spell_like = trigger == "ACTIVATED_ABILITY"
        if not targeting.has_valid_chosen_target(state, desc, player_id,
                                                source_minion=minion,
                                                is_spell=is_spell_like):
            return False, "Esta habilidade exige um alvo válido"
        target_id = chosen_targets[idx] if idx < len(chosen_targets) else None
        if target_id is None:
            return False, "Esta habilidade exige um alvo"
        if not targeting.resolve_targets(state, desc, player_id, minion, target_id,
                                         is_spell=is_spell_like):
            return False, "Alvo inválido"

    chosen_target = chosen_targets[0] if chosen_targets else None

    # Validação específica da Rica Coelinho antes de gastar/consumir uso.
    if trigger == "DURING_YOUR_TURN" and eff.get("action") == "MOVE_SELF_TO_ZONE":
        valid_zones = [str(z).upper() for z in (eff.get("valid_zones") or [])]
        wanted_zone = str(zone or "HAND").upper()
        if wanted_zone not in valid_zones:
            return False, "Zona inválida para esta habilidade"
        if wanted_zone == "BOARD_POSITION":
            try:
                int(position)
            except Exception:
                return False, "Posição inválida"

    p.mana -= cost
    if has_total_uses:
        minion.ability_uses_remaining[use_key] -= 1
    else:
        minion.activated_abilities_this_turn += 1
    state.log_event({
        "type": "activate_ability",
        "player": player_id,
        "minion": minion.instance_id,
        "card_id": minion.card_id,
        "trigger": trigger,
        "action": eff.get("action"),
        "cost": cost,
        "uses_remaining": minion.ability_uses_remaining.get(use_key) if has_total_uses else None,
    })

    ctx = {
        "chosen_target": chosen_target,
        "target_queue": list(chosen_targets),
        "target_cursor": 0,
        "source_trigger": trigger,
        "source_card_id": minion.card_id,
        "zone": zone,
        "target_zone": zone,
        "position": position,
    }
    effects.resolve_effect(state, eff, player_id, minion, ctx)
    cleanup(state)
    return True, "OK"

def attack(state: GameState, player_id: int, attacker_instance_id: str,
           target_id: str) -> tuple[bool, str]:
    """attacker_instance_id é id de minion. target_id pode ser 'hero:0', 'hero:1' ou id de minion."""
    if state.pending_choice is not None:
        return False, "Resolva a escolha pendente antes de agir"
    if state.current_player != player_id or state.phase != "PLAYING":
        return False, "Não é seu turno"
    p = state.players[player_id]
    foe = state.opponent_of(player_id)

    # localiza atacante
    attacker = next((m for m in p.board if m.instance_id == attacker_instance_id), None)
    if attacker is None:
        return False, "Atacante inválido"
    if not attacker.can_attack():
        return False, "Lacaio não pode atacar agora"

    # localiza alvo
    if target_id.startswith("hero:"):
        target_pid = int(target_id.split(":")[1])
        if target_pid == player_id:
            return False, "Não pode atacar próprio herói"
        target = foe
    else:
        tgt = state.find_minion(target_id)
        if tgt is None:
            return False, "Alvo inválido"
        target_minion, target_owner = tgt
        if target_owner == player_id:
            return False, "Não pode atacar próprios lacaios"
        if target_minion.has_tag("DORMANT"):
            return False, "Lacaio dormente não pode ser atacado"
        target = target_minion

    # valida TAUNT no inimigo: se houver lacaio com TAUNT, deve atacar ele primeiro.
    # Provocar imune não conta - senão o jogador fica sem alvo legal e o jogo trava.
    enemy_taunts = [m for m in foe.board if m.has_tag("TAUNT") and not m.has_tag("STEALTH") and not m.immune]
    if enemy_taunts:
        if isinstance(target, PlayerState):
            return False, "Há lacaios com Provocar"
        if isinstance(target, Minion) and not target.has_tag("TAUNT"):
            return False, "Há lacaios com Provocar"

    # valida STEALTH no alvo
    if isinstance(target, Minion) and target.has_tag("STEALTH"):
        return False, "Lacaio camuflado não pode ser atacado"

    # Rush não pode atacar herói no turno em que entra
    if isinstance(target, PlayerState):
        if not attacker.can_attack_hero():
            return False, "Lacaio com Investida (Rush) não pode atacar heróis no turno em que entra"

    # valida imune
    if isinstance(target, Minion) and target.immune:
        return False, "Alvo imune"
    if isinstance(target, PlayerState) and target.hero_immune:
        return False, "Herói imune"

    # Redirecionamento defensivo: se um lacaio aliado ao defensor tem efeito
    # REDIRECT_ATTACK_TO_SELF, ele recebe o ataque no lugar do alvo escolhido.
    defender_pid = target.owner if isinstance(target, Minion) else target.player_id
    redirector = _find_attack_redirector(state, defender_pid, target)
    if redirector is not None:
        state.log_event({"type": "attack_redirected",
                         "from": target.instance_id if isinstance(target, Minion) else f"hero:{target.player_id}",
                         "to": redirector.instance_id})
        target = redirector

    if _is_attack_forbidden_by_restriction(state, attacker, target):
        return False, "Este lacaio não pode atacar esse alvo"

    # quebra stealth do atacante (mas não se for PERMANENT_STEALTH)
    if attacker.has_tag("STEALTH") and not attacker.has_tag("PERMANENT_STEALTH"):
        attacker.tags.remove("STEALTH")

    # aplica dano em ambos os lados (lacaio vs lacaio: troca dano simultânea)
    attacker_atk = attacker.attack
    attack_ctx = {
        "attack_target_id": target.instance_id if isinstance(target, Minion) else f"hero:{target.player_id}",
        "chosen_target": target.instance_id if isinstance(target, Minion) else f"hero:{target.player_id}",
        "attack_target_owner": target.owner if isinstance(target, Minion) else target.player_id,
    }
    adjacent_damage_instead = (
        isinstance(target, Minion)
        and any(
            eff.get("trigger") == "ON_ATTACK_MINION"
            and eff.get("action") == "DAMAGE_ADJACENT_MINIONS_INSTEAD"
            for eff in (attacker.effects or [])
        )
        and not attacker.silenced
    )

    if isinstance(target, Minion):
        target_atk = target.attack
        # Viní de Aimbot: substitui o dano causado ao alvo por dano aos adjacentes.
        if adjacent_damage_instead:
            effects.fire_minion_trigger(state, attacker, "ON_ATTACK_MINION", extra_ctx=attack_ctx)
        # ATTACK_DAMAGE_IMMUNE: alvo não recebe dano de ataque
        dmg_to_target = 0 if (target.has_tag("ATTACK_DAMAGE_IMMUNE") or adjacent_damage_instead) else attacker_atk
        dmg_to_attacker = 0 if attacker.has_tag("ATTACK_DAMAGE_IMMUNE") else target_atk
        # atacante sofre dano da defesa
        effects.damage_character(state, target, dmg_to_target, source_owner=player_id,
                                 source_minion=attacker, is_attack=True)
        effects.damage_character(state, attacker, dmg_to_attacker, source_owner=foe.player_id,
                                 source_minion=target, is_attack=True)
        state.log_event({
            "type": "attack",
            "attacker": attacker.instance_id,
            "attacker_name": attacker.name,
            "attacker_card_id": attacker.card_id,
            "attacker_owner": attacker.owner,
            "target": target.instance_id,
            "target_name": target.name,
            "target_card_id": target.card_id,
            "target_owner": target.owner,
        })
    else:  # herói
        effects.damage_character(state, target, attacker_atk, source_owner=player_id,
                                 source_minion=attacker, is_attack=True)
        state.log_event({
            "type": "attack",
            "attacker": attacker.instance_id,
            "attacker_name": attacker.name,
            "attacker_card_id": attacker.card_id,
            "attacker_owner": attacker.owner,
            "target": f"hero:{target.player_id}",
            "target_name": f"Herói de P{target.player_id}",
            "target_owner": target.player_id,
        })

    attacker.attacks_this_turn += 1

    # triggers AFTER_ATTACK
    effects.fire_minion_trigger(state, attacker, "AFTER_ATTACK", extra_ctx=attack_ctx)
    if isinstance(target, Minion):
        if not adjacent_damage_instead:
            effects.fire_minion_trigger(state, attacker, "ON_ATTACK_MINION", extra_ctx=attack_ctx)
        effects.fire_minion_trigger(state, target, "ON_ATTACK_MINION")

    cleanup(state)
    return True, "OK"


def cleanup(state: GameState):
    """Remove lacaios mortos, dispara deathrattles, checa fim de jogo. Pode iterar."""
    iterations = 0
    while iterations < 50:
        iterations += 1
        # Recalcula auras a cada iteração: se um deathrattle anterior matou
        # uma fonte de aura, dependentes precisam ter status ajustado antes
        # de detectar a próxima onda de mortes.
        apply_continuous_effects(state)
        # coleta mortos
        dead: list[tuple[Minion, int]] = []
        for p in state.players:
            for m in list(p.board):
                if m.health <= 0 and not m.immune:
                    dead.append((m, p.player_id))
        if not dead:
            break
        # processa deathrattles em ordem
        for minion, owner in dead:
            # remove do campo
            owner_p = state.players[owner]
            if minion not in owner_p.board:
                continue

            # Lote 17: efeitos especiais que podem substituir a morte
            # ou aplicar bônus antes do cemitério/deathrattle padrão.
            try:
                from .effects_lote17 import handle_minion_death_specials
                if handle_minion_death_specials(state, minion, owner):
                    continue
            except Exception:
                pass

            # Captura posição original ANTES de remover, para que deathrattles
            # possam reposicionar tokens (ex: "evoque um lacaio onde eu morri").
            try:
                death_index = owner_p.board.index(minion)
            except ValueError:
                death_index = None

            if minion in owner_p.board:
                # Lamboia Religioso: substitui morte por embaralhar no deck.
                try:
                    from .effects_lote16 import find_death_replacement_shuffle_into_deck
                    replacement_position = find_death_replacement_shuffle_into_deck(state, minion, owner)
                except Exception:
                    replacement_position = None
                if replacement_position:
                    owner_p.board.remove(minion)
                    try:
                        from .effects_lote16 import _insert_cards
                        _insert_cards(owner_p.deck, [minion.card_id], replacement_position)
                    except Exception:
                        owner_p.deck.append(minion.card_id)
                    state.log_event({
                        "type": "death_replaced_shuffle_into_deck",
                        "owner": owner,
                        "minion": minion.instance_id,
                        "card_id": minion.card_id,
                        "position": replacement_position,
                    })
                    continue

                owner_p.board.remove(minion)
            # adiciona ao cemitério (pra RESURRECT_LAST_FRIENDLY_DEAD_MINION)
            state.graveyard.append({
                "card_id": minion.card_id,
                "owner": owner,
                "name": minion.name,
                "turn_number": state.turn_number,
                "health_at_death": minion.health,
                "max_health_at_death": minion.max_health,
            })
            state.log_event({"type": "death", "minion": minion.instance_id,
                             "name": minion.name, "card_id": minion.card_id,
                             "owner": owner, "death_index": death_index})
            # ON_DEATH com death_index disponível em ctx.
            effects.fire_minion_trigger(
                state, minion, "ON_DEATH",
                extra_ctx={"death_index": death_index, "death_owner": owner},
            )

    # Atualiza efeitos sustentados por lacaios que podem ter morrido/sumido.
    for m in list(state.all_minions()):
        _has_active_freeze_until_source(state, m.instance_id)

    # checa fim de jogo
    p0_dead = state.players[0].is_dead()
    p1_dead = state.players[1].is_dead()
    if p0_dead and p1_dead:
        state.phase = "ENDED"
        state.winner = -1
        state.log_event({"type": "game_end", "winner": -1})
    elif p0_dead:
        state.phase = "ENDED"
        state.winner = 1
        state.log_event({"type": "game_end", "winner": 1})
    elif p1_dead:
        state.phase = "ENDED"
        state.winner = 0
        state.log_event({"type": "game_end", "winner": 0})


# ======================= AÇÕES LEGAIS (pra UI / IA) =======================

def list_playable_cards(state: GameState, player_id: int) -> list[str]:
    """Retorna instance_ids de cartas que o jogador pode jogar agora."""
    if state.current_player != player_id or state.phase != "PLAYING":
        return []
    p = state.players[player_id]
    out = []
    for c in p.hand:
        card = get_card(c.card_id)
        if not card:
            continue
        cost = compute_dynamic_cost(state, p, c, card)
        if p.mana < cost:
            continue
        if card.get("type") == "MINION" and len(p.board) >= MAX_BOARD_SIZE:
            continue
        chosen_desc = targeting.needs_chosen_target(card)
        if chosen_desc and not targeting.has_valid_chosen_target(state, chosen_desc, player_id, is_spell=(card.get("type") == "SPELL")):
            if card.get("type") == "SPELL":
                continue
        out.append(c.instance_id)
    return out


def list_legal_attack_targets(state: GameState, player_id: int,
                               attacker_id: str) -> list[str]:
    if state.current_player != player_id or state.phase != "PLAYING":
        return []
    p = state.players[player_id]
    foe = state.opponent_of(player_id)
    attacker = next((m for m in p.board if m.instance_id == attacker_id), None)
    if not attacker or not attacker.can_attack():
        return []

    enemy_taunts = [m for m in foe.board if m.has_tag("TAUNT") and not m.has_tag("STEALTH") and not m.immune]
    targets = []
    if enemy_taunts:
        for m in enemy_taunts:
            targets.append(m.instance_id)
    else:
        for m in foe.board:
            if m.has_tag("STEALTH"):
                continue
            if m.immune:
                continue
            targets.append(m.instance_id)
        if attacker.can_attack_hero() and not foe.hero_immune:
            targets.append(f"hero:{foe.player_id}")
    return targets
