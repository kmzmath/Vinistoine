"""Lote 22 - correções encontradas em teste manual de cartas.

Correções:
- Hello World: ADD_COPY_TO_DECK com card_id/cost_override.
- Gusneba: SUMMON_COPY respeita copy_modifications/add_tags.
- Vinagra: BUFF_STATS aceita attack_amount/health_amount.
- Igleba: SUMMON_TOKEN aceita token inline.
- Mao Tsé-Tung: debuff também afeta mão/deck.
- Nando: PERMANENT_STEALTH como passivo real.
- Viní Flamejante / Cultista: status de dano no herói e timings.
- Perfeitinha: congela adjacentes mesmo depois de devolver o alvo à mão.
"""
from __future__ import annotations
import math

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, MAX_BOARD_SIZE, gen_id
from .cards import get_card
from . import targeting


def _insert_in_deck(deck: list[str], entries: list[str], position: str):
    pos = (position or "MIDDLE").upper()
    if pos == "TOP":
        deck[0:0] = entries
    elif pos == "BOTTOM":
        deck.extend(entries)
    else:
        idx = len(deck) // 2
        deck[idx:idx] = entries


def _deck_entry_card_id(state, entry: str) -> str:
    mods = getattr(state, "deck_card_modifiers", {}) or {}
    if entry in mods:
        return mods[entry].get("card_id", entry)
    return entry


def _deck_entry_card(state, entry: str) -> dict | None:
    return get_card(_deck_entry_card_id(state, entry))


def _make_deck_marker(state, card_id: str, *, cost_override=None,
                      cost_modifier: int = 0,
                      stat_modifier: dict | None = None,
                      extra_tags: list[str] | None = None) -> str:
    if not hasattr(state, "deck_card_modifiers"):
        state.deck_card_modifiers = {}
    marker = f"{card_id}__mod__{gen_id('')}"
    state.deck_card_modifiers[marker] = {
        "card_id": card_id,
        "cost_override": cost_override,
        "cost_modifier": int(cost_modifier or 0),
        "stat_modifier": dict(stat_modifier or {}),
        "extra_tags": list(extra_tags or []),
    }
    return marker


def _normalize_copy_modifications(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    out = []
    for tag in raw.get("tags") or raw.get("add_tags") or []:
        out.append({"action": "ADD_TAG", "tag": tag})
    for tag in raw.get("remove_tags") or []:
        out.append({"action": "REMOVE_TAG", "tag": tag})
    if "attack" in raw or "health" in raw:
        out.append({
            "action": "SET_STATS",
            "attack": raw.get("attack"),
            "health": raw.get("health"),
        })
    for trigger in raw.get("remove_triggers") or []:
        out.append({"action": "REMOVE_TRIGGER", "trigger": trigger})
    return out


def _apply_minion_modifications(minion: Minion, modifications):
    for mod in _normalize_copy_modifications(modifications):
        action = mod.get("action")
        if action == "ADD_TAG":
            tag = mod.get("tag")
            if tag and tag not in minion.tags:
                minion.tags.append(tag)
            if tag == "DIVINE_SHIELD":
                minion.divine_shield = True
        elif action == "REMOVE_TAG":
            tag = mod.get("tag")
            if tag in minion.tags:
                minion.tags.remove(tag)
            if tag == "DIVINE_SHIELD":
                minion.divine_shield = False
        elif action == "REMOVE_TRIGGER":
            trigger = mod.get("trigger")
            if trigger:
                minion.effects = [e for e in minion.effects if e.get("trigger") != trigger]
        elif action == "SET_STATS":
            if mod.get("attack") is not None:
                minion.attack = int(mod.get("attack"))
            if mod.get("health") is not None:
                minion.health = int(mod.get("health"))
                minion.max_health = int(mod.get("health"))


def _summon_token_inline(state, owner: int, token: dict) -> Minion | None:
    if len(state.players[owner].board) >= MAX_BOARD_SIZE:
        state.log_event({"type": "board_full", "owner": owner})
        return None
    m = Minion(
        instance_id=gen_id("m_"),
        card_id=token.get("id", "token"),
        name=token.get("name", "Token"),
        attack=int(token.get("attack", 0) or 0),
        health=int(token.get("health", 1) or 1),
        max_health=int(token.get("health", 1) or 1),
        tags=list(token.get("tags") or []),
        tribes=list(token.get("tribes") or []),
        effects=[dict(e) for e in (token.get("effects") or [])],
        owner=owner,
        summoning_sick=True,
        divine_shield="DIVINE_SHIELD" in (token.get("tags") or []),
    )
    if "RUSH" in m.tags:
        m.summoning_sick = True
    state.players[owner].board.append(m)
    state.log_event({"type": "summon_token", "owner": owner, "minion": m.to_dict()})
    return m


def _halve_attack(old: int, rounding: str) -> int:
    if rounding == "FLOOR":
        return max(0, old // 2)
    return max(0, math.ceil(old / 2))


def _halve_hand_card(ch: CardInHand, rounding: str):
    card = get_card(ch.card_id) or {}
    if card.get("type") != "MINION":
        return
    base = int(card.get("attack", 0) or 0)
    current = base + int((ch.stat_modifier or {}).get("attack", 0) or 0)
    new_atk = _halve_attack(current, rounding)
    ch.stat_modifier["attack"] = new_atk - base


def _halve_deck_entry(state, player: PlayerState, idx: int, rounding: str):
    entry = player.deck[idx]
    card_id = _deck_entry_card_id(state, entry)
    card = get_card(card_id) or {}
    if card.get("type") != "MINION":
        return
    mods = getattr(state, "deck_card_modifiers", {}) or {}
    old_mod = dict(mods.get(entry) or {})
    stat_mod = dict(old_mod.get("stat_modifier") or {})
    base = int(card.get("attack", 0) or 0)
    current = base + int(stat_mod.get("attack", 0) or 0)
    new_atk = _halve_attack(current, rounding)
    stat_mod["attack"] = new_atk - base

    # Se já era marker, atualiza. Se era card_id comum, substitui por marker.
    if entry in mods:
        old_mod["stat_modifier"] = stat_mod
        mods[entry] = old_mod
    else:
        player.deck[idx] = _make_deck_marker(state, card_id, stat_modifier=stat_mod)


def _adjacent_ids_for_minion(state, minion: Minion) -> list[str]:
    board = state.players[minion.owner].board
    try:
        idx = board.index(minion)
    except ValueError:
        return []
    ids = []
    if idx > 0:
        ids.append(board[idx - 1].instance_id)
    if idx < len(board) - 1:
        ids.append(board[idx + 1].instance_id)
    return ids


def register_lote22_bugfix_handlers(handler):
    @handler("BUFF_STATS")
    def _buff_stats(state, eff, source_owner, source_minion, ctx):
        amount = eff.get("amount")
        if isinstance(amount, dict):
            atk = int(amount.get("attack", 0) or 0)
            hp = int(amount.get("health", 0) or 0)
        else:
            atk = int(eff.get("attack", eff.get("attack_amount", eff.get("attack_bonus", 0))) or 0)
            hp = int(eff.get("health", eff.get("health_amount", eff.get("health_bonus", 0))) or 0)
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.attack += atk
                t.max_health += hp
                t.health += hp
                state.log_event({"type": "buff", "minion": t.instance_id,
                                 "attack_delta": atk, "health_delta": hp})

    @handler("ADD_COPY_TO_DECK")
    def _add_copy_to_deck(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 1) or 1)
        position = eff.get("position") or (eff.get("target") or {}).get("position", "MIDDLE")
        owner_idx = source_owner
        entries: list[str] = []

        # Hello World: card_id explícito, não depende de um lacaio-alvo.
        explicit_card_id = eff.get("card_id") or (eff.get("card") or {}).get("id")
        if explicit_card_id:
            for _ in range(amount):
                if eff.get("cost_override") is not None or eff.get("cost_modifier") or eff.get("stat_modifier") or eff.get("extra_tags") or eff.get("attack_bonus") or eff.get("health_bonus"):
                    entries.append(_make_deck_marker(
                        state, explicit_card_id,
                        cost_override=eff.get("cost_override"),
                        cost_modifier=int(eff.get("cost_modifier", 0) or 0),
                        stat_modifier=eff.get("stat_modifier") or {"attack": int(eff.get("attack_bonus", 0) or 0), "health": int(eff.get("health_bonus", 0) or 0)},
                        extra_tags=eff.get("extra_tags") or [],
                    ))
                else:
                    entries.append(explicit_card_id)
        else:
            target_desc = eff.get("target") or {"mode": "SELF"}
            targets = targeting.resolve_targets(state, target_desc, source_owner,
                                                source_minion, ctx.get("chosen_target"))
            for t in targets:
                if isinstance(t, Minion):
                    entries.extend([t.card_id] * amount)

        if not entries:
            return
        _insert_in_deck(state.players[owner_idx].deck, entries, position)
        state.log_event({"type": "add_copy_to_deck", "owner": owner_idx,
                         "amount": len(entries), "position": position,
                         "card_id": explicit_card_id or entries[0]})

    @handler("SUMMON_COPY")
    def _summon_copy(state, eff, source_owner, source_minion, ctx):
        if not source_minion:
            return
        from .effects import summon_minion_from_card
        n = int(eff.get("amount", 1) or 1)
        modifications = eff.get("copy_modifications", eff.get("modifications"))
        for _ in range(n):
            new_m = summon_minion_from_card(
                state, source_owner, source_minion.card_id,
                stat_override=(source_minion.attack, max(0, source_minion.max_health)),
            )
            if not new_m:
                continue
            new_m.tags = list(source_minion.tags)
            new_m.tribes = list(source_minion.tribes)
            new_m.effects = [dict(e) for e in (source_minion.effects or [])]
            new_m.divine_shield = source_minion.divine_shield or ("DIVINE_SHIELD" in new_m.tags)
            _apply_minion_modifications(new_m, modifications)
            try:
                from .effects_lote17 import register_copy_relationship_for_summon
                register_copy_relationship_for_summon(state, source_minion, new_m)
            except Exception:
                pass
            state.log_event({"type": "summon_copy", "source": source_minion.instance_id,
                             "copy": new_m.instance_id})

    @handler("SUMMON_TOKEN")
    def _summon_token(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 1) or 1)
        token = eff.get("token")
        if isinstance(token, dict):
            for _ in range(amount):
                _summon_token_inline(state, source_owner, token)
            return

        from .effects import summon_minion_from_card
        card_id = eff.get("card_id") or (eff.get("card") or {}).get("id")
        if not card_id:
            return
        for _ in range(amount):
            m = summon_minion_from_card(state, source_owner, card_id)
            if m:
                _apply_minion_modifications(m, {"add_tags": eff.get("granted_tags") or eff.get("tags") or []})

    @handler("RETURN_TO_HAND")
    def _return_to_hand(state, eff, source_owner, source_minion, ctx):
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in list(targets):
            if not isinstance(t, Minion):
                continue
            if t.instance_id == ctx.get("chosen_target"):
                ctx["adjacent_to_chosen_ids"] = _adjacent_ids_for_minion(state, t)
            owner = state.players[t.owner]
            if t in owner.board:
                owner.board.remove(t)
            if len(owner.hand) < MAX_HAND_SIZE:
                ch = CardInHand(instance_id=gen_id("h_"), card_id=t.card_id)
                owner.hand.append(ch)
                ctx.setdefault("returned_card_instance_ids", []).append(ch.instance_id)
                ctx["returned_card_instance_id"] = ch.instance_id
                state.log_event({"type": "return_to_hand", "minion": t.instance_id, "owner": t.owner,
                                 "card_instance_id": ch.instance_id})
            else:
                state.log_event({"type": "burn", "minion": t.instance_id, "owner": t.owner})

    @handler("FREEZE")
    def _freeze(state, eff, source_owner, source_minion, ctx):
        target_desc = eff.get("target") or {}
        targets = []
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
                t.frozen = True
                t.freeze_pending = (source_owner != t.owner)
                state.log_event({"type": "freeze", "minion": t.instance_id})
            elif isinstance(t, PlayerState):
                t.hero_frozen = True
                t.hero_freeze_pending = (source_owner != t.player_id)
                state.log_event({"type": "freeze_hero", "player": t.player_id})

    @handler("APPLY_START_OF_TURN_DAMAGE_STATUS")
    def _apply_start_of_turn_damage_status(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 1) or 1)
        timing = eff.get("timing", "START_OF_TARGET_TURN")
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                state.pending_modifiers.append({
                    "kind": "minion_sot_damage",
                    "minion_id": t.instance_id,
                    "amount": amount,
                    "timing": timing,
                    "owner": t.owner,
                    "expires_on": "minion_dies",
                })
                if "BURNING" not in t.tags:
                    t.tags.append("BURNING")
                state.log_event({"type": "apply_sot_damage", "minion": t.instance_id,
                                 "amount": amount, "timing": timing})
            elif isinstance(t, PlayerState):
                state.pending_modifiers.append({
                    "kind": "hero_sot_damage",
                    "player_id": t.player_id,
                    "amount": amount,
                    "timing": timing,
                    "owner": t.player_id,
                })
                state.log_event({"type": "apply_hero_sot_damage", "player": t.player_id,
                                 "amount": amount, "timing": timing})

    @handler("APPLY_PERMANENT_ATTACK_HALF_STATUS")
    def _apply_permanent_attack_half_status(state, eff, source_owner, source_minion, ctx):
        rounding = (eff.get("rounding") or "CEIL").upper()

        # Campo: respeita o alvo do efeito, geralmente ALL_OTHER_MINIONS.
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if not isinstance(t, Minion):
                continue
            if "_PERMANENT_ATTACK_HALVED" in t.tags:
                continue
            old = t.attack
            t.attack = _halve_attack(old, rounding)
            t.tags.append("_PERMANENT_ATTACK_HALVED")
            state.log_event({"type": "permanent_attack_half", "zone": "board",
                             "minion": t.instance_id, "old_attack": old,
                             "new_attack": t.attack})

        # Mão e deck: afeta os lacaios atuais dos dois jogadores.
        for p in state.players:
            for ch in p.hand:
                _halve_hand_card(ch, rounding)
            for idx in range(len(p.deck)):
                _halve_deck_entry(state, p, idx, rounding)

    @handler("PERMANENT_STEALTH")
    def _permanent_stealth(state, eff, source_owner, source_minion, ctx):
        targets = targeting.resolve_targets(state, eff.get("target") or {"mode": "SELF"},
                                            source_owner, source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if "STEALTH" not in t.tags:
                    t.tags.append("STEALTH")
                if "PERMANENT_STEALTH" not in t.tags:
                    t.tags.append("PERMANENT_STEALTH")
                state.log_event({"type": "permanent_stealth", "minion": t.instance_id})


    @handler("BUFF_HEALTH")
    def _buff_health(state, eff, source_owner, source_minion, ctx):
        amount = eff.get("amount", 0)
        if eff.get("amount_source") == "FRIENDLY_MINION_COUNT":
            amount = len(state.players[source_owner].board)
            if not eff.get("include_self", False) and source_minion in state.players[source_owner].board:
                amount -= 1
        amount = int(amount or 0)
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.max_health += amount
                t.health += amount
                state.log_event({"type": "buff_health", "minion": t.instance_id, "amount": amount})

    @handler("BUFF_SELF_PER_FRIENDLY_MINION")
    def _buff_self_per_friendly_minion(state, eff, source_owner, source_minion, ctx):
        if not source_minion:
            return
        atk_per = int(eff.get("attack_amount", eff.get("attack", 1)) or 0)
        hp_per = int(eff.get("health_amount", eff.get("health", 1)) or 0)
        board = list(state.players[source_owner].board)
        if eff.get("exclude_self", True):
            board = [m for m in board if m is not source_minion]
        n = len(board)
        if n <= 0:
            return
        source_minion.attack += atk_per * n
        source_minion.max_health += hp_per * n
        source_minion.health += hp_per * n
        state.log_event({"type": "buff_self_per_friendly",
                         "minion": source_minion.instance_id,
                         "attack_gain": atk_per*n,
                         "health_gain": hp_per*n})

    @handler("BUFF_ATTACK_PER_FRIENDLY_MINION")
    def _buff_attack_per_friendly_minion(state, eff, source_owner, source_minion, ctx):
        if not source_minion:
            return
        per_amount = int(eff.get("amount_per_minion", eff.get("amount", 1)) or 0)
        board = list(state.players[source_owner].board)
        cf = eff.get("count_filter") or {}
        if cf.get("exclude_self", True):
            board = [m for m in board if m is not source_minion]
        gain = len(board) * per_amount
        source_minion.attack += gain
        state.log_event({"type": "buff_attack_per_friendly_minion",
                         "minion": source_minion.instance_id,
                         "attack_gain": gain})

    @handler("STEAL_STATS")
    def _steal_stats(state, eff, source_owner, source_minion, ctx):
        atk = int(eff.get("attack_amount", eff.get("attack", 1)) or 0)
        hp = int(eff.get("health_amount", eff.get("health", 1)) or 0)
        target_desc = eff.get("target") or {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.attack = max(0, t.attack - atk)
                t.max_health = max(1, t.max_health - hp)
                t.health = max(1, min(t.health - hp, t.max_health))
                if source_minion:
                    source_minion.attack += atk
                    source_minion.max_health += hp
                    source_minion.health += hp
                state.log_event({"type": "steal_stats",
                                 "from": t.instance_id,
                                 "to": source_minion.instance_id if source_minion else None,
                                 "attack": atk, "health": hp})

    @handler("DRAW_CARD_FROM_DECK")
    def _draw_card_from_deck_filtered(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 1) or 1)
        filt = eff.get("filter") or {}
        p = state.players[source_owner]
        for _ in range(amount):
            all_matches = []
            preferred = []
            for i, entry in enumerate(p.deck):
                card = _deck_entry_card(state, entry)
                if not card:
                    continue
                if filt.get("type") and card.get("type") != filt.get("type"):
                    continue
                if filt.get("tribe"):
                    from .cards import card_has_tribe
                    if not card_has_tribe(card, filt.get("tribe")):
                        continue
                all_matches.append(i)
                if filt.get("preferred_id") and card.get("id") == filt.get("preferred_id"):
                    preferred.append(i)
            pool = preferred or all_matches
            if not pool:
                state.log_event({"type": "no_filtered_card_to_draw", "filter": filt})
                return
            idx = pool[0]
            entry = p.deck.pop(idx)
            card_id = _deck_entry_card_id(state, entry)
            mods = getattr(state, "deck_card_modifiers", {}) or {}
            mod = mods.pop(entry, {}) if entry in mods else {}
            if len(p.hand) >= MAX_HAND_SIZE:
                state.log_event({"type": "burn", "player": source_owner, "card_id": card_id})
                continue
            p.hand.append(CardInHand(
                instance_id=gen_id("h_"),
                card_id=card_id,
                cost_override=mod.get("cost_override"),
                cost_modifier=int(mod.get("cost_modifier", 0) or 0),
                stat_modifier=dict(mod.get("stat_modifier") or {}),
                extra_tags=list(mod.get("extra_tags") or []),
            ))
            state.log_event({"type": "draw_card_from_deck_filtered",
                             "player": source_owner, "card_id": card_id, "filter": filt})

    @handler("DRAW_MINION_FROM_DECK")
    def _draw_minion_from_deck_preferred(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 1) or 1)
        preferred = eff.get("preferred") or []
        if isinstance(preferred, str):
            preferred = [preferred]
        p = state.players[source_owner]
        from .cards import card_has_tribe
        for _ in range(amount):
            minions = []
            preferred_idx = []
            for i, entry in enumerate(p.deck):
                card = _deck_entry_card(state, entry)
                if not card or card.get("type") != "MINION":
                    continue
                minions.append(i)
                if any(card.get("id") == pref.lower() or card_has_tribe(card, pref) for pref in preferred):
                    preferred_idx.append(i)
            pool = preferred_idx or minions
            if not pool:
                state.log_event({"type": "no_minion_to_draw"})
                return
            idx = state.rng.choice(pool)
            entry = p.deck.pop(idx)
            card_id = _deck_entry_card_id(state, entry)
            mods = getattr(state, "deck_card_modifiers", {}) or {}
            mod = mods.pop(entry, {}) if entry in mods else {}
            if len(p.hand) >= MAX_HAND_SIZE:
                state.log_event({"type": "burn", "player": source_owner, "card_id": card_id})
                continue
            p.hand.append(CardInHand(
                instance_id=gen_id("h_"), card_id=card_id,
                cost_override=mod.get("cost_override"),
                cost_modifier=int(mod.get("cost_modifier", 0) or 0),
                stat_modifier=dict(mod.get("stat_modifier") or {}),
                extra_tags=list(mod.get("extra_tags") or []),
            ))
            state.log_event({"type": "draw_minion_from_deck",
                             "player": source_owner, "card_id": card_id,
                             "preferred": preferred})

    @handler("DAMAGE")
    def _damage(state, eff, source_owner, source_minion, ctx):
        from .effects import damage_character, heal_character, check_condition
        cond = eff.get("condition") or {}
        if isinstance(cond, dict) and cond.get("type") and not check_condition(state, cond, source_owner, source_minion, ctx):
            return
        amount = int(eff.get("amount", 0) or 0)
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        is_spell = ctx.get("is_spell", False)
        total = 0
        for t in targets:
            dealt = damage_character(state, t, amount, source_owner, source_minion, is_spell=is_spell)
            total += int(dealt or 0)
        if eff.get("lifesteal") and total > 0:
            heal_character(state, state.players[source_owner], total)

    @handler("DAMAGE_ALL_ENEMY_MINIONS")
    def _damage_all_enemy_minions(state, eff, source_owner, source_minion, ctx):
        from .effects import damage_character
        amount = eff.get("amount", 1)
        if eff.get("amount_source") == "ENEMY_MINION_COUNT":
            amount = len(state.opponent_of(source_owner).board)
        amount = int(amount or 0)
        for m in list(state.opponent_of(source_owner).board):
            damage_character(state, m, amount, source_owner, source_minion,
                             is_spell=ctx.get("is_spell", False))

    @handler("DAMAGE_ADJACENT_MINIONS")
    def _damage_adjacent_minions(state, eff, source_owner, source_minion, ctx):
        from .effects import damage_character
        amount = eff.get("amount", 1)
        if eff.get("amount_source") == "SELF_ATTACK" and source_minion:
            amount = source_minion.attack
        amount = int(amount or 0)
        target_desc = eff.get("target") or {"mode": "ADJACENT_TO_PREVIOUS_TARGET"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                damage_character(state, t, amount, source_owner, source_minion,
                                 is_spell=ctx.get("is_spell", False))

    @handler("SET_COST")
    def _set_cost_with_returned_card(state, eff, source_owner, source_minion, ctx):
        new_cost = int(eff.get("amount") if "amount" in eff else eff.get("cost", 0))
        target_desc = eff.get("target") or {}
        targets = []
        if target_desc.get("mode") == "RETURNED_CARD":
            ids = ctx.get("returned_card_instance_ids") or []
            hand = state.players[source_owner].hand
            targets = [c for c in hand if c.instance_id in ids]
        else:
            targets = targeting.resolve_targets(state, target_desc, source_owner,
                                                source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, CardInHand):
                if not hasattr(t, "_previous_cost_override"):
                    # dataclasses allow dynamic attrs.
                    t._previous_cost_override = t.cost_override
                    t._previous_cost_modifier = t.cost_modifier
                t.cost_override = new_cost
                t.cost_modifier = 0
                if eff.get("duration") == "THIS_TURN":
                    state.pending_modifiers.append({
                        "kind": "temporary_card_cost_override",
                        "owner": source_owner,
                        "card_instance_id": t.instance_id,
                        "previous_cost_override": getattr(t, "_previous_cost_override", None),
                        "previous_cost_modifier": getattr(t, "_previous_cost_modifier", 0),
                        "expires_on": "end_of_turn",
                    })
                state.log_event({"type": "cost_set", "card": t.instance_id, "cost": new_cost})


    @handler("DRAW_CARD")
    def _draw_card_conditional(state, eff, source_owner, source_minion, ctx):
        from .effects import check_condition, draw_card
        cond = eff.get("condition") or {}
        if isinstance(cond, dict) and cond.get("type") and not check_condition(state, cond, source_owner, source_minion, ctx):
            return
        amount = int(eff.get("amount", 1) or 1)
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        if not targets:
            targets = [state.players[source_owner]]
        for t in targets:
            if isinstance(t, PlayerState):
                before = len(t.hand)
                draw_card(state, t, amount)
                if eff.get("reveal"):
                    for ch in t.hand[before:]:
                        state.log_event({"type": "reveal_drawn_card",
                                         "player": t.player_id,
                                         "card_id": ch.card_id,
                                         "instance_id": ch.instance_id})

    @handler("DESTROY")
    def _destroy_conditional(state, eff, source_owner, source_minion, ctx):
        from .effects import check_condition
        cond = eff.get("condition") or {}
        if isinstance(cond, dict) and cond.get("type") and not check_condition(state, cond, source_owner, source_minion, ctx):
            return
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                ctx["destroyed_minion_id"] = t.instance_id
                ctx["destroyed_minion_health"] = max(0, t.health)
                ctx["destroyed_minion_attack"] = max(0, t.attack)
                t.health = 0
                state.log_event({"type": "destroy", "minion": t.instance_id})

