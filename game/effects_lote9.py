"""Lote 9 - custo, mana e efeitos de próximo turno.

Ações cobertas:
- BUFF_NEXT_PLAYED_TRIBE_HEALTH
- REDUCE_NEXT_TURN_FIRST_MINION_COST
- NEXT_TURN_FIRST_MINION_COST_REDUCTION
- REDUCE_MANA_NEXT_TURN
- NEXT_SPELL_COSTS_HEALTH_INSTEAD_OF_MANA
- DRAW_CARD_TYPE
- DRAW_CARD_DELAYED
"""
from __future__ import annotations

from .state import CardInHand, PlayerState, MAX_HAND_SIZE, gen_id
from .cards import get_card
from . import targeting


def _target_players(state, eff, source_owner, source_minion, ctx):
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    players = [t for t in targets if isinstance(t, PlayerState)]
    return players or [state.players[source_owner]]


def register_lote9_handlers(handler):
    @handler("BUFF_NEXT_PLAYED_TRIBE_HEALTH")
    def _buff_next_played_tribe_health(state, eff, source_owner, source_minion, ctx):
        """Banana: a próxima Fruta deste turno tem +1 de vida."""
        target = eff.get("target") or {}
        state.pending_modifiers.append({
            "kind": "next_played_stat_buff",
            "owner": source_owner,
            "attack": int(eff.get("attack", 0) or 0),
            "health": int(eff.get("health", eff.get("amount", 0)) or 0),
            "valid": target.get("valid") or [],
            "expires_on": "end_of_turn",
            "consumed": False,
        })
        state.log_event({
            "type": "pending_next_played_stat_buff",
            "player": source_owner,
            "health": int(eff.get("health", eff.get("amount", 0)) or 0),
            "valid": target.get("valid") or [],
        })

    @handler("REDUCE_NEXT_TURN_FIRST_MINION_COST")
    def _reduce_next_turn_first_minion_cost(state, eff, source_owner, source_minion, ctx):
        """La Selecione: no próximo turno, o primeiro lacaio custa menos."""
        for p in _target_players(state, eff, source_owner, source_minion, ctx):
            state.pending_modifiers.append({
                "kind": "next_turn_first_minion_cost_reduction",
                "owner": p.player_id,
                "amount": int(eff.get("amount", 1) or 1),
                "valid": ["MINION"],
                "consumed": False,
            })
            state.log_event({"type": "pending_next_turn_minion_discount",
                             "player": p.player_id, "amount": int(eff.get("amount", 1) or 1)})

    @handler("NEXT_TURN_FIRST_MINION_COST_REDUCTION")
    def _next_turn_first_minion_cost_reduction(state, eff, source_owner, source_minion, ctx):
        """Vinas: próximo primeiro lacaio custa X menos, ou Y se satisfizer condição."""
        for p in _target_players(state, eff, source_owner, source_minion, ctx):
            state.pending_modifiers.append({
                "kind": "next_turn_first_minion_cost_reduction",
                "owner": p.player_id,
                "amount": int(eff.get("amount", 1) or 1),
                "conditional_amount": eff.get("conditional_amount"),
                "condition": eff.get("condition") or {},
                "valid": ["MINION"],
                "consumed": False,
            })
            state.log_event({
                "type": "pending_next_turn_minion_discount",
                "player": p.player_id,
                "amount": int(eff.get("amount", 1) or 1),
                "conditional_amount": eff.get("conditional_amount"),
            })

    @handler("REDUCE_MANA_NEXT_TURN")
    def _reduce_mana_next_turn(state, eff, source_owner, source_minion, ctx):
        """Funkeiro: reduz a mana disponível no próximo turno do dono."""
        for p in _target_players(state, eff, source_owner, source_minion, ctx):
            state.pending_modifiers.append({
                "kind": "reduce_mana_this_turn",
                "owner": p.player_id,
                "amount": int(eff.get("amount", 1) or 1),
            })
            state.log_event({"type": "pending_reduce_mana_next_turn",
                             "player": p.player_id, "amount": int(eff.get("amount", 1) or 1)})

    @handler("NEXT_SPELL_COSTS_HEALTH_INSTEAD_OF_MANA")
    def _next_spell_costs_health_instead_of_mana(state, eff, source_owner, source_minion, ctx):
        """Tomo Amaldiçoado: o próximo feitiço deste turno custa vida."""
        for p in _target_players(state, eff, source_owner, source_minion, ctx):
            state.pending_modifiers.append({
                "kind": "next_spell_costs_health_instead_of_mana",
                "owner": p.player_id,
                "expires_on": "end_of_turn",
                "consumed": False,
            })
            state.log_event({"type": "pending_spell_costs_health", "player": p.player_id})

    @handler("DRAW_CARD_TYPE")
    def _draw_card_type(state, eff, source_owner, source_minion, ctx):
        """Compra cartas de um tipo específico do deck, preservando a ordem."""
        amount = int(eff.get("amount", 1) or 1)
        card_type = eff.get("card_type") or eff.get("type")
        players = _target_players(state, eff, source_owner, source_minion, ctx)
        for p in players:
            drawn_ids = []
            for _ in range(amount):
                idx = None
                for i, cid in enumerate(p.deck):
                    card = get_card(cid)
                    if card and (card_type is None or card.get("type") == card_type):
                        idx = i
                        break
                if idx is None:
                    break
                cid = p.deck.pop(idx)
                if len(p.hand) >= MAX_HAND_SIZE:
                    state.log_event({"type": "burn", "player": p.player_id, "card_id": cid})
                    continue
                ch = CardInHand(instance_id=gen_id("h_"), card_id=cid)
                p.hand.append(ch)
                drawn_ids.append(ch.instance_id)
                state.log_event({"type": "draw_card_type", "player": p.player_id,
                                 "card_id": cid, "instance_id": ch.instance_id,
                                 "card_type": card_type})
            state.last_drawn_card_instance_ids = drawn_ids

    @handler("DRAW_CARD_DELAYED")
    def _draw_card_delayed(state, eff, source_owner, source_minion, ctx):
        """AliExpress: remove carta(s) do deck agora e entrega no próximo turno."""
        from .effects import damage_character
        amount = int(eff.get("amount", 1) or 1)
        delay = int(eff.get("delay_turns", 1) or 1)
        cost_modifier = int(eff.get("cost_modifier", 0) or 0)
        players = _target_players(state, eff, source_owner, source_minion, ctx)
        for p in players:
            delayed = []
            for _ in range(amount):
                if not p.deck:
                    p.fatigue_counter += 1
                    damage_character(state, p, p.fatigue_counter, source_owner=p.player_id)
                    state.log_event({"type": "fatigue", "player": p.player_id, "amount": p.fatigue_counter})
                    continue
                delayed.append(p.deck.pop(0))
            if delayed:
                state.pending_modifiers.append({
                    "kind": "delayed_draw",
                    "owner": p.player_id,
                    "cards": delayed,
                    "own_turns_remaining": delay,
                    "cost_modifier": cost_modifier,
                })
                state.log_event({"type": "delayed_draw_created", "player": p.player_id,
                                 "cards": list(delayed), "delay_turns": delay,
                                 "cost_modifier": cost_modifier})
