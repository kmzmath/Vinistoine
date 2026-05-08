"""Lote 27 — ajustes de mulligan/moeda, targeting, burn e cartas reportadas."""
from __future__ import annotations

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, gen_id
from .cards import get_card
from . import targeting


def _normalize_card_id(card_id: str | None) -> str | None:
    if card_id == "moeda":
        return "coin"
    return card_id


def register_lote27_requested_fixes_handlers(handler):
    @handler("ADD_CARD_TO_HAND")
    def _add_card_to_hand_with_coin_alias(state, eff, source_owner, source_minion, ctx):
        card_id = _normalize_card_id(eff.get("card_id") or (eff.get("card") or {}).get("id"))
        n = int(eff.get("amount", 1) or 1)
        if not card_id:
            return
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        if not targets:
            targets = [state.player_at(source_owner)]
        for t in targets:
            if not isinstance(t, PlayerState):
                continue
            for _ in range(n):
                if len(t.hand) >= MAX_HAND_SIZE:
                    state.log_event({"type": "burn", "player": t.player_id, "card_id": card_id})
                    continue
                ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
                t.hand.append(ch)
                state.log_event({"type": "add_card_to_hand",
                                 "player": t.player_id,
                                 "instance_id": ch.instance_id,
                                 "card_id": card_id})

    @handler("REMOVE_TAG")
    def _remove_tag_and_state(state, eff, source_owner, source_minion, ctx):
        tag = eff.get("tag")
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if not isinstance(t, Minion):
                continue
            if tag in t.tags:
                t.tags.remove(tag)
            if tag == "DIVINE_SHIELD":
                t.divine_shield = False
            if tag == "STEALTH" and "IMMUNE_WHILE_STEALTH" in t.tags:
                t.tags.remove("IMMUNE_WHILE_STEALTH")
                t.immune = False
            state.log_event({"type": "remove_tag", "minion": t.instance_id, "tag": tag})

    @handler("SET_MAX_HEALTH")
    def _set_max_health_keep_current(state, eff, source_owner, source_minion, ctx):
        hp = int(eff.get("amount") or eff.get("health") or 1)
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                old_max = t.max_health
                t.max_health = hp
                t.health = min(t.health, hp)
                state.log_event({"type": "set_max_health", "target_kind": "minion",
                                 "target_id": t.instance_id, "old": old_max, "amount": hp})
            elif isinstance(t, PlayerState):
                old_max = t.hero_max_health
                t.hero_max_health = hp
                t.hero_health = min(t.hero_health, hp)
                state.log_event({"type": "set_max_health", "target_kind": "hero",
                                 "target_id": t.player_id, "old": old_max, "amount": hp})
