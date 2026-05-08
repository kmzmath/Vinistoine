"""Lote 17 — handlers especiais finais.

Ações cobertas:
- CAST_CARD_ON_MINIONS
- CHOOSE_N_KEYWORDS
- CHOOSE_X_DAMAGE_SELF_PLAYER_SUMMON
- COPY_SELF_STATS_TO_MINION
- GAIN_COPY_ATTRIBUTES
- MARK_FRIENDLY_MINION_FOR_SELF_BUFF_ON_OPPONENT_KILL
- MARK_KILL_TARGET_RETURN_BOTH_TO_HAND
- REDISTRIBUTE_SELF_STATS
- SLEEP
"""
from __future__ import annotations

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, gen_id
from .cards import get_card
from . import targeting


def _add_card_to_hand(state, player: PlayerState, card_id: str):
    if len(player.hand) >= MAX_HAND_SIZE:
        state.log_event({"type": "burn", "player": player.player_id, "card_id": card_id})
        return None
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    player.hand.append(ch)
    state.log_event({"type": "add_to_hand", "player": player.player_id,
                     "card_id": card_id, "instance_id": ch.instance_id})
    return ch


def apply_choose_n_keywords(state, source_owner: int, source_minion: Minion | None,
                            choices: list[str], selected: list[str], choose: int):
    if not source_minion:
        return []
    valid = [c for c in choices if isinstance(c, str)]
    out = []
    seen = set()
    for tag in selected or []:
        if tag in seen:
            continue
        if tag in valid:
            out.append(tag)
            seen.add(tag)
        if len(out) >= choose:
            break
    for tag in out:
        if tag not in source_minion.tags:
            source_minion.tags.append(tag)
        if tag == "DIVINE_SHIELD":
            source_minion.divine_shield = True
        if tag == "RUSH":
            # Rapidez deve funcionar imediatamente se ele acabou de entrar.
            source_minion.summoning_sick = True
        state.log_event({"type": "add_tag", "minion": source_minion.instance_id,
                         "tag": tag})
    return out


def apply_choose_x_damage_self_player_summon(state, source_owner: int, amount: int,
                                             x: int, summon_card_id: str):
    from .effects import damage_character, summon_minion_from_card
    p = state.players[source_owner]
    x = max(0, int(x or 0))
    for _ in range(x):
        damage_character(state, p, int(amount or 0), source_owner=source_owner)
        if summon_card_id:
            summon_minion_from_card(state, source_owner, summon_card_id)
    state.log_event({"type": "choose_x_damage_self_player_summon",
                     "player": source_owner, "x": x,
                     "damage_each": amount, "summon_card_id": summon_card_id})


def apply_redistribute_self_stats(state, source_minion: Minion, attack_value: int):
    total = max(0, int(source_minion.attack) + int(source_minion.health))
    if total <= 0:
        return
    atk = max(0, min(int(attack_value), total))
    hp = max(1, total - atk) if total > 0 else 0
    # Evita estado 0/0 gerado por escolha ruim; 0 ataque é válido.
    source_minion.attack = atk
    source_minion.health = hp
    source_minion.max_health = max(source_minion.max_health, hp)
    state.log_event({"type": "redistribute_self_stats",
                     "minion": source_minion.instance_id,
                     "attack": atk, "health": hp})


def register_lote17_handlers(handler):
    @handler("CAST_CARD_ON_MINIONS")
    def _cast_card_on_minions(state, eff, source_owner, source_minion, ctx):
        """Gusnabo Sagrado: conjura uma carta em cada lacaio alvo."""
        card_id = eff.get("card_id")
        spell = get_card(card_id) if card_id else None
        if not spell:
            state.log_event({"type": "cast_card_on_minions_failed",
                             "reason": "invalid_card", "card_id": card_id})
            return
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"),
                                            is_spell=False)
        from .effects import resolve_card_effects
        for t in list(targets):
            if not isinstance(t, Minion):
                continue
            if state.find_minion(t.instance_id) is None:
                continue
            resolve_card_effects(state, spell, source_owner, "ON_PLAY",
                                 source_minion=None,
                                 chosen_target=t.instance_id,
                                 is_spell=True,
                                 extra_ctx={"source_trigger": "ON_PLAY"})
            state.log_event({"type": "cast_card_on_minion",
                             "card_id": card_id, "target": t.instance_id})

    @handler("CHOOSE_N_KEYWORDS")
    def _choose_n_keywords(state, eff, source_owner, source_minion, ctx):
        choices = list(eff.get("choices") or [])
        choose = int(eff.get("choose", 1) or 1)
        if not source_minion:
            return
        if getattr(state, "manual_choices", False) and ctx.get("selected_keywords") is None:
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "choose_n_keywords",
                "owner": source_owner,
                "source_minion_id": source_minion.instance_id,
                "choices": choices,
                "choose": choose,
            }
            state.log_event({"type": "choice_required",
                             "kind": "choose_n_keywords",
                             "player": source_owner})
            return
        selected = ctx.get("selected_keywords")
        if not isinstance(selected, list):
            selected = choices[:choose]
        apply_choose_n_keywords(state, source_owner, source_minion, choices, selected, choose)

    @handler("CHOOSE_X_DAMAGE_SELF_PLAYER_SUMMON")
    def _choose_x_damage_self_player_summon(state, eff, source_owner, source_minion, ctx):
        choices = [int(x) for x in (eff.get("choices") or [1])]
        amount = int(eff.get("amount", 0) or 0)
        summon_card_id = eff.get("summon_card_id")
        if getattr(state, "manual_choices", False) and ctx.get("x") is None:
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "choose_x_damage_self_player_summon",
                "owner": source_owner,
                "choices": choices,
                "amount": amount,
                "summon_card_id": summon_card_id,
            }
            state.log_event({"type": "choice_required",
                             "kind": "choose_x_damage_self_player_summon",
                             "player": source_owner})
            return
        try:
            x = int(ctx.get("x", max(choices)))
        except Exception:
            x = max(choices)
        if x not in choices:
            x = max(choices)
        apply_choose_x_damage_self_player_summon(state, source_owner, amount, x, summon_card_id)

    @handler("COPY_SELF_STATS_TO_MINION")
    def _copy_self_stats_to_minion(state, eff, source_owner, source_minion, ctx):
        if not source_minion:
            return
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        for t in targets:
            if not isinstance(t, Minion) or t is source_minion:
                continue
            t.attack = source_minion.attack
            t.health = source_minion.health
            t.max_health = source_minion.max_health
            state.log_event({"type": "copy_self_stats_to_minion",
                             "source": source_minion.instance_id,
                             "target": t.instance_id})

    @handler("GAIN_COPY_ATTRIBUTES")
    def _gain_copy_attributes(state, eff, source_owner, source_minion, ctx):
        """Handler marcador. A aplicação real ocorre no cleanup quando cópia morre."""
        if source_minion and "GAIN_COPY_ATTRIBUTES_ON_COPY_DEATH" not in source_minion.tags:
            source_minion.tags.append("GAIN_COPY_ATTRIBUTES_ON_COPY_DEATH")

    @handler("MARK_FRIENDLY_MINION_FOR_SELF_BUFF_ON_OPPONENT_KILL")
    def _mark_friendly_minion_for_self_buff_on_opponent_kill(state, eff, source_owner, source_minion, ctx):
        if not source_minion:
            return
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        if not targets:
            return
        target = next((t for t in targets if isinstance(t, Minion)), None)
        if not target:
            return
        state.pending_modifiers.append({
            "kind": "buff_source_if_target_killed_by_opponent",
            "source_minion_id": source_minion.instance_id,
            "target_id": target.instance_id,
            "owner": source_owner,
            "buff_attack": int(eff.get("buff_attack", 0) or 0),
        })
        state.log_event({"type": "mark_friendly_minion_for_self_buff_on_opponent_kill",
                         "source": source_minion.instance_id,
                         "target": target.instance_id})

    @handler("MARK_KILL_TARGET_RETURN_BOTH_TO_HAND")
    def _mark_kill_target_return_both_to_hand(state, eff, source_owner, source_minion, ctx):
        if not source_minion:
            return
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        target = next((t for t in targets if isinstance(t, Minion)), None)
        if not target:
            return
        state.pending_modifiers.append({
            "kind": "return_both_to_hand_if_source_kills_target",
            "source_minion_id": source_minion.instance_id,
            "target_id": target.instance_id,
            "owner": source_owner,
        })
        state.log_event({"type": "mark_kill_target_return_both_to_hand",
                         "source": source_minion.instance_id,
                         "target": target.instance_id})

    @handler("REDISTRIBUTE_SELF_STATS")
    def _redistribute_self_stats(state, eff, source_owner, source_minion, ctx):
        if not source_minion:
            return
        # Condição usada por La torre de Pisa: só após atacar lacaio.
        cond = eff.get("condition") or {}
        if cond.get("target_type") == "MINION":
            target_id = ctx.get("attack_target_id") or ctx.get("chosen_target")
            found = state.find_minion(target_id) if target_id else None
            if found is None:
                return

        total = max(0, source_minion.attack + source_minion.health)
        if getattr(state, "manual_choices", False) and ctx.get("attack_value") is None:
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "redistribute_self_stats",
                "owner": source_owner,
                "source_minion_id": source_minion.instance_id,
                "total": total,
                "optional": bool(eff.get("optional")),
            }
            state.log_event({"type": "choice_required",
                             "kind": "redistribute_self_stats",
                             "player": source_owner})
            return
        if eff.get("optional") and ctx.get("skip"):
            return
        attack_value = ctx.get("attack_value")
        if attack_value is None:
            return  # fallback: mantém como está
        apply_redistribute_self_stats(state, source_minion, int(attack_value))

    @handler("SLEEP")
    def _sleep(state, eff, source_owner, source_minion, ctx):
        """Viní Dorminhoco: quando morre, volta dormente ao campo."""
        from .effects import summon_minion_from_card
        card_id = ctx.get("source_card_id") or (source_minion.card_id if source_minion else None)
        if not card_id:
            return
        m = summon_minion_from_card(state, source_owner, card_id)
        if not m:
            return
        if "DORMANT" not in m.tags:
            m.tags.append("DORMANT")
        m.cant_attack = True
        m.immune = True
        m.summoning_sick = True
        # Reseta contador de despertar.
        state.pending_modifiers.append({
            "kind": "friendly_summons_awaken_counter",
            "target_id": m.instance_id,
            "owner": source_owner,
            "count": 0,
            "required": 2,
        })
        state.log_event({"type": "sleep", "minion": m.instance_id, "card_id": card_id})


def register_copy_relationship_for_summon(state, source_minion: Minion, copy_minion: Minion):
    """Chamado por SUMMON_COPY para Spiideba."""
    has_gain = any(
        eff.get("trigger") == "ON_SUMMONED_COPY_DEATH" and eff.get("action") == "GAIN_COPY_ATTRIBUTES"
        for eff in (source_minion.effects or [])
    )
    if not has_gain:
        return
    state.pending_modifiers.append({
        "kind": "copy_death_buff_original",
        "original_id": source_minion.instance_id,
        "copy_id": copy_minion.instance_id,
    })
    copy_minion.tags.append("_SUMMONED_COPY")
    state.log_event({"type": "copy_death_buff_registered",
                     "original": source_minion.instance_id,
                     "copy": copy_minion.instance_id})


def handle_minion_death_specials(state, minion: Minion, owner: int):
    """Processa efeitos especiais que dependem de um lacaio morrer.

    Retorna True quando a morte foi substituída e o cleanup deve pular
    cemitério/deathrattle padrão.
    """
    source_info = getattr(state, "last_damage_sources", {}).get(minion.instance_id, {})
    source_owner = source_info.get("source_owner")
    source_minion_id = source_info.get("source_minion_id")

    # Spiideba: cópia morreu, original ganha atributos.
    for pm in list(state.pending_modifiers):
        if pm.get("kind") == "copy_death_buff_original" and pm.get("copy_id") == minion.instance_id:
            found = state.find_minion(pm.get("original_id"))
            if found:
                original = found[0]
                original.attack += max(0, minion.attack)
                original.max_health += max(0, minion.max_health)
                original.health += max(0, minion.max_health)
                state.log_event({"type": "gain_copy_attributes",
                                 "original": original.instance_id,
                                 "copy": minion.instance_id,
                                 "attack": minion.attack,
                                 "health": minion.max_health})
            state.pending_modifiers.remove(pm)

    # Viní Sertanejo: alvo aliado morto pelo adversário buffa a fonte.
    for pm in list(state.pending_modifiers):
        if pm.get("kind") != "buff_source_if_target_killed_by_opponent":
            continue
        if pm.get("target_id") != minion.instance_id:
            continue
        if source_owner is None or source_owner == pm.get("owner"):
            continue
        found = state.find_minion(pm.get("source_minion_id"))
        if found:
            src = found[0]
            buff = int(pm.get("buff_attack", 0) or 0)
            src.attack += buff
            state.log_event({"type": "self_buff_on_opponent_kill",
                             "source": src.instance_id,
                             "target": minion.instance_id,
                             "buff_attack": buff})
        state.pending_modifiers.remove(pm)

    # Vic: se a fonte marcada matou o alvo, ambos voltam para a mão do dono da Vic.
    for pm in list(state.pending_modifiers):
        if pm.get("kind") != "return_both_to_hand_if_source_kills_target":
            continue
        if pm.get("target_id") != minion.instance_id:
            continue
        if source_minion_id != pm.get("source_minion_id"):
            continue
        owner_id = int(pm.get("owner"))
        owner_state = state.players[owner_id]
        source_found = state.find_minion(source_minion_id)
        if source_found:
            source, source_owner_pid = source_found
            if source in state.players[source_owner_pid].board:
                state.players[source_owner_pid].board.remove(source)
            _add_card_to_hand(state, owner_state, source.card_id)
        # Remove o alvo do board dele e adiciona na mão do dono da Vic.
        target_owner_state = state.players[owner]
        if minion in target_owner_state.board:
            target_owner_state.board.remove(minion)
        _add_card_to_hand(state, owner_state, minion.card_id)
        state.pending_modifiers.remove(pm)
        state.log_event({"type": "return_both_to_hand_after_kill",
                         "source": source_minion_id,
                         "target": minion.instance_id,
                         "owner": owner_id})
        return True

    return False
