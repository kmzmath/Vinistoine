"""Lote 26 - ajustes de UI/mecânica e correções de cartas reportadas.

Correções principais:
- Cardume adiciona Peixes à mão via `card_id` direto.
- Ramoni renasce a partir dos stats base da carta, reduzindo -1/-1 por morte.
- Lamboinha Rook and How cria cópia 6 mana 6/6.
- Viní Religioso aumenta vida máxima de herói.
- Vic só retorna com o alvo quando mata em ataque.
"""
from __future__ import annotations

from .state import CardInHand, Minion, PlayerState, MAX_HAND_SIZE, gen_id
from .cards import get_card
from . import targeting


def register_lote26_requested_fixes_handlers(handler):
    @handler("ADD_CARD_TO_HAND")
    def _add_card_to_hand_direct_id(state, eff, source_owner, source_minion, ctx):
        card_id = eff.get("card_id") or (eff.get("card") or {}).get("id")
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

    @handler("SET_MAX_HEALTH")
    def _set_max_health_player_or_minion(state, eff, source_owner, source_minion, ctx):
        hp = int(eff.get("amount") or eff.get("health") or 1)
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.max_health = hp
                t.health = min(t.health, hp)
                state.log_event({"type": "set_max_health", "target_kind": "minion",
                                 "target_id": t.instance_id, "amount": hp})
            elif isinstance(t, PlayerState):
                t.hero_max_health = hp
                t.hero_health = min(t.hero_health, hp)
                state.log_event({"type": "set_max_health", "target_kind": "hero",
                                 "target_id": t.player_id, "amount": hp})

    @handler("ADD_MODIFIED_COPY_TO_HAND")
    def _add_modified_copy_to_hand_direct_fields(state, eff, source_owner, source_minion, ctx):
        targets = targeting.resolve_targets(state, eff.get("target") or {},
                                            source_owner, source_minion,
                                            ctx.get("chosen_target"))
        p = state.players[source_owner]
        # Handler antigo lia apenas copy_modifiers. Este também lê campos diretos
        # usados por Lamboinha Rook and How: attack/health/cost.
        mods = dict(eff.get("copy_modifiers") or {})
        if "attack" in eff:
            mods["attack"] = int(eff.get("attack"))
        if "health" in eff:
            mods["health"] = int(eff.get("health"))
        if "cost" in eff:
            mods["cost"] = int(eff.get("cost"))

        for t in targets:
            if not isinstance(t, Minion):
                continue
            if len(p.hand) >= MAX_HAND_SIZE:
                state.log_event({"type": "burn", "player": source_owner, "card_id": t.card_id})
                continue
            base = get_card(t.card_id) or {}
            ch = CardInHand(
                instance_id=gen_id("h_"),
                card_id=t.card_id,
                cost_override=mods.get("cost"),
            )
            if "attack" in mods:
                ch.stat_modifier["attack"] = int(mods["attack"]) - int(base.get("attack", 0) or 0)
            if "health" in mods:
                ch.stat_modifier["health"] = int(mods["health"]) - int(base.get("health", 1) or 1)
            p.hand.append(ch)
            state.log_event({"type": "add_modified_copy_to_hand",
                             "player": source_owner,
                             "instance_id": ch.instance_id,
                             "card_id": t.card_id,
                             "modifiers": mods})

    @handler("SUMMON_SELF_WITH_STAT_MODIFIER")
    def _summon_self_with_base_stat_modifier(state, eff, source_owner, source_minion, ctx):
        """Ramoni: evoca nova instância baseada no template original.

        Não usa buffs/debuffs do lacaio morto. Cada renascimento acumula -1/-1
        sobre o 5/3 base. Quando a próxima vida seria <=0, não evoca.
        """
        if not source_minion:
            return
        from .effects import summon_minion_from_card
        base = get_card(source_minion.card_id) or {}
        base_atk = int(base.get("attack", 0) or 0)
        base_hp = int(base.get("health", 1) or 1)
        atk_mod = int(eff.get("attack_modifier", 0) or 0)
        hp_mod = int(eff.get("health_modifier", 0) or 0)

        deaths = 0
        for tag in source_minion.tags:
            if tag.startswith("_SELF_RESUMMONS:"):
                try:
                    deaths = int(tag.split(":", 1)[1])
                except Exception:
                    deaths = 0
                break
        next_deaths = deaths + 1
        next_atk = base_atk + atk_mod * next_deaths
        next_hp = base_hp + hp_mod * next_deaths
        if next_hp <= 0:
            state.log_event({"type": "self_resummon_stopped",
                             "card_id": source_minion.card_id,
                             "attack": next_atk, "health": next_hp})
            return

        m = summon_minion_from_card(state, source_owner, source_minion.card_id,
                                    stat_override=(max(0, next_atk), next_hp))
        if m:
            # Remove contador antigo se veio do template/cópia e marca novo.
            m.tags = [t for t in m.tags if not t.startswith("_SELF_RESUMMONS:")]
            m.tags.append(f"_SELF_RESUMMONS:{next_deaths}")
            state.log_event({"type": "summon_self_with_stat_modifier",
                             "source": source_minion.instance_id,
                             "new": m.instance_id,
                             "attack": m.attack,
                             "health": m.health})
