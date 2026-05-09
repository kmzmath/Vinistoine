"""Lote 14 - ataques forçados e dano especial.

Ações cobertas:
- FORCE_MINION_ATTACK
- DAMAGE_SEQUENCE
- REPEAT_DAMAGE_IF_KILLS
- EXCESS_DAMAGE_TO_CHOSEN_SIDE
- DAMAGE_ADJACENT_EQUAL_TO_TARGET_HEALTH
- DAMAGE_ADJACENT_MINIONS_INSTEAD
"""
from __future__ import annotations

from .state import Minion, PlayerState
from . import targeting


def _chosen_id(ctx: dict, index: int = 0):
    queue = ctx.get("target_queue")
    if isinstance(queue, list) and len(queue) > index:
        return queue[index]
    if index == 0:
        return ctx.get("chosen_target")
    return None


def _as_minion(state, target_id):
    if not target_id:
        return None
    found = state.find_minion(target_id)
    return found[0] if found else None


def _resolve_one(state, desc: dict, source_owner: int, source_minion, target_id, *, is_spell=False):
    fixed = dict(desc or {})
    mode = fixed.get("mode")
    if mode in ("CHOSEN_EACH", "CHOSEN_ADJACENT_DIRECTION"):
        fixed["mode"] = "CHOSEN"
    targets = targeting.resolve_targets(state, fixed, source_owner, source_minion,
                                        target_id, is_spell=is_spell)
    return targets[0] if targets else None


def _damage_minion_combat(state, attacker: Minion, target: Minion):
    """Aplica dano simultâneo de combate sem validar turno/taunt.

    Usado por efeitos que forçam ataque. Preserva mitigação/escudo/veneno/etc.
    """
    from .effects import damage_character
    if attacker.has_tag("STEALTH") and not attacker.has_tag("PERMANENT_STEALTH"):
        attacker.tags.remove("STEALTH")
    dmg_to_target = 0 if target.has_tag("ATTACK_DAMAGE_IMMUNE") else attacker.attack
    dmg_to_attacker = 0 if attacker.has_tag("ATTACK_DAMAGE_IMMUNE") else target.attack
    damage_character(state, target, dmg_to_target, source_owner=attacker.owner,
                     source_minion=attacker)
    damage_character(state, attacker, dmg_to_attacker, source_owner=target.owner,
                     source_minion=target)
    attacker.attacks_this_turn += 1
    state.log_event({"type": "forced_attack",
                     "attacker": attacker.instance_id,
                     "target": target.instance_id})


def _adjacent_minions(state, pivot: Minion) -> list[Minion]:
    board = state.players[pivot.owner].board
    if pivot not in board:
        return []
    idx = board.index(pivot)
    out = []
    if idx > 0:
        out.append(board[idx - 1])
    if idx < len(board) - 1:
        out.append(board[idx + 1])
    return out


def register_lote14_handlers(handler):
    @handler("FORCE_MINION_ATTACK")
    def _force_minion_attack(state, eff, source_owner, source_minion, ctx):
        """Fusca Azul: escolha um inimigo; ele ataca outro lacaio escolhido.

        O JSON usa `source` para o atacante forçado e `target` para o alvo.
        Com target_queue, a ordem é [atacante_forçado, alvo].
        """
        source_id = _chosen_id(ctx, 0)
        target_id = _chosen_id(ctx, 1)
        source_desc = eff.get("source") or {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}
        target_desc = eff.get("target") or {"mode": "CHOSEN", "valid": ["MINION"]}

        attackers = targeting.resolve_targets(state, source_desc, source_owner,
                                              source_minion, source_id)
        attacker = next((t for t in attackers if isinstance(t, Minion)), None)
        target = _resolve_one(state, target_desc, source_owner, source_minion, target_id)

        if not isinstance(attacker, Minion) or not isinstance(target, Minion):
            state.log_event({"type": "force_attack_failed", "reason": "invalid_target"})
            return
        if attacker is target or attacker.immune or target.immune:
            state.log_event({"type": "force_attack_failed", "reason": "invalid_pair"})
            return
        _damage_minion_combat(state, attacker, target)

    @handler("DAMAGE_SEQUENCE")
    def _damage_sequence(state, eff, source_owner, source_minion, ctx):
        """Vagner Pikachu: causa 1, 2 e 3 de dano, um de cada vez.

        Se o cliente enviar target_queue, cada item mira uma etapa. Se enviar
        só um alvo, o mesmo alvo recebe as etapas enquanto continuar válido.
        Sem alvo explícito, escolhe deterministicamente o herói inimigo.
        """
        from .effects import damage_character
        amounts = list(eff.get("amounts") or [])
        target_desc = dict(eff.get("target") or {})
        target_desc["mode"] = "CHOSEN"
        target_queue = ctx.get("target_queue") if isinstance(ctx.get("target_queue"), list) else []
        fallback_id = ctx.get("chosen_target")
        if not fallback_id:
            fallback_id = f"hero:{1 - source_owner}"

        for i, amount in enumerate(amounts):
            target_id = target_queue[i] if i < len(target_queue) else fallback_id
            target = _resolve_one(state, target_desc, source_owner, source_minion,
                                  target_id, is_spell=ctx.get("is_spell", False))
            if target is None:
                state.log_event({"type": "damage_sequence_step_failed", "index": i})
                continue
            damage_character(state, target, int(amount or 0), source_owner,
                             source_minion, is_spell=ctx.get("is_spell", False))
            state.log_event({"type": "damage_sequence_step", "index": i,
                             "amount": int(amount or 0)})

    @handler("REPEAT_DAMAGE_IF_KILLS")
    def _repeat_damage_if_kills(state, eff, source_owner, source_minion, ctx):
        """Mafia Italiana: dano em área; se algum lacaio morrer, repete."""
        from .effects import damage_character
        from . import engine

        amount = int(eff.get("amount", 0) or 0)
        target_desc = eff.get("target") or {"mode": "ALL_OTHER_MINIONS"}
        repeats = 0
        while repeats < 20:
            repeats += 1
            targets = [t for t in targeting.resolve_targets(
                state, target_desc, source_owner, source_minion,
                ctx.get("chosen_target"), is_spell=ctx.get("is_spell", False)
            ) if isinstance(t, Minion)]
            if not targets:
                break
            before_graveyard = len(state.graveyard)
            before_dead_ids = {m.instance_id for m in state.all_minions() if m.health <= 0}
            for t in list(targets):
                damage_character(state, t, amount, source_owner, source_minion,
                                 is_spell=ctx.get("is_spell", False))
            engine.cleanup(state)
            after_graveyard = len(state.graveyard)
            died = after_graveyard > before_graveyard or any(
                m.instance_id not in before_dead_ids and m.health <= 0
                for m in targets
            )
            state.log_event({"type": "repeat_damage_pass",
                             "amount": amount, "pass": repeats, "died": bool(died)})
            if not died:
                break

    @handler("EXCESS_DAMAGE_TO_CHOSEN_SIDE")
    def _excess_damage_to_chosen_side(state, eff, source_owner, source_minion, ctx):
        """Awp: dano alto em um lacaio; excesso segue para um lado escolhido.

        `direction` pode ser LEFT ou RIGHT no ctx. Sem escolha explícita,
        usa RIGHT como fallback determinístico.
        """
        from .effects import damage_character

        amount = int(eff.get("source_damage", eff.get("amount", 0)) or 0)
        target_desc = dict(eff.get("target") or {})
        target_desc["mode"] = "CHOSEN"
        target_id = _chosen_id(ctx, 0)
        target = _resolve_one(state, target_desc, source_owner, source_minion,
                              target_id, is_spell=ctx.get("is_spell", False))
        if not isinstance(target, Minion):
            state.log_event({"type": "excess_damage_failed", "reason": "no_target"})
            return

        pre_health = max(0, target.health)
        dealt = damage_character(state, target, amount, source_owner,
                                 source_minion, is_spell=ctx.get("is_spell", False))
        excess = max(0, amount - pre_health) if dealt > 0 else 0
        direction = str(ctx.get("direction") or ctx.get("adjacent_direction") or "RIGHT").upper()
        if direction not in ("LEFT", "RIGHT"):
            direction = "RIGHT"
        state.log_event({"type": "excess_damage_start",
                         "target": target.instance_id,
                         "amount": amount,
                         "excess": excess,
                         "direction": direction})
        if excess <= 0:
            return

        board = state.players[target.owner].board
        if target not in board:
            # Mesmo se morreu, o objeto ainda nos dá o owner, mas não o índice.
            # Não há forma segura de inferir a posição após cleanup/deathrattle;
            # então achamos pelo alvo original usando o log visual somente.
            return
        idx = board.index(target)
        step = -1 if direction == "LEFT" else 1
        i = idx + step
        while excess > 0 and 0 <= i < len(board):
            nxt = board[i]
            pre = max(0, nxt.health)
            dealt = damage_character(state, nxt, excess, source_owner,
                                     source_minion, is_spell=ctx.get("is_spell", False))
            excess = max(0, excess - pre) if dealt > 0 else 0
            i += step

    @handler("DAMAGE_ADJACENT_EQUAL_TO_TARGET_HEALTH")
    def _damage_adjacent_equal_to_target_health(state, eff, source_owner, source_minion, ctx):
        """Spray do Viní: mira um inimigo e causa a vida dele aos adjacentes."""
        from .effects import damage_character

        target_id = _chosen_id(ctx, 0)
        target = _resolve_one(state, eff.get("target") or {}, source_owner,
                              source_minion, target_id,
                              is_spell=ctx.get("is_spell", False))
        if not isinstance(target, Minion):
            return
        amount = max(0, target.health)
        for adj in list(_adjacent_minions(state, target)):
            damage_character(state, adj, amount, source_owner, source_minion,
                             is_spell=ctx.get("is_spell", False))
        state.log_event({"type": "damage_adjacent_equal_to_health",
                         "target": target.instance_id, "amount": amount})

    @handler("DAMAGE_ADJACENT_MINIONS_INSTEAD")
    def _damage_adjacent_minions_instead(state, eff, source_owner, source_minion, ctx):
        """Viní de Aimbot: ao atacar lacaio, causa dano só aos adjacentes."""
        from .effects import damage_character
        if not source_minion:
            return
        target_id = ctx.get("attack_target_id") or ctx.get("chosen_target")
        pivot = _as_minion(state, target_id)
        if not pivot:
            return
        amount_source = eff.get("amount_source")
        amount = source_minion.attack if amount_source == "SELF_ATTACK" else int(eff.get("amount", source_minion.attack) or 0)
        for adj in list(_adjacent_minions(state, pivot)):
            damage_character(state, adj, amount, source_owner, source_minion)
        state.log_event({"type": "damage_adjacent_instead",
                         "source": source_minion.instance_id,
                         "pivot": pivot.instance_id,
                         "amount": amount})
