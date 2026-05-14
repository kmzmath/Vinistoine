"""Lote 13 - recrutamento, ressurreição, transformação e cura especial.

Ações cobertas:
- RECRUIT_FIRST_MINION_WITH_COST
- RECRUIT_HIGHEST_COST_MINION_UP_TO
- DISCOVER_AND_RESURRECT_RECENT_FRIENDLY_MINION
- DRAW_MINION_AND_TRANSFORM
- DRAW_TRIBE_FROM_DECK_OR_RESURRECT_SELF
- DRAW_HIGHEST_COST_SPELL_AND_SET_COST
- HEAL_WITH_OVERHEAL_TO_HEALTH
- HEAL_OR_REVIVE_FRIENDLY
- HEAL_OPPONENT_AND_DRAW_SCALING
"""
from __future__ import annotations

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, gen_id
from .cards import get_card, card_has_tribe
from . import targeting


def _deck_entry_card_id(state, entry: str) -> str:
    mods = getattr(state, "deck_card_modifiers", {}) or {}
    if entry in mods:
        return mods[entry].get("card_id", entry)
    return entry


def _card_for_deck_entry(state, entry: str) -> dict | None:
    return get_card(_deck_entry_card_id(state, entry))


def _remove_deck_entry(player: PlayerState, entry: str):
    try:
        player.deck.remove(entry)
        return True
    except ValueError:
        return False


def _add_card_to_hand(state, player: PlayerState, card_id: str, *, cost_override=None,
                      cost_modifier: int = 0, stat_modifier: dict | None = None,
                      extra_tags: list[str] | None = None) -> CardInHand | None:
    if len(player.hand) >= MAX_HAND_SIZE:
        state.log_event({"type": "burn", "player": player.player_id, "card_id": card_id})
        return None
    ch = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
    if cost_override is not None:
        ch.cost_override = int(cost_override)
    ch.cost_modifier += int(cost_modifier or 0)
    if stat_modifier:
        ch.stat_modifier.update(stat_modifier)
    if extra_tags:
        ch.extra_tags.extend([t for t in extra_tags if t not in ch.extra_tags])
    player.hand.append(ch)
    state.log_event({"type": "draw", "player": player.player_id,
                     "card_id": card_id, "instance_id": ch.instance_id})
    state.last_drawn_card_instance_ids = [ch.instance_id]
    return ch


def _summon_deck_entry(state, owner: int, entry: str, position=None) -> Minion | None:
    """Invoca um entry do deck, preservando stat_modifier quando entry é marker."""
    from .effects import summon_minion_from_card
    mods = getattr(state, "deck_card_modifiers", {}) or {}
    mod = mods.pop(entry, None) if entry in mods else None
    card_id = mod.get("card_id") if mod else entry
    card = get_card(card_id) or {}
    stat_override = None
    if mod and card.get("type") == "MINION":
        atk = (card.get("attack") if card.get("attack") is not None else 0) + int((mod.get("stat_modifier") or {}).get("attack", 0))
        hp = (card.get("health") if card.get("health") is not None else 1) + int((mod.get("stat_modifier") or {}).get("health", 0))
        stat_override = (max(0, atk), max(0, hp))
    m = summon_minion_from_card(state, owner, card_id, position=position, stat_override=stat_override)
    if m and mod:
        for tag in mod.get("extra_tags") or []:
            if tag not in m.tags:
                m.tags.append(tag)
                if tag == "DIVINE_SHIELD":
                    m.divine_shield = True
    return m


def _resurrect_card(state, owner: int, card_id: str, *, health: int | None = None) -> Minion | None:
    card = get_card(card_id) or {}
    stat_override = None
    if health is not None and card.get("type") == "MINION":
        atk = card.get("attack") if card.get("attack") is not None else 0
        stat_override = (atk, int(health))
    from .effects import summon_minion_from_card
    m = summon_minion_from_card(state, owner, card_id, stat_override=stat_override)
    if m:
        state.log_event({"type": "resurrect", "owner": owner,
                         "card_id": card_id, "minion": m.instance_id})
    return m


def _recent_friendly_graveyard(state, owner: int, pool_size: int | None = None):
    out = []
    for idx in range(len(state.graveyard) - 1, -1, -1):
        rec = state.graveyard[idx]
        if rec.get("owner") != owner:
            continue
        card = get_card(rec.get("card_id"))
        if card and card.get("type") == "MINION":
            out.append((idx, rec))
            if pool_size is not None and len(out) >= pool_size:
                break
    return out


def register_lote13_handlers(handler):
    @handler("RECRUIT_FIRST_MINION_WITH_COST")
    def _recruit_first_minion_with_cost(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        wanted_cost = int(eff.get("cost", 0) or 0)
        chosen_entry = None
        for entry in list(p.deck):
            card = _card_for_deck_entry(state, entry)
            if not card or card.get("type") != "MINION":
                continue
            if int(card.get("cost", 0) or 0) == wanted_cost:
                chosen_entry = entry
                break
        if chosen_entry is None:
            state.log_event({"type": "recruit_failed", "player": source_owner,
                             "reason": "no_minion_with_cost", "cost": wanted_cost})
            return
        _remove_deck_entry(p, chosen_entry)
        m = _summon_deck_entry(state, source_owner, chosen_entry)
        if not m:
            return
        cond = eff.get("if_recruited_has_tribe") or {}
        tribe = cond.get("tribe")
        if tribe and m.has_tribe(tribe):
            if cond.get("action") == "ADD_TAG":
                tag = cond.get("tag")
                if tag and tag not in m.tags:
                    m.tags.append(tag)
                    if tag == "DIVINE_SHIELD":
                        m.divine_shield = True
                    state.log_event({"type": "add_tag", "minion": m.instance_id, "tag": tag})
        state.log_event({"type": "recruit", "player": source_owner,
                         "card_id": m.card_id, "minion": m.instance_id})

    @handler("RECRUIT_HIGHEST_COST_MINION_UP_TO")
    def _recruit_highest_cost_minion_up_to(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        max_cost = int(eff.get("max_cost", 10) or 10)
        best = None
        best_cost = -1
        for entry in list(p.deck):
            card = _card_for_deck_entry(state, entry)
            if not card or card.get("type") != "MINION":
                continue
            cost = int(card.get("cost", 0) or 0)
            if cost <= max_cost and cost > best_cost:
                best = entry
                best_cost = cost
        if best is None:
            state.log_event({"type": "recruit_failed", "player": source_owner,
                             "reason": "no_eligible_minion", "max_cost": max_cost})
            return
        _remove_deck_entry(p, best)
        m = _summon_deck_entry(state, source_owner, best)
        if m:
            state.log_event({"type": "recruit", "player": source_owner,
                             "card_id": m.card_id, "minion": m.instance_id,
                             "selection": "highest_cost", "max_cost": max_cost})

    @handler("DISCOVER_AND_RESURRECT_RECENT_FRIENDLY_MINION")
    def _discover_and_resurrect_recent_friendly_minion(state, eff, source_owner, source_minion, ctx):
        pool_size = int(eff.get("pool_size", 3) or 3)
        options = _recent_friendly_graveyard(state, source_owner, pool_size)
        if not options:
            state.log_event({"type": "resurrect_failed", "player": source_owner,
                             "reason": "empty_graveyard"})
            return

        if getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "resurrect_from_graveyard",
                "owner": source_owner,
                "options": [
                    {"graveyard_index": idx, "card_id": rec.get("card_id"), "name": rec.get("name")}
                    for idx, rec in options
                ],
            }
            state.log_event({"type": "choice_required",
                             "kind": "resurrect_from_graveyard",
                             "player": source_owner})
            return

        chosen_idx = ctx.get("graveyard_index")
        chosen = None
        if chosen_idx is not None:
            try:
                chosen_idx = int(chosen_idx)
                chosen = next((pair for pair in options if pair[0] == chosen_idx), None)
            except Exception:
                chosen = None
        if chosen is None:
            chosen = options[0]
        _resurrect_card(state, source_owner, chosen[1].get("card_id"))

    @handler("DRAW_MINION_AND_TRANSFORM")
    def _draw_minion_and_transform(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        amount = int(eff.get("amount", 1) or 1)
        transform_into = eff.get("transform_into")
        if not transform_into or get_card(transform_into) is None:
            state.log_event({"type": "transform_draw_failed", "reason": "invalid_transform_card"})
            return
        drawn = []
        for _ in range(amount):
            entry = next((e for e in list(p.deck)
                          if (_card_for_deck_entry(state, e) or {}).get("type") == "MINION"), None)
            if entry is None:
                break
            _remove_deck_entry(p, entry)
            # Consome marker se existia: a carta virou outra, os mods antigos não importam.
            mods = getattr(state, "deck_card_modifiers", {}) or {}
            mods.pop(entry, None)
            ch = _add_card_to_hand(state, p, transform_into)
            if ch:
                drawn.append(ch.instance_id)
                state.log_event({"type": "draw_minion_and_transform",
                                 "player": source_owner,
                                 "original_card_id": _deck_entry_card_id(state, entry),
                                 "new_card_id": transform_into,
                                 "instance_id": ch.instance_id})
        state.last_drawn_card_instance_ids = drawn

    @handler("DRAW_TRIBE_FROM_DECK_OR_RESURRECT_SELF")
    def _draw_tribe_from_deck_or_resurrect_self(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        tribe = eff.get("tribe")
        amount = int(eff.get("amount", 1) or 1)
        drawn = []
        from .effects import effective_card_has_tribe
        for _ in range(amount):
            entry = next((e for e in list(p.deck)
                          if effective_card_has_tribe(state, source_owner, _card_for_deck_entry(state, e) or {}, tribe)), None)
            if entry is None:
                break
            _remove_deck_entry(p, entry)
            card_id = _deck_entry_card_id(state, entry)
            # Compra via CardInHand simples; se entry for marker, transforma em carta base.
            ch = _add_card_to_hand(state, p, card_id)
            if ch:
                drawn.append(ch.instance_id)
        if drawn:
            state.last_drawn_card_instance_ids = drawn
            return

        # Fallback: ressuscita o próprio lacaio morto com vida indicada.
        source_card_id = ctx.get("source_card_id") or (source_minion.card_id if source_minion else None)
        if not source_card_id:
            return
        fallback = eff.get("fallback") or {}
        health = fallback.get("health")
        m = _resurrect_card(state, source_owner, source_card_id, health=health)
        if m:
            for mod in fallback.get("modifications") or []:
                if mod.get("action") == "REMOVE_TAG":
                    tag = mod.get("tag")
                    m.tags = [t for t in m.tags if t != tag]
                    if tag == "DIVINE_SHIELD":
                        m.divine_shield = False
                elif mod.get("action") == "REMOVE_TRIGGER":
                    trig = mod.get("trigger")
                    m.effects = [e for e in (m.effects or []) if e.get("trigger") != trig]
            state.log_event({"type": "resurrect_self_modified",
                             "card_id": source_card_id,
                             "minion": m.instance_id})

    @handler("DRAW_HIGHEST_COST_SPELL_AND_SET_COST")
    def _draw_highest_cost_spell_and_set_cost(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        set_cost = int(eff.get("set_cost", 1) or 1)
        best = None
        best_cost = -1
        for entry in list(p.deck):
            card = _card_for_deck_entry(state, entry)
            if not card or card.get("type") != "SPELL":
                continue
            cost = int(card.get("cost", 0) or 0)
            if cost > best_cost:
                best = entry
                best_cost = cost
        if best is None:
            state.log_event({"type": "draw_failed", "player": source_owner,
                             "reason": "no_spell"})
            return
        _remove_deck_entry(p, best)
        card_id = _deck_entry_card_id(state, best)
        # Se era marker, consumimos o marker e forçamos custo final.
        mods = getattr(state, "deck_card_modifiers", {}) or {}
        mods.pop(best, None)
        ch = _add_card_to_hand(state, p, card_id, cost_override=set_cost)
        if ch:
            state.log_event({"type": "draw_highest_cost_spell_and_set_cost",
                             "player": source_owner,
                             "card_id": card_id,
                             "instance_id": ch.instance_id,
                             "set_cost": set_cost})

    @handler("HEAL_WITH_OVERHEAL_TO_HEALTH")
    def _heal_with_overheal_to_health(state, eff, source_owner, source_minion, ctx):
        from .effects import heal_character
        amount = int(eff.get("amount", 0) or 0)
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if not isinstance(t, Minion):
                continue
            missing = max(0, t.max_health - t.health)
            healed = heal_character(state, t, amount)
            excess = max(0, amount - missing)
            if excess > 0:
                t.max_health += excess
                t.health += excess
                state.log_event({"type": "overheal_to_health",
                                 "minion": t.instance_id,
                                 "amount": excess})

    @handler("HEAL_OR_REVIVE_FRIENDLY")
    def _heal_or_revive_friendly(state, eff, source_owner, source_minion, ctx):
        from .effects import heal_character
        amount = int(eff.get("amount", 0) or 0)
        chosen_id = ctx.get("chosen_target") or ctx.get("heal_target_id")
        dead_prefix = "dead:"
        if isinstance(chosen_id, str) and chosen_id.startswith(dead_prefix):
            try:
                idx = int(chosen_id[len(dead_prefix):])
            except Exception:
                idx = -1
            if eff.get("can_revive_dead_this_turn") and 0 <= idx < len(state.graveyard):
                rec = state.graveyard[idx]
                if rec.get("owner") == source_owner:
                    _resurrect_card(state, source_owner, rec.get("card_id"), health=amount)
                    return

        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, chosen_id)
        if targets:
            # Quando o jogador escolheu manualmente, respeite exatamente esse
            # alvo; caso contrário mantenha o fallback histórico do mais ferido.
            if chosen_id:
                chosen = targets[0]
            else:
                damaged = [t for t in targets
                           if (isinstance(t, Minion) and t.health < t.max_health)
                           or (isinstance(t, PlayerState) and t.hero_health < t.hero_max_health)]
                chosen = damaged[0] if damaged else targets[0]
            heal_character(state, chosen, amount)
            return

        if getattr(state, "manual_choices", False):
            me = state.players[source_owner]
            options = [{"id": f"hero:{source_owner}", "kind": "hero",
                        "name": me.name, "health": me.hero_health,
                        "max_health": me.hero_max_health}]
            options.extend(m.to_dict() for m in me.board)
            if eff.get("can_revive_dead_this_turn"):
                for idx, rec in _recent_friendly_graveyard(state, source_owner, 3):
                    card = get_card(rec.get("card_id")) or {}
                    options.append({"id": f"dead:{idx}", "kind": "dead_minion",
                                    "card_id": rec.get("card_id"),
                                    "name": rec.get("name") or card.get("name") or rec.get("card_id")})
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "heal_or_revive_friendly",
                "owner": source_owner,
                "source_minion_id": source_minion.instance_id if source_minion else None,
                "amount": amount,
                "options": options,
            }
            state.log_event({"type": "choice_required",
                             "kind": "heal_or_revive_friendly",
                             "player": source_owner})
            return

        # Sem UI de alvo em gatilho de fim de turno: cura o aliado mais ferido.
        me = state.players[source_owner]
        candidates = [me] + list(me.board)
        damaged = []
        for t in candidates:
            if isinstance(t, PlayerState):
                missing = t.hero_max_health - t.hero_health
            else:
                missing = t.max_health - t.health
            if missing > 0:
                damaged.append((missing, t))
        if damaged:
            damaged.sort(key=lambda x: x[0], reverse=True)
            heal_character(state, damaged[0][1], amount)
            return

        if eff.get("can_revive_dead_this_turn"):
            options = _recent_friendly_graveyard(state, source_owner, 1)
            if options:
                _resurrect_card(state, source_owner, options[0][1].get("card_id"), health=amount)

    @handler("HEAL_OPPONENT_AND_DRAW_SCALING")
    def _heal_opponent_and_draw_scaling(state, eff, source_owner, source_minion, ctx):
        from .effects import heal_character, draw_card
        options = list(eff.get("options") or [])
        if not options:
            return
        idx = ctx.get("chose_index")
        if getattr(state, "manual_choices", False) and idx is None:
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "heal_opponent_and_draw_scaling",
                "owner": source_owner,
                "source_minion_id": source_minion.instance_id if source_minion else None,
                "options": options,
            }
            state.log_event({"type": "choice_required",
                             "kind": "heal_opponent_and_draw_scaling",
                             "player": source_owner})
            return
        try:
            idx = int(idx)
        except Exception:
            idx = len(options) - 1  # Fallback de testes/IA: maior risco/recompensa.
        idx = max(0, min(idx, len(options) - 1))
        opt = options[idx]
        opp = state.opponent_of(source_owner)
        heal_character(state, opp, int(opt.get("heal_amount", 0) or 0))
        draw_card(state, state.players[source_owner], int(opt.get("draw_amount", 0) or 0))
        state.log_event({"type": "heal_opponent_and_draw_scaling",
                         "player": source_owner,
                         "option": idx,
                         "heal_amount": opt.get("heal_amount", 0),
                         "draw_amount": opt.get("draw_amount", 0)})
