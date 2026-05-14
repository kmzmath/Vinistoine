"""Lote 24 - segunda auditoria de execução de cartas.

Correções de alta confiança:
- Queima de Estoque não deve recrutar duas vezes.
- RECRUIT_MINION passa a respeitar amount_source=DISCARDED_COUNT.
- Fúria do Viní Geladinho causa dano apenas aos lacaios congelados.
"""
from __future__ import annotations

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, MAX_BOARD_SIZE, gen_id
from .cards import get_card
from . import targeting


def _deck_entry_card_id(state, entry: str) -> str:
    mods = getattr(state, "deck_card_modifiers", {}) or {}
    if entry in mods:
        return mods[entry].get("card_id", entry)
    return entry


def _deck_entry_card(state, entry: str) -> dict | None:
    return get_card(_deck_entry_card_id(state, entry))


def resolve_discard_up_to_cards_no_recruit(state, source_owner: int,
                                           card_instance_ids: list[str],
                                           amount: int,
                                           filter_desc: dict | None) -> int:
    """Descarta até amount cartas válidas e retorna a quantidade descartada.

    A carta Queima de Estoque tem dois efeitos no JSON:
    1. DISCARD_UP_TO_CARDS
    2. RECRUIT_MINION com amount_source=DISCARDED_COUNT

    Portanto o descarte não deve recrutar por conta própria; senão a carta
    recruta a mais.
    """
    from .effects_lote16 import _eligible_hand_cards

    p = state.players[source_owner]
    allowed = {c.instance_id: c for c in _eligible_hand_cards(p, filter_desc)}
    selected: list[CardInHand] = []
    seen = set()
    for cid in card_instance_ids or []:
        if cid in seen:
            continue
        seen.add(cid)
        ch = allowed.get(cid)
        if ch is not None and ch in p.hand:
            selected.append(ch)
        if len(selected) >= amount:
            break

    discarded = 0
    for ch in selected:
        p.hand.remove(ch)
        discarded += 1
        state.log_event({
            "type": "discard",
            "player": source_owner,
            "instance_id": ch.instance_id,
            "card_id": ch.card_id,
        })

    state.last_discarded_count = discarded
    state.log_event({"type": "discard_up_to_cards_resolved",
                     "player": source_owner, "discarded": discarded})
    return discarded


def recruit_minions_from_deck(state, source_owner: int, amount: int,
                              target_desc: dict | None = None) -> list[Minion]:
    """Recruta lacaios do deck para o campo, preservando markers básicos."""
    p = state.players[source_owner]
    recruited: list[Minion] = []
    if amount <= 0:
        return recruited

    preferred_tribe = (target_desc or {}).get("preferred_tribe")
    max_cost = (target_desc or {}).get("max_cost")
    from .effects import effective_card_has_tribe, summon_minion_from_card

    for _ in range(amount):
        if len(p.board) >= MAX_BOARD_SIZE:
            state.log_event({"type": "board_full_recruit"})
            break

        pool: list[int] = []
        preferred_pool: list[int] = []
        for i, entry in enumerate(p.deck):
            card = _deck_entry_card(state, entry)
            if not card or card.get("type") != "MINION":
                continue
            if max_cost is not None and int(card.get("cost", 0) or 0) > int(max_cost):
                continue
            pool.append(i)
            if preferred_tribe and effective_card_has_tribe(state, source_owner, card, preferred_tribe):
                preferred_pool.append(i)

        chosen_pool = preferred_pool or pool
        if not chosen_pool:
            state.log_event({"type": "no_minion_to_recruit"})
            break

        idx = state.rng.choice(chosen_pool)
        entry = p.deck.pop(idx)
        card_id = _deck_entry_card_id(state, entry)

        # Se veio de marker modificado, usa stat/tags ao invocar.
        mods = getattr(state, "deck_card_modifiers", {}) or {}
        mod = mods.pop(entry, None) if entry in mods else None
        stat_override = None
        if mod:
            card = get_card(card_id) or {}
            atk = int(card.get("attack", 0) or 0) + int((mod.get("stat_modifier") or {}).get("attack", 0) or 0)
            hp = int(card.get("health", 1) or 1) + int((mod.get("stat_modifier") or {}).get("health", 0) or 0)
            stat_override = (max(0, atk), max(1, hp))

        m = summon_minion_from_card(state, source_owner, card_id, stat_override=stat_override)
        if m and mod:
            for tag in mod.get("extra_tags") or []:
                if tag not in m.tags:
                    m.tags.append(tag)
                    if tag == "DIVINE_SHIELD":
                        m.divine_shield = True
        if m:
            recruited.append(m)
            state.log_event({"type": "recruit", "player": source_owner,
                             "card_id": card_id, "minion": m.instance_id})
    return recruited


def register_lote24_second_audit_handlers(handler):
    @handler("DISCARD_UP_TO_CARDS")
    def _discard_up_to_cards(state, eff, source_owner, source_minion, ctx):
        from .effects_lote16 import _eligible_hand_cards

        p = state.players[source_owner]
        amount = int(eff.get("amount", 1) or 1)
        filter_desc = eff.get("filter") or {}
        candidates = _eligible_hand_cards(p, filter_desc)
        if not candidates:
            ctx["discarded_count"] = 0
            state.last_discarded_count = 0
            state.log_event({"type": "discard_up_to_cards_no_candidates",
                             "player": source_owner})
            return

        if getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "discard_up_to_cards",
                "owner": source_owner,
                "amount": amount,
                "filter": filter_desc,
                "cards": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in candidates],
            }
            state.log_event({"type": "choice_required", "kind": "discard_up_to_cards",
                             "player": source_owner})
            return

        ids = ctx.get("card_ids") or ctx.get("discard_ids")
        if not isinstance(ids, list):
            ids = [c.instance_id for c in candidates[:amount]]
        from .effects_lote16 import resolve_discard_up_to_cards
        discarded = resolve_discard_up_to_cards(
            state, source_owner, ids, amount, filter_desc
        )
        ctx["discarded_count"] = discarded
        ctx["discard_already_recruited"] = True

    @handler("RECRUIT_MINION")
    def _recruit_minion(state, eff, source_owner, source_minion, ctx):
        amount = eff.get("amount", 1)
        if eff.get("amount_source") == "DISCARDED_COUNT":
            if ctx.get("discard_already_recruited"):
                return
            amount = ctx.get("discarded_count", getattr(state, "last_discarded_count", 0))
        amount = int(amount or 0)
        max_amount = eff.get("max_amount")
        if max_amount is not None:
            amount = min(amount, int(max_amount))
        recruit_minions_from_deck(state, source_owner, amount, eff.get("target") or {})

    @handler("DAMAGE")
    def _damage(state, eff, source_owner, source_minion, ctx):
        from .effects import damage_character, heal_character, check_condition

        cond = eff.get("condition") or {}
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(
            state, target_desc, source_owner, source_minion, ctx.get("chosen_target")
        )

        if isinstance(cond, dict) and cond.get("type"):
            ctype = cond.get("type")
            # Fúria do Viní Geladinho: target é ALL_MINIONS, condição deve
            # filtrar por lacaios congelados, não bloquear o efeito inteiro.
            if ctype == "TARGET_IS_FROZEN" and target_desc.get("mode") != "CHOSEN":
                targets = [t for t in targets if isinstance(t, Minion) and t.frozen]
            else:
                if not check_condition(state, cond, source_owner, source_minion, ctx):
                    return

        amount = int(eff.get("amount", 0) or 0)
        is_spell = ctx.get("is_spell", False)
        total = 0
        for t in targets:
            dealt = damage_character(state, t, amount, source_owner, source_minion,
                                     is_spell=is_spell)
            total += int(dealt or 0)
        if eff.get("lifesteal") and total > 0:
            heal_character(state, state.players[source_owner], total)
