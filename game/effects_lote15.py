"""Lote 15 — estados/passivos complexos.

Ações cobertas:
- FREEZE_UNTIL_SELF_DIES
- LOCK_ALL_OTHER_MINIONS_FROM_ATTACKING
- IMMUNE_TO_TRIGGERED_EFFECTS
- CANT_ATTACK_WHILE_ONLY_FRIENDLY_MINION
- REDUCE_ATTACK_INSTEAD_OF_HEALTH
- APPLY_PERMANENT_ATTACK_HALF_STATUS
"""
from __future__ import annotations
import math

from .state import Minion
from . import targeting


def _has_effect_action(minion: Minion, action: str) -> bool:
    if minion.silenced:
        return False
    return any((eff.get("action") == action) for eff in (minion.effects or []))


def register_lote15_handlers(handler):
    @handler("FREEZE_UNTIL_SELF_DIES")
    def _freeze_until_self_dies(state, eff, source_owner, source_minion, ctx):
        """Viní Geladinho: congela alvo enquanto o lacaio fonte existir."""
        if not source_minion:
            return
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        for t in targets:
            if not isinstance(t, Minion):
                continue
            t.frozen = True
            t.freeze_pending = True
            state.pending_modifiers.append({
                "kind": "freeze_until_source_dies",
                "source_minion_id": source_minion.instance_id,
                "target_id": t.instance_id,
            })
            state.log_event({
                "type": "freeze_until_source_dies",
                "source": source_minion.instance_id,
                "target": t.instance_id,
            })

    @handler("LOCK_ALL_OTHER_MINIONS_FROM_ATTACKING")
    def _lock_all_other_minions_from_attacking(state, eff, source_owner, source_minion, ctx):
        """Blitz: todos exceto o alvo escolhido ficam travados por N turnos próprios."""
        excluded_desc = eff.get("excluded_target") or {}
        excluded = targeting.resolve_targets(state, excluded_desc, source_owner,
                                             source_minion, ctx.get("chosen_target"))
        excluded_ids = {m.instance_id for m in excluded if isinstance(m, Minion)}

        target_desc = dict(eff.get("target") or {})
        # Garante que target_queue/previous_target não quebre o fallback.
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        if not targets:
            targets = [m for m in state.all_minions() if m.instance_id not in excluded_ids]

        turns = int(eff.get("duration_turns", 2) or 2)
        for t in targets:
            if not isinstance(t, Minion) or t.instance_id in excluded_ids:
                continue
            if "ATTACK_LOCKED" not in t.tags:
                t.tags.append("ATTACK_LOCKED")
            state.pending_modifiers.append({
                "kind": "attack_lock",
                "target_id": t.instance_id,
                "owner": t.owner,
                "turns_remaining": turns,
            })
            state.log_event({"type": "attack_locked", "minion": t.instance_id,
                             "turns": turns})

    @handler("IMMUNE_TO_TRIGGERED_EFFECTS")
    def _immune_to_triggered_effects(state, eff, source_owner, source_minion, ctx):
        """Surdo: passivo de imunidade a um trigger, como ON_PLAY."""
        immune_trigger = eff.get("immune_trigger") or "ON_PLAY"
        targets = targeting.resolve_targets(state, eff.get("target") or {"mode": "SELF"},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        tag = f"TRIGGER_IMMUNE_{immune_trigger}"
        for t in targets:
            if isinstance(t, Minion) and tag not in t.tags:
                t.tags.append(tag)
                state.log_event({"type": "trigger_immunity",
                                 "minion": t.instance_id,
                                 "trigger": immune_trigger})

    @handler("CANT_ATTACK_WHILE_ONLY_FRIENDLY_MINION")
    def _cant_attack_while_only_friendly_minion(state, eff, source_owner, source_minion, ctx):
        """Marcador/aura. A aplicação real é recalculada em apply_continuous_effects."""
        if source_minion and "CANT_ATTACK_ONLY_FRIENDLY_AURA" not in source_minion.tags:
            source_minion.tags.append("CANT_ATTACK_ONLY_FRIENDLY_AURA")

    @handler("REDUCE_ATTACK_INSTEAD_OF_HEALTH")
    def _reduce_attack_instead_of_health(state, eff, source_owner, source_minion, ctx):
        """Marcador. O redirecionamento de dano é feito em damage_character."""
        if source_minion and "REDUCE_ATTACK_INSTEAD_OF_HEALTH" not in source_minion.tags:
            source_minion.tags.append("REDUCE_ATTACK_INSTEAD_OF_HEALTH")

    @handler("APPLY_PERMANENT_ATTACK_HALF_STATUS")
    def _apply_permanent_attack_half_status(state, eff, source_owner, source_minion, ctx):
        """Mao Tsé-Tung: corta o ataque dos alvos pela metade, uma única vez."""
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        rounding = (eff.get("rounding") or "CEIL").upper()
        for t in targets:
            if not isinstance(t, Minion):
                continue
            if "_PERMANENT_ATTACK_HALVED" in t.tags:
                continue
            old = t.attack
            if rounding == "FLOOR":
                new_atk = old // 2
            else:
                new_atk = math.ceil(old / 2)
            t.attack = max(0, new_atk)
            t.tags.append("_PERMANENT_ATTACK_HALVED")
            state.log_event({"type": "permanent_attack_half",
                             "minion": t.instance_id,
                             "old_attack": old,
                             "new_attack": t.attack})


def has_reduce_attack_instead_of_health(minion: Minion) -> bool:
    """Usado por effects.damage_character sem criar dependência circular."""
    return _has_effect_action(minion, "REDUCE_ATTACK_INSTEAD_OF_HEALTH") or minion.has_tag("REDUCE_ATTACK_INSTEAD_OF_HEALTH")
