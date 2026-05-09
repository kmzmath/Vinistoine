"""
Lote 2 — 30 ações simples que desbloqueiam +30 cartas.

Cada handler é registrado via `handler` decorator passado por `register_lote2_handlers()`,
chamado pelo `effects.py`.

Categorias:
- DANO CALCULADO: dano baseado em vida/atk/mão/tabuleiro
- ROUBO: STEAL_HEALTH, STEAL_STATS  
- BUFFS POR CONDIÇÃO: por aliados em campo, por tribo, por danificados
- COST REDUCTION: por estado do tabuleiro/condicional
- COMPRA FILTRADA: lacaio mais barato, com tag, repetir se tribo, jogável
- DEFESA: PERMANENT_STEALTH, SET_IMMUNE, PREVENT_ATTACK_DAMAGE_TO_SELF, REFLECT_DAMAGE
- ESPECIAIS: DOUBLE_ATTACK, SWAP_ATTACK_HEALTH, REFRESH_ATTACK, SET_ATTACK
"""
from __future__ import annotations
from typing import Optional
from .state import GameState, Minion, PlayerState, CardInHand, gen_id, MAX_HAND_SIZE, MAX_BOARD_SIZE
from .cards import get_card, all_cards, card_has_tribe
from . import targeting


def register_lote2_handlers(handler):
    """Registra todos os handlers do Lote 2."""

    # ============================================================
    # DANO CALCULADO
    # ============================================================

    @handler("DAMAGE_EQUAL_TO_TARGET_HEALTH")
    def _dmg_eq_health(state, eff, source_owner, source_minion, ctx):
        """Cause dano igual à vida do alvo. Tipicamente ao próprio source."""
        from .effects import damage_character
        # Encontra o "alvo de leitura" (CHOSEN normalmente), ou usa o alvo
        # destruído imediatamente antes no mesmo contexto.
        src_desc = eff.get("source") or {}
        if src_desc.get("mode") == "DESTROYED_MINION":
            amount = int(ctx.get("destroyed_minion_health", 0) or 0)
            if amount <= 0:
                return
        else:
            ref_desc = eff.get("reference_target") or {"mode": "CHOSEN", "valid": ["ANY_MINION"]}
            ref_targets = targeting.resolve_targets(state, ref_desc, source_owner,
                                                    source_minion, ctx.get("chosen_target"))
            if not ref_targets or not isinstance(ref_targets[0], Minion):
                return
            amount = ref_targets[0].health
        # Aplica dano nos targets reais (default: SELF se source_minion existir, senão SELF_PLAYER)
        actual_desc = eff.get("target") or {"mode": "SELF_PLAYER"}
        targets = targeting.resolve_targets(state, actual_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            damage_character(state, t, amount, source_owner, source_minion,
                             is_spell=ctx.get("is_spell", False))

    @handler("DAMAGE_EQUAL_TO_TARGET_ATTACK")
    def _dmg_eq_atk(state, eff, source_owner, source_minion, ctx):
        from .effects import damage_character
        ref_desc = eff.get("reference_target") or {"mode": "CHOSEN", "valid": ["ANY_MINION"]}
        ref_targets = targeting.resolve_targets(state, ref_desc, source_owner,
                                                source_minion, ctx.get("chosen_target"))
        if not ref_targets or not isinstance(ref_targets[0], Minion):
            return
        amount = ref_targets[0].attack
        actual_desc = eff.get("target") or {"mode": "SELF_PLAYER"}
        targets = targeting.resolve_targets(state, actual_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            damage_character(state, t, amount, source_owner, source_minion,
                             is_spell=ctx.get("is_spell", False))

    @handler("DAMAGE_EQUAL_TO_HAND_SIZE")
    def _dmg_eq_hand(state, eff, source_owner, source_minion, ctx):
        from .effects import damage_character
        amount = len(state.players[source_owner].hand)
        target_desc = eff.get("target") or {"mode": "CHOSEN", "valid": ["ANY_MINION"]}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            damage_character(state, t, amount, source_owner, source_minion,
                             is_spell=ctx.get("is_spell", False))

    @handler("DAMAGE_WITH_EXCESS_TO_SELF")
    def _dmg_excess_self(state, eff, source_owner, source_minion, ctx):
        """Cabeçada do Viní: causa X de dano ao alvo, excesso volta a você."""
        from .effects import damage_character
        amount = eff.get("amount", 0)
        target_desc = eff.get("target") or {"mode": "CHOSEN", "valid": ["ANY_MINION"]}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                # Calcula excesso ANTES do dano
                excess = max(0, amount - t.health)
                damage_character(state, t, amount, source_owner, source_minion,
                                 is_spell=ctx.get("is_spell", False))
                if excess > 0:
                    me = state.players[source_owner]
                    damage_character(state, me, excess, source_owner, None,
                                     is_spell=False)

    # ============================================================
    # ROUBO de stats / vida
    # ============================================================

    @handler("STEAL_HEALTH")
    def _steal_health(state, eff, source_owner, source_minion, ctx):
        """Rouba X de vida de um lacaio (causa dano, cura herói)."""
        from .effects import damage_character
        amount = eff.get("amount", 1)
        target_desc = eff.get("target") or {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                actual = damage_character(state, t, amount, source_owner, source_minion,
                                          is_spell=ctx.get("is_spell", False))
                # Cura herói pelo dano REAL (escudo divino bloqueia roubo)
                me = state.players[source_owner]
                me.hero_health = min(30, me.hero_health + actual)
                state.log_event({"type": "heal", "player": source_owner, "amount": actual})

    @handler("STEAL_STATS")
    def _steal_stats(state, eff, source_owner, source_minion, ctx):
        """Igão Chave: rouba X/X. Reduz alvo, aumenta self."""
        atk = eff.get("attack", 1)
        hp = eff.get("health", 1)
        target_desc = eff.get("target") or {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.attack = max(0, t.attack - atk)
                t.health = max(1, t.health - hp)
                t.max_health = max(1, t.max_health - hp)
                if source_minion:
                    source_minion.attack += atk
                    source_minion.health += hp
                    source_minion.max_health += hp
                state.log_event({"type": "steal_stats",
                                 "from": t.instance_id,
                                 "to": source_minion.instance_id if source_minion else None})

    # ============================================================
    # MANIPULAÇÃO de ATAQUE
    # ============================================================

    @handler("SET_ATTACK")
    def _set_attack(state, eff, source_owner, source_minion, ctx):
        new_atk = eff.get("amount", 1)
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.attack = new_atk
                state.log_event({"type": "set_attack",
                                 "minion": t.instance_id, "value": new_atk})

    @handler("SET_HEALTH")
    def _set_health(state, eff, source_owner, source_minion, ctx):
        new_hp = eff.get("amount", 1)
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.health = new_hp
                t.max_health = max(t.max_health, new_hp)
                state.log_event({"type": "set_health",
                                 "minion": t.instance_id, "value": new_hp})

    @handler("DOUBLE_ATTACK")
    def _double_attack(state, eff, source_owner, source_minion, ctx):
        """Dobra o ataque do alvo. Para Viní Monstrão: a cada turno."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.attack *= 2
                state.log_event({"type": "double_attack",
                                 "minion": t.instance_id, "new_attack": t.attack})

    @handler("SWAP_ATTACK_HEALTH")
    def _swap_atk_hp(state, eff, source_owner, source_minion, ctx):
        """Troca atk e vida de todos (ou alvos)."""
        target_desc = eff.get("target") or {"mode": "ALL_MINIONS"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                old_atk, old_hp = t.attack, t.health
                t.attack = old_hp
                t.health = old_atk
                t.max_health = old_atk if old_atk > 0 else 1
                state.log_event({"type": "swap_atk_hp", "minion": t.instance_id})

    @handler("REFRESH_ATTACK")
    def _refresh_attack(state, eff, source_owner, source_minion, ctx):
        """Iglu Atleta: pode atacar de novo neste turno."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                had_already_attacked = int(t.attacks_this_turn or 0) > 0
                t.attacks_this_turn = 0
                t.summoning_sick = False
                # Iglu Atleta ganha uma nova oportunidade de ataque por Rapidez:
                # se ele já atacou neste turno, essa nova oportunidade deve mirar
                # apenas lacaios, não o herói inimigo.
                if had_already_attacked and "CANT_ATTACK_HERO_THIS_TURN" not in t.tags:
                    t.tags.append("CANT_ATTACK_HERO_THIS_TURN")
                    state.pending_modifiers.append({
                        "kind": "temporary_tag",
                        "owner": t.owner,
                        "minion_id": t.instance_id,
                        "tag": "CANT_ATTACK_HERO_THIS_TURN",
                        "remove_at": "end_of_turn",
                        "added_now": True,
                    })
                state.log_event({"type": "refresh_attack", "minion": t.instance_id,
                                 "minion_only": had_already_attacked})

    # ============================================================
    # DEFESA / PROTEÇÃO
    # ============================================================

    @handler("PERMANENT_STEALTH")
    def _permanent_stealth(state, eff, source_owner, source_minion, ctx):
        """Nando: STEALTH não é perdido ao atacar."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if "STEALTH" not in t.tags:
                    t.tags.append("STEALTH")
                if "PERMANENT_STEALTH" not in t.tags:
                    t.tags.append("PERMANENT_STEALTH")
                state.log_event({"type": "permanent_stealth", "minion": t.instance_id})

    @handler("SET_IMMUNE")
    def _set_immune(state, eff, source_owner, source_minion, ctx):
        """Marca lacaio como imune. Para Nando 3 Anos: imune enquanto stealth."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.immune = True
                # Tag custom: re-aplicar a imunidade quando stealth for removido vai
                # depender do engine. Por ora, adicionamos tag IMMUNE_WHILE_STEALTH
                if "IMMUNE_WHILE_STEALTH" not in t.tags:
                    t.tags.append("IMMUNE_WHILE_STEALTH")
                state.log_event({"type": "set_immune", "minion": t.instance_id})

    @handler("PREVENT_ATTACK_DAMAGE_TO_SELF")
    def _prevent_attack_dmg(state, eff, source_owner, source_minion, ctx):
        """Viní Monge: não recebe dano de ataques. Adiciona tag custom."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if "ATTACK_DAMAGE_IMMUNE" not in t.tags:
                    t.tags.append("ATTACK_DAMAGE_IMMUNE")
                state.log_event({"type": "attack_damage_immune", "minion": t.instance_id})

    @handler("REFLECT_DAMAGE")
    def _reflect_damage(state, eff, source_owner, source_minion, ctx):
        """Tronco: reflete o dano recebido. Marca tag REFLECT."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if "REFLECT" not in t.tags:
                    t.tags.append("REFLECT")
                state.log_event({"type": "reflect_damage", "minion": t.instance_id})

    @handler("POISONOUS_AGAINST_TRIBE")
    def _poisonous_tribe(state, eff, source_owner, source_minion, ctx):
        """Grama: venenosa contra Vinís. Marca tag custom."""
        tribe = eff.get("tribe", "VINI")
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                tag = f"POISONOUS_VS_{tribe}"
                if tag not in t.tags:
                    t.tags.append(tag)
                state.log_event({"type": "poisonous_against_tribe",
                                 "minion": t.instance_id, "tribe": tribe})

    @handler("GAIN_ATTACK_EQUAL_TO_DAMAGE_TAKEN")
    def _gain_atk_dmg(state, eff, source_owner, source_minion, ctx):
        """Baiano: marca tag GAIN_ATTACK_ON_DAMAGE pro engine consultar."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if "GAIN_ATTACK_ON_DAMAGE" not in t.tags:
                    t.tags.append("GAIN_ATTACK_ON_DAMAGE")

    # ============================================================
    # BUFFS por CONDIÇÃO
    # ============================================================

    @handler("BUFF_ATTACK_PER_FRIENDLY_MINION")
    def _buff_atk_per_friendly(state, eff, source_owner, source_minion, ctx):
        """Certamente Não é o Rica: +1 atk por cada outro lacaio aliado."""
        per_amount = eff.get("amount", 1)
        me = state.players[source_owner]
        n = sum(1 for m in me.board if m is not source_minion)
        gain = n * per_amount
        if source_minion and gain > 0:
            source_minion.attack += gain
            state.log_event({"type": "buff_per_friendly_minion",
                             "minion": source_minion.instance_id, "attack_gain": gain})

    @handler("BUFF_SELF_PER_FRIENDLY_MINION")
    def _buff_self_per_friendly(state, eff, source_owner, source_minion, ctx):
        """Rica 3 Anos: +1/+1 por cada outro lacaio aliado."""
        atk_per = eff.get("attack", 1)
        hp_per = eff.get("health", 1)
        me = state.players[source_owner]
        n = sum(1 for m in me.board if m is not source_minion)
        if source_minion and n > 0:
            source_minion.attack += atk_per * n
            source_minion.health += hp_per * n
            source_minion.max_health += hp_per * n
            state.log_event({"type": "buff_self_per_friendly",
                             "minion": source_minion.instance_id,
                             "attack_gain": atk_per*n, "health_gain": hp_per*n})

    @handler("BUFF_ATTACK_PER_MINION_TRIBE")
    def _buff_atk_per_tribe(state, eff, source_owner, source_minion, ctx):
        """Pastel: +1 atk por cada lacaio Brasileiro ou Chinês.
        Acumula até "expirar" — passivo recalculado a cada cleanup. Simplificação:
        recalcula no momento do trigger.
        """
        per_amount = eff.get("amount", 1)
        tribes = eff.get("tribes") or [eff.get("tribe", "BRASILEIRO")]
        if isinstance(tribes, str):
            tribes = [tribes]
        n = 0
        for m in state.all_minions():
            for tr in tribes:
                if m.has_tribe(tr):
                    n += 1
                    break
        if source_minion:
            # Para passivos contínuos, esse handler é chamado em ON_PLAY.
            # Como simplificação aplicamos UMA vez: stat estático no momento de jogar.
            # Para a real "aura" precisaria de recalcular sempre — deixaremos esse 
            # delta como permanent.
            source_minion.attack += n * per_amount
            state.log_event({"type": "buff_per_tribe",
                             "minion": source_minion.instance_id, "tribes": tribes,
                             "attack_gain": n*per_amount})

    # ============================================================
    # COST REDUCTIONS condicionais
    # ============================================================

    @handler("COST_REDUCTION_PER_FRIENDLY_MINION")
    def _cost_red_per_friendly(state, eff, source_owner, source_minion, ctx):
        """Garçom: custa 1 a menos por cada lacaio aliado.
        Mecânica: ao ENTRAR EM CAMPO o efeito já foi feito via cost menor; mas para
        cartas de mão isto deveria ser passivo de mão (IN_HAND). Por enquanto, 
        recalculamos custo de SELF (na carta na mão) ANTES de jogar — verifica
        em play_card via pending_modifiers.
        Como o handler é disparado no ON_PLAY (já entrou em campo), aqui é tarde 
        demais. A solução real é implementar IN_HAND triggers. SIMPLIFICAÇÃO: 
        registramos o efeito como NO-OP aqui e fazemos o cálculo direto em play_card.
        """
        # No-op: o cálculo é feito em play_card lendo a tag COST_REDUCTION_PER_FRIENDLY
        pass

    @handler("COST_REDUCTION_PER_DAMAGED_MINION")
    def _cost_red_per_damaged(state, eff, source_owner, source_minion, ctx):
        # Idem: cálculo em play_card via tag
        pass

    @handler("COST_REDUCTION")
    def _cost_red_conditional(state, eff, source_owner, source_minion, ctx):
        """Justiça: custa 2 a menos se oponente tem mais cartas."""
        # Idem: cálculo em play_card
        pass

    # ============================================================
    # COMPRA filtrada
    # ============================================================

    @handler("DRAW_LOWEST_COST_MINION")
    def _draw_lowest_cost_minion(state, eff, source_owner, source_minion, ctx):
        amount = eff.get("amount", 1)
        p = state.players[source_owner]
        drawn_ids = []
        for _ in range(amount):
            best_idx = -1
            best_cost = 999
            for i, cid in enumerate(p.deck):
                c = get_card(cid)
                if not c or c.get("type") != "MINION":
                    continue
                cost = c.get("cost", 0)
                if cost < best_cost:
                    best_cost = cost
                    best_idx = i
            if best_idx == -1:
                state.log_event({"type": "no_minion_to_draw"})
                continue
            cid = p.deck.pop(best_idx)
            if len(p.hand) >= MAX_HAND_SIZE:
                state.log_event({"type": "burn", "player": source_owner, "card_id": cid})
                continue
            new_card = CardInHand(instance_id=gen_id("h_"), card_id=cid)
            # Compatibilidade: se o próprio efeito trouxer buff_drawn embutido.
            buff = eff.get("buff_drawn") or {}
            if buff:
                new_card.stat_modifier = {
                    "attack": buff.get("attack", 0),
                    "health": buff.get("health", 0),
                }
            p.hand.append(new_card)
            drawn_ids.append(new_card.instance_id)
            state.log_event({"type": "draw_lowest_cost_minion",
                             "player": source_owner,
                             "instance_id": new_card.instance_id,
                             "card_id": cid, "with_buff": bool(buff)})
        state.last_drawn_card_instance_ids = drawn_ids


    @handler("BUFF_DRAWN_CARD")
    def _buff_drawn_card(state, eff, source_owner, source_minion, ctx):
        """Aplica modificador de stats nas cartas recém-compradas.

        Usado por Foco: compra o lacaio mais barato e ele recebe +2/+2
        enquanto está na mão; o modificador é aplicado ao ser invocado.
        """
        atk = eff.get("attack", eff.get("amount", {}).get("attack", 0) if isinstance(eff.get("amount"), dict) else 0)
        hp = eff.get("health", eff.get("amount", {}).get("health", 0) if isinstance(eff.get("amount"), dict) else 0)
        ids = list(getattr(state, "last_drawn_card_instance_ids", []) or [])
        if not ids:
            return
        p = state.players[source_owner]
        for ch in p.hand:
            if ch.instance_id in ids:
                ch.stat_modifier["attack"] = ch.stat_modifier.get("attack", 0) + atk
                ch.stat_modifier["health"] = ch.stat_modifier.get("health", 0) + hp
                state.log_event({"type": "buff_drawn_card",
                                 "card": ch.instance_id,
                                 "attack": atk,
                                 "health": hp})


    @handler("DRAW_MINION_FROM_DECK")
    def _draw_minion_simple(state, eff, source_owner, source_minion, ctx):
        """Vinassito: compra um lacaio (preferencial via target.preferred_*).
        Equivalente a DRAW_MINION (alias)."""
        from .effects_lote1 import register_lote1_handlers  # garantia
        # Reaproveita lógica de DRAW_MINION
        from .effects import HANDLERS
        h = HANDLERS.get("DRAW_MINION")
        if h:
            h(state, eff, source_owner, source_minion, ctx)

    @handler("DRAW_MINION_WITH_TAG")
    def _draw_minion_tag(state, eff, source_owner, source_minion, ctx):
        """Cultista do Viní: compra um lacaio com Último Suspiro do deck."""
        target_desc = eff.get("target") or {}
        required_tag = target_desc.get("required_tag") or eff.get("tag", "DEATHRATTLE")
        amount = eff.get("amount", 1)
        p = state.players[source_owner]
        for _ in range(amount):
            pool = []
            for i, cid in enumerate(p.deck):
                c = get_card(cid)
                if not c or c.get("type") != "MINION":
                    continue
                if required_tag in (c.get("tags") or []):
                    pool.append(i)
            if not pool:
                state.log_event({"type": "no_minion_with_tag", "tag": required_tag})
                continue
            idx = state.rng.choice(pool)
            cid = p.deck.pop(idx)
            if len(p.hand) >= MAX_HAND_SIZE:
                continue
            p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
            state.log_event({"type": "draw_minion_with_tag",
                             "card_id": cid, "tag": required_tag})

    @handler("DRAW_MINION_REPEAT_IF_TRIBE")
    def _draw_minion_repeat(state, eff, source_owner, source_minion, ctx):
        """Carnaval: compra lacaio. Se tribo bater, repete (até esgotar)."""
        tribe = eff.get("tribe", "BRASILEIRO")
        max_iter = eff.get("max_iterations", 10)  # safety
        p = state.players[source_owner]
        drawn = 0
        for _ in range(max_iter):
            minion_idx = [i for i, cid in enumerate(p.deck)
                          if (get_card(cid) or {}).get("type") == "MINION"]
            if not minion_idx:
                break
            idx = state.rng.choice(minion_idx)
            cid = p.deck.pop(idx)
            if len(p.hand) >= MAX_HAND_SIZE:
                state.log_event({"type": "burn", "player": source_owner})
                break
            p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
            drawn += 1
            c = get_card(cid)
            state.log_event({"type": "draw_repeat", "card_id": cid,
                             "is_target_tribe": card_has_tribe(c, tribe)})
            if not card_has_tribe(c, tribe):
                break

    @handler("DRAW_PLAYABLE_CARD")
    def _draw_playable(state, eff, source_owner, source_minion, ctx):
        """Vini 3 Anos Matemático: compra uma carta que pode jogar com a mana atual."""
        p = state.players[source_owner]
        affordable = []
        for i, cid in enumerate(p.deck):
            c = get_card(cid)
            if not c:
                continue
            if (c.get("cost") or 0) <= p.mana:
                affordable.append(i)
        # Se nada cabe na mana, compra qualquer
        pool = affordable if affordable else list(range(len(p.deck)))
        if not pool:
            state.log_event({"type": "no_card_to_draw"})
            return
        idx = state.rng.choice(pool)
        cid = p.deck.pop(idx)
        if len(p.hand) >= MAX_HAND_SIZE:
            return
        p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
        state.log_event({"type": "draw_playable_card", "card_id": cid})

    # ============================================================
    # ROUBOS pelo OPONENTE
    # ============================================================

    @handler("OPPONENT_STEALS_RANDOM_HAND_CARD")
    def _opp_steals_hand(state, eff, source_owner, source_minion, ctx):
        """Dedé Santana Corrompido: oponente rouba carta da MINHA mão."""
        amount = eff.get("amount", 1)
        me = state.players[source_owner]
        opp = state.opponent_of(source_owner)
        for _ in range(amount):
            if not me.hand:
                break
            idx = state.rng.randrange(len(me.hand))
            stolen = me.hand.pop(idx)
            if len(opp.hand) < MAX_HAND_SIZE:
                opp.hand.append(stolen)
                state.log_event({"type": "opp_stole_hand_card",
                                 "from": source_owner,
                                 "card_id": stolen.card_id})
            else:
                state.log_event({"type": "burn_stolen", "card_id": stolen.card_id})

    @handler("OPPONENT_STEALS_RANDOM_DRAWN_CARD")
    def _opp_steals_drawn(state, eff, source_owner, source_minion, ctx):
        """Investidor: oponente rouba uma das cartas recém-compradas.

        A compra já aconteceu no efeito anterior (DRAW_CARD). Este handler NÃO
        compra cartas adicionais; ele usa state.last_drawn_card_instance_ids.
        """
        amount = eff.get("amount", 1)
        me = state.players[source_owner]
        opp = state.opponent_of(source_owner)
        candidates = [c for c in me.hand
                      if c.instance_id in set(getattr(state, "last_drawn_card_instance_ids", []) or [])]
        for _ in range(amount):
            if not candidates:
                break
            idx = state.rng.randrange(len(candidates))
            stolen = candidates.pop(idx)
            if stolen not in me.hand:
                continue
            me.hand.remove(stolen)
            if len(opp.hand) < MAX_HAND_SIZE:
                opp.hand.append(stolen)
                state.log_event({"type": "opp_stole_drawn_card",
                                 "from": source_owner,
                                 "to": opp.player_id,
                                 "card_id": stolen.card_id,
                                 "instance_id": stolen.instance_id})
            else:
                state.log_event({"type": "burn_stolen", "card_id": stolen.card_id})
