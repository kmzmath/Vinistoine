"""Lote 25 - correções solicitadas após teste em partida real.

Inclui:
- Congelar perde só o próximo ataque.
- Obansug rouba vida para si mesmo, não para o herói.
- Mario abre escolha ao comprar.
- Dormant real por turnos.
- CHOOSE_ONE disparado por trigger abre escolha.
"""
from __future__ import annotations

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, gen_id
from . import targeting


def _wake_dormant_minion(state, m: Minion):
    if "DORMANT" in m.tags:
        m.tags.remove("DORMANT")
    m.cant_attack = False
    m.immune = False
    # Ao acordar, é como se tivesse acabado de entrar em campo.
    m.summoning_sick = True
    state.log_event({"type": "awaken", "minion": m.instance_id})


def _resolve_mario_choice(state, owner: int, drawn_instance_id: str,
                          revealed_card_id: str, choose_revealed: bool):
    p = state.players[owner]
    drawn = next((c for c in p.hand if c.instance_id == drawn_instance_id), None)
    if drawn is None:
        return
    if choose_revealed:
        # Remove a carta recém-comprada (Mario) da mão.
        if drawn in p.hand:
            p.hand.remove(drawn)
        # Compra a carta revelada do topo, se ainda for a mesma.
        if p.deck and p.deck[0] == revealed_card_id:
            p.deck.pop(0)
            if len(p.hand) < MAX_HAND_SIZE:
                p.hand.append(CardInHand(
                    instance_id=gen_id("h_"), card_id=revealed_card_id, revealed=True,
                ))
                state.log_event({"type": "mario_choose_draw",
                                 "player": owner, "chosen": revealed_card_id})
            else:
                state.log_event({"type": "burn", "player": owner, "card_id": revealed_card_id})
    else:
        state.log_event({"type": "mario_keep_drawn", "player": owner,
                         "card_id": drawn.card_id})


def register_lote25_requested_fixes_handlers(handler):
    @handler("FREEZE")
    def _freeze_one_attack_only(state, eff, source_owner, source_minion, ctx):
        targets = []
        target_desc = eff.get("target") or {}
        if target_desc.get("mode") == "ADJACENT_TO_CHOSEN_MINION" and ctx.get("adjacent_to_chosen_ids"):
            for mid in ctx.get("adjacent_to_chosen_ids") or []:
                found = state.find_minion(mid)
                if found:
                    targets.append(found[0])
        else:
            targets = targeting.resolve_targets(state, target_desc, source_owner,
                                                source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if t.has_tag("DORMANT"):
                    continue
                t.frozen = True
                # Congelamento deve consumir só a próxima oportunidade real de ataque.
                t.freeze_pending = False
                state.log_event({"type": "freeze", "minion": t.instance_id})
            elif isinstance(t, PlayerState):
                t.hero_frozen = True
                t.hero_freeze_pending = False
                state.log_event({"type": "freeze_hero", "player": t.player_id})

    @handler("STEAL_HEALTH")
    def _steal_health_to_self(state, eff, source_owner, source_minion, ctx):
        """Obansug: rouba vida para o próprio lacaio."""
        from .effects import damage_character
        amount = int(eff.get("amount", 1) or 1)
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        for t in targets:
            if not isinstance(t, Minion):
                continue
            actual = damage_character(state, t, amount, source_owner, source_minion,
                                      is_spell=ctx.get("is_spell", False))
            if actual > 0 and source_minion:
                source_minion.max_health += actual
                source_minion.health += actual
                state.log_event({"type": "steal_health_to_self",
                                 "from": t.instance_id,
                                 "to": source_minion.instance_id,
                                 "amount": actual})
            elif actual > 0:
                me = state.players[source_owner]
                me.hero_health = min(me.hero_max_health, me.hero_health + actual)
                state.log_event({"type": "heal", "player": source_owner, "amount": actual})

    @handler("BECOME_DORMANT")
    def _become_dormant_real(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 2) or 2)
        targets = targeting.resolve_targets(state, eff.get("target") or {"mode": "SELF"},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        for t in targets:
            if not isinstance(t, Minion):
                continue
            if "DORMANT" not in t.tags:
                t.tags.append("DORMANT")
            t.cant_attack = True
            t.immune = True
            t.frozen = False
            t.freeze_pending = False
            # Remove modifiers antigos do mesmo alvo para evitar despertar duplo.
            state.pending_modifiers = [
                pm for pm in state.pending_modifiers
                if not (pm.get("kind") == "dormant_turns" and pm.get("minion_id") == t.instance_id)
            ]
            state.pending_modifiers.append({
                "kind": "dormant_turns",
                "minion_id": t.instance_id,
                "owner": t.owner,
                "turns_remaining": amount,
            })
            state.log_event({"type": "dormant", "minion": t.instance_id,
                             "turns": amount})

    @handler("AWAKEN")
    def _awaken_real(state, eff, source_owner, source_minion, ctx):
        targets = targeting.resolve_targets(state, eff.get("target") or {"mode": "SELF"},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                _wake_dormant_minion(state, t)

    @handler("REVEAL_TOP_CARD_AND_CHOOSE_DRAW")
    def _mario_reveal_choose_draw(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        drawn = ctx.get("just_drawn_card")
        if not p.deck or drawn is None:
            return
        revealed = p.deck[0]
        if getattr(state, "manual_choices", False) and ctx.get("choose_revealed") is None:
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "mario_reveal_top_choose_draw",
                "owner": source_owner,
                "drawn_card": {"instance_id": drawn.instance_id, "card_id": drawn.card_id},
                "revealed_card_id": revealed,
            }
            state.log_event({"type": "choice_required",
                             "kind": "mario_reveal_top_choose_draw",
                             "player": source_owner,
                             "revealed": revealed})
            return

        choose_revealed = ctx.get("choose_revealed")
        if choose_revealed is None:
            # Fallback determinístico fora de partidas manuais.
            from .cards import get_card
            revealed_cost = (get_card(revealed) or {}).get("cost", 0)
            drawn_cost = (get_card(drawn.card_id) or {}).get("cost", 0)
            choose_revealed = revealed_cost > drawn_cost
        _resolve_mario_choice(state, source_owner, drawn.instance_id, revealed, bool(choose_revealed))

    @handler("CHOOSE_ONE")
    def _choose_one_manual_triggers(state, eff, source_owner, source_minion, ctx):
        choices = eff.get("choices") or []
        if not choices:
            return

        idx = ctx.get("chose_index")
        if idx is None and getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "choose_one_effect",
                "owner": source_owner,
                "source_minion_id": source_minion.instance_id if source_minion else None,
                "choices": choices,
                "ctx": {
                    k: v for k, v in (ctx or {}).items()
                    if isinstance(v, (str, int, float, bool)) or v is None
                },
            }
            state.log_event({"type": "choice_required",
                             "kind": "choose_one_effect",
                             "player": source_owner})
            return

        if idx is None:
            idx = 0
        idx = max(0, min(int(idx), len(choices) - 1))
        chosen = choices[idx]
        from .effects import resolve_effect
        resolve_effect(state, chosen, source_owner, source_minion, ctx)
        state.log_event({"type": "choose_one",
                         "option_index": idx,
                         "action": chosen.get("action")})


# Helpers importados pela engine.resolve_choice/start_turn.
resolve_mario_choice = _resolve_mario_choice
wake_dormant_minion = _wake_dormant_minion
