"""
Lote 1 de handlers - ações com maior impacto direto.
Importado por effects.py (que registra os handlers via @handler).

Cobre:
- REDUCE_COST, INCREASE_COST, SET_COST, SET_STATS, SET_MAX_HEALTH
- RETURN_TO_HAND
- AWAKEN, BECOME_DORMANT
- CHOOSE_ONE
- ADD_TRIBE, ADD_COPY_TO_DECK
- DRAW_CARD_FROM_DECK, DRAW_FROM_OPPONENT_DECK
- DRAW_MINION, DRAW_SPELL, RECRUIT_MINION
- DAMAGE_ADJACENT_MINIONS, DAMAGE_ALL_ENEMY_MINIONS
- LOOK_TOP_CARDS, REVEAL_TOP_CARD
- RESURRECT_LAST_FRIENDLY_DEAD_MINION
- ADD_MODIFIED_COPY_TO_HAND
- APPLY_START_OF_TURN_DAMAGE_STATUS
"""
from __future__ import annotations
from typing import Optional
from .state import GameState, Minion, PlayerState, CardInHand, gen_id, MAX_HAND_SIZE, MAX_BOARD_SIZE
from .cards import get_card, all_cards, card_has_tribe
from . import targeting


def register_lote1_handlers(handler):
    """Registra todos os handlers do Lote 1. Chamado pelo effects.py."""

    # ============================================================
    # CUSTO DE CARTAS
    # ============================================================

    @handler("REDUCE_COST")
    def _reduce_cost(state, eff, source_owner, source_minion, ctx):
        """Reduz o custo de cartas-alvo na mão (ou cartas futuras).
        Modes suportados: SELF (a carta na mão de auto-buff), CHOSEN (mão),
        NEXT_CARD_PLAYED_THIS_TURN, ALL_FRIENDLY_MINIONS (em mão? não - geralmente
        só faz sentido em mão).
        """
        amount = eff.get("amount", 1)
        target_desc = eff.get("target") or {}
        mode = target_desc.get("mode")

        if mode == "NEXT_CARD_PLAYED_THIS_TURN":
            # Vira um modificador pendente: a próxima carta jogada que satisfaça
            # o filtro recebe -amount no custo.
            valid_filter = target_desc.get("valid") or []
            state.pending_modifiers.append({
                "kind": "next_card_cost_reduction",
                "owner": source_owner,
                "amount": amount,
                "valid": valid_filter,
                "expires_on": "end_of_turn",
                "consumed": False,
            })
            state.log_event({"type": "pending_cost_reduction",
                             "owner": source_owner, "amount": amount})
            return

        if mode == "SELF_CARD":
            # A própria carta na mão (REDUCE_COST passivo de mão)
            # No JSON: trigger IN_HAND. Por enquanto aplicamos o efeito como
            # imediato - quando essa função roda em IN_HAND, source ainda é a
            # carta. Vamos pular pois requer recálculo a cada estado.
            return

        if mode == "DRAWN_CARD":
            # Guilãozinho/Foco: efeitos encadeados sobre a última carta comprada.
            ids = list(getattr(state, "last_drawn_card_instance_ids", []) or [])
            for cid in ids:
                for card_in_hand in state.players[source_owner].hand:
                    if card_in_hand.instance_id == cid:
                        card_in_hand.cost_modifier -= amount
                        state.log_event({"type": "cost_reduced",
                                         "card": card_in_hand.instance_id,
                                         "amount": amount,
                                         "reason": "DRAWN_CARD"})
            return

        # Caso geral: alvos são cartas/lacaios. Para handlers do Lote 1,
        # tratamos só REDUCE_COST em cartas na mão escolhidas (raro no JSON).
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, CardInHand):
                t.cost_modifier -= amount
                state.log_event({"type": "cost_reduced",
                                 "card": t.instance_id, "amount": amount})

    @handler("INCREASE_COST")
    def _increase_cost(state, eff, source_owner, source_minion, ctx):
        amount = eff.get("amount", 1)
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, CardInHand):
                t.cost_modifier += amount
                state.log_event({"type": "cost_increased",
                                 "card": t.instance_id, "amount": amount})

    @handler("SET_COST")
    def _set_cost(state, eff, source_owner, source_minion, ctx):
        new_cost = eff.get("amount") if "amount" in eff else eff.get("cost", 0)
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, CardInHand):
                t.cost_override = new_cost
                t.cost_modifier = 0
                state.log_event({"type": "cost_set",
                                 "card": t.instance_id, "cost": new_cost})

    # ============================================================
    # SET_STATS / SET_MAX_HEALTH
    # ============================================================

    @handler("SET_STATS")
    def _set_stats(state, eff, source_owner, source_minion, ctx):
        atk = eff.get("attack")
        hp = eff.get("health")
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if atk is not None:
                    t.attack = atk
                if hp is not None:
                    t.health = hp
                    t.max_health = hp
                state.log_event({"type": "set_stats",
                                 "minion": t.instance_id, "attack": t.attack,
                                 "health": t.health})

    @handler("SET_MAX_HEALTH")
    def _set_max_health(state, eff, source_owner, source_minion, ctx):
        hp = eff.get("amount") or eff.get("health") or 1
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                t.max_health = hp
                t.health = min(t.health, hp)

    # ============================================================
    # RETURN_TO_HAND
    # ============================================================

    @handler("RETURN_TO_HAND")
    def _return_to_hand(state, eff, source_owner, source_minion, ctx):
        """Tira lacaios do campo e devolve à mão do dono."""
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in list(targets):  # cópia: vamos modificar boards
            if not isinstance(t, Minion):
                continue
            owner = state.player_at(t.owner)
            if t in owner.board:
                owner.board.remove(t)
            # Adiciona de volta à mão se houver espaço
            if len(owner.hand) < MAX_HAND_SIZE:
                owner.hand.append(CardInHand(
                    instance_id=gen_id("h_"),
                    card_id=t.card_id,
                ))
                state.log_event({"type": "return_to_hand",
                                 "minion": t.instance_id, "owner": t.owner})
            else:
                state.log_event({"type": "burn",
                                 "minion": t.instance_id, "owner": t.owner})

    # ============================================================
    # AWAKEN / BECOME_DORMANT
    # ============================================================

    @handler("BECOME_DORMANT")
    def _become_dormant(state, eff, source_owner, source_minion, ctx):
        """Lacaio fica dormente (não pode atacar nem ser atacado).
        Registramos como tag DORMANT + cant_attack + immune.
        """
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if "DORMANT" not in t.tags:
                    t.tags.append("DORMANT")
                t.cant_attack = True
                t.immune = True
                state.log_event({"type": "dormant", "minion": t.instance_id})

    @handler("AWAKEN")
    def _awaken(state, eff, source_owner, source_minion, ctx):
        """Desperta um lacaio dormente."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                if "DORMANT" in t.tags:
                    t.tags.remove("DORMANT")
                t.cant_attack = False
                t.immune = False
                t.summoning_sick = False  # acorda já podendo atacar
                state.log_event({"type": "awaken", "minion": t.instance_id})

    # ============================================================
    # CHOOSE_ONE
    # ============================================================

    @handler("CHOOSE_ONE")
    def _choose_one(state, eff, source_owner, source_minion, ctx):
        """Jogador escolhe uma das N opções. O cliente envia a opção via
        `chose_index` (0-based) no payload do play_card. Se não for fornecido,
        usa a primeira opção como fallback (para evitar bloqueio).
        """
        choices = eff.get("choices") or []
        if not choices:
            return
        idx = ctx.get("chose_index")
        if idx is None:
            idx = 0
        idx = max(0, min(idx, len(choices) - 1))
        chosen = choices[idx]
        from .effects import resolve_effect
        resolve_effect(state, chosen, source_owner, source_minion, ctx)
        state.log_event({"type": "choose_one",
                         "option_index": idx,
                         "action": chosen.get("action")})

    # ============================================================
    # TRIBOS
    # ============================================================

    @handler("ADD_TRIBE")
    def _add_tribe(state, eff, source_owner, source_minion, ctx):
        tribe = eff.get("tribe")
        if not tribe:
            return
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion) and tribe not in t.tribes:
                t.tribes.append(tribe)
                state.log_event({"type": "add_tribe",
                                 "minion": t.instance_id, "tribe": tribe})

    # ============================================================
    # COMPRA / DECK
    # ============================================================

    @handler("ADD_COPY_TO_DECK")
    def _add_copy_to_deck(state, eff, source_owner, source_minion, ctx):
        """Embaralha uma cópia (do lacaio-alvo, ou de SELF) no deck."""
        target_desc = eff.get("target") or {"mode": "SELF"}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        amount = eff.get("amount", 1)
        owner_idx = source_owner
        # destino do deck: por padrão dono do source
        for t in targets:
            cid = t.card_id if isinstance(t, Minion) else None
            if not cid:
                continue
            for _ in range(amount):
                state.players[owner_idx].deck.append(cid)
            # embaralha
            state.rng.shuffle(state.players[owner_idx].deck)
            state.log_event({"type": "add_copy_to_deck",
                             "card_id": cid, "owner": owner_idx, "amount": amount})

    @handler("DRAW_CARD_FROM_DECK")
    def _draw_card_from_deck(state, eff, source_owner, source_minion, ctx):
        """Compra uma carta específica do topo do próprio deck - equivalente
        a DRAW_CARD por enquanto (sem filtros adicionais). Filtros como
        'preferred_tribe' são tratados em DRAW_MINION."""
        amount = eff.get("amount", 1)
        p = state.players[source_owner]
        for _ in range(amount):
            if not p.deck:
                return
            cid = p.deck.pop(0)
            if len(p.hand) < MAX_HAND_SIZE:
                p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
                state.log_event({"type": "draw", "player": source_owner})
            else:
                state.log_event({"type": "burn", "player": source_owner})

    @handler("DRAW_FROM_OPPONENT_DECK")
    def _draw_from_opponent_deck(state, eff, source_owner, source_minion, ctx):
        """Rouba carta do topo do deck do oponente e coloca na própria mão."""
        amount = eff.get("amount", 1)
        me = state.players[source_owner]
        opp = state.opponent_of(source_owner)
        for _ in range(amount):
            if not opp.deck:
                state.log_event({"type": "no_card_to_steal", "player": source_owner})
                return
            cid = opp.deck.pop(0)
            if len(me.hand) < MAX_HAND_SIZE:
                me.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
                state.log_event({"type": "steal_card",
                                 "from": opp.player_id, "to": source_owner,
                                 "card_id": cid})
            else:
                state.log_event({"type": "burn", "player": source_owner})

    @handler("DRAW_MINION")
    def _draw_minion(state, eff, source_owner, source_minion, ctx):
        """Compra um lacaio aleatório do deck. Se 'preferred_tribe' for dado,
        prioriza essa tribo; cai pra qualquer lacaio se não achar.
        """
        target_desc = eff.get("target") or {}
        preferred = target_desc.get("preferred_tribe")
        amount = eff.get("amount", 1)
        p = state.players[source_owner]
        from .effects import effective_card_has_tribe

        for _ in range(amount):
            # Filtra cartas do deck que são MINION
            deck_minions_idx = []
            preferred_idx = []
            for i, cid in enumerate(p.deck):
                c = get_card(cid)
                if not c or c.get("type") != "MINION":
                    continue
                deck_minions_idx.append(i)
                if preferred and effective_card_has_tribe(state, source_owner, c, preferred):
                    preferred_idx.append(i)

            pool = preferred_idx if preferred_idx else deck_minions_idx
            if not pool:
                state.log_event({"type": "no_minion_to_draw"})
                continue

            chosen_idx = state.rng.choice(pool)
            cid = p.deck.pop(chosen_idx)
            if len(p.hand) < MAX_HAND_SIZE:
                p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
                state.log_event({"type": "draw_minion",
                                 "player": source_owner, "card_id": cid,
                                 "preferred_tribe": preferred})
            else:
                state.log_event({"type": "burn", "player": source_owner})

    @handler("DRAW_SPELL")
    def _draw_spell(state, eff, source_owner, source_minion, ctx):
        """Compra um feitiço aleatório do deck."""
        amount = eff.get("amount", 1)
        p = state.players[source_owner]
        for _ in range(amount):
            spell_idx = [i for i, cid in enumerate(p.deck)
                         if (get_card(cid) or {}).get("type") == "SPELL"]
            if not spell_idx:
                state.log_event({"type": "no_spell_to_draw"})
                continue
            chosen_idx = state.rng.choice(spell_idx)
            cid = p.deck.pop(chosen_idx)
            if len(p.hand) < MAX_HAND_SIZE:
                p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=cid))
                state.log_event({"type": "draw_spell", "player": source_owner,
                                 "card_id": cid})
            else:
                state.log_event({"type": "burn", "player": source_owner})

    @handler("RECRUIT_MINION")
    def _recruit_minion(state, eff, source_owner, source_minion, ctx):
        """Invoca um lacaio direto do deck (não vai pra mão, vai pro campo)."""
        target_desc = eff.get("target") or {}
        preferred = target_desc.get("preferred_tribe")
        max_cost = eff.get("max_cost")
        p = state.players[source_owner]
        from .effects import effective_card_has_tribe

        if len(p.board) >= MAX_BOARD_SIZE:
            state.log_event({"type": "board_full_recruit"})
            return

        # Pool: lacaios do deck (com filtros)
        pool = []
        preferred_pool = []
        for i, cid in enumerate(p.deck):
            c = get_card(cid)
            if not c or c.get("type") != "MINION":
                continue
            if max_cost is not None and (c.get("cost") or 0) > max_cost:
                continue
            pool.append(i)
            if preferred and effective_card_has_tribe(state, source_owner, c, preferred):
                preferred_pool.append(i)

        chosen_pool = preferred_pool if preferred_pool else pool
        if not chosen_pool:
            state.log_event({"type": "no_minion_to_recruit"})
            return
        idx = state.rng.choice(chosen_pool)
        cid = p.deck.pop(idx)
        c = get_card(cid)
        m = Minion(
            instance_id=gen_id("m_"),
            card_id=cid,
            name=c["name"],
            attack=c.get("attack") or 0,
            health=c.get("health") or 1,
            max_health=c.get("health") or 1,
            tags=list(c.get("tags") or []),
            tribes=list(c.get("tribes") or []),
            effects=list(c.get("effects") or []),
            owner=source_owner,
            divine_shield="DIVINE_SHIELD" in (c.get("tags") or []),
            summoning_sick=True,
        )
        p.board.append(m)
        state.log_event({"type": "recruit", "player": source_owner,
                         "minion": m.to_dict()})

    @handler("LOOK_TOP_CARDS")
    def _look_top_cards(state, eff, source_owner, source_minion, ctx):
        """Apenas registra para o jogador olhar - em produção mandaríamos
        os IDs ao cliente do dono. Por hora salva no event_log e em ctx pra
        encadeamentos."""
        amount = eff.get("amount", 1)
        p = state.players[source_owner]
        top = p.deck[:amount]
        # Guarda no contexto pra usar em REORDER ou OPTIONAL_SWAP encadeados
        ctx["revealed_cards"] = top
        state.log_event({"type": "look_top_cards",
                         "player": source_owner, "cards": list(top)})

    @handler("REVEAL_TOP_CARD")
    def _reveal_top_card(state, eff, source_owner, source_minion, ctx):
        """Revela carta do topo (visível para ambos)."""
        target_desc = eff.get("target") or {"mode": "SELF_DECK"}
        mode = target_desc.get("mode")
        # Decide qual deck
        if mode == "OPPONENT_DECK":
            p = state.opponent_of(source_owner)
        else:
            p = state.players[source_owner]
        if not p.deck:
            return
        cid = p.deck[0]
        ctx["revealed_card"] = cid
        ctx["revealed_card_owner"] = p.player_id
        state.log_event({"type": "reveal_top_card",
                         "player": p.player_id, "card_id": cid})

    # ============================================================
    # DAMAGE em vizinhos
    # ============================================================

    @handler("DAMAGE_ADJACENT_MINIONS")
    def _damage_adjacent(state, eff, source_owner, source_minion, ctx):
        """Causa dano em lacaios adjacentes ao alvo anterior (ou source)."""
        from .effects import damage_character
        amount = eff.get("amount", 1)
        # Resolve o pivô a partir do contexto (último alvo escolhido)
        target_desc = eff.get("target") or {"mode": "ADJACENT_TO_PREVIOUS_TARGET"}
        # ctx.chosen_target funciona como "alvo anterior" no caso comum:
        # ex.: "DAMAGE alvo X" + "DAMAGE_ADJACENT amount=2 mode=ADJACENT_TO_PREVIOUS_TARGET"
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                damage_character(state, t, amount, source_owner, source_minion,
                                 is_spell=ctx.get("is_spell", False))

    @handler("DAMAGE_ALL_ENEMY_MINIONS")
    def _damage_all_enemy(state, eff, source_owner, source_minion, ctx):
        from .effects import damage_character
        amount = eff.get("amount", 1)
        foe = state.opponent_of(source_owner)
        # cópia: matar lacaio durante iteração mexe na lista
        for m in list(foe.board):
            damage_character(state, m, amount, source_owner, source_minion,
                             is_spell=ctx.get("is_spell", False))

    # ============================================================
    # RESURRECT
    # ============================================================

    @handler("RESURRECT_LAST_FRIENDLY_DEAD_MINION")
    def _resurrect(state, eff, source_owner, source_minion, ctx):
        """Ressuscita o último lacaio aliado morto (do graveyard)."""
        # Procura no graveyard: o último entry com owner == source_owner
        candidate = None
        for entry in reversed(state.graveyard):
            if entry.get("owner") == source_owner:
                candidate = entry
                break
        if candidate is None:
            state.log_event({"type": "no_dead_to_resurrect"})
            return
        p = state.players[source_owner]
        if len(p.board) >= MAX_BOARD_SIZE:
            state.log_event({"type": "board_full_resurrect"})
            return
        cid = candidate["card_id"]
        c = get_card(cid)
        if not c:
            return
        m = Minion(
            instance_id=gen_id("m_"),
            card_id=cid,
            name=c["name"],
            attack=c.get("attack") or 0,
            health=c.get("health") or 1,
            max_health=c.get("health") or 1,
            tags=list(c.get("tags") or []),
            tribes=list(c.get("tribes") or []),
            effects=list(c.get("effects") or []),
            owner=source_owner,
            divine_shield="DIVINE_SHIELD" in (c.get("tags") or []),
            summoning_sick=True,
        )
        p.board.append(m)
        state.log_event({"type": "resurrect", "minion": m.to_dict()})

    # ============================================================
    # ADD_MODIFIED_COPY_TO_HAND
    # ============================================================

    @handler("ADD_MODIFIED_COPY_TO_HAND")
    def _add_modified_copy(state, eff, source_owner, source_minion, ctx):
        """Adiciona à mão uma cópia da carta-alvo, com stats/cost modificados.
        Usado por ex.: "Capataz" (cria 1/1 que custa 1 do lacaio escolhido)."""
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        mods = eff.get("copy_modifiers") or {}
        p = state.players[source_owner]
        for t in targets:
            if not isinstance(t, Minion):
                continue
            if len(p.hand) >= MAX_HAND_SIZE:
                state.log_event({"type": "burn", "player": source_owner})
                continue
            # Cria carta na mão com os modificadores
            ch = CardInHand(
                instance_id=gen_id("h_"),
                card_id=t.card_id,
                cost_override=mods.get("cost"),
            )
            base = get_card(t.card_id) or {}
            if "attack" in mods:
                ch.stat_modifier["attack"] = mods["attack"] - (base.get("attack") or 0)
            if "health" in mods:
                ch.stat_modifier["health"] = mods["health"] - (base.get("health") or 0)
            p.hand.append(ch)
            state.log_event({"type": "add_modified_copy_to_hand",
                             "card_id": t.card_id, "modifiers": mods})

    # ============================================================
    # APPLY_START_OF_TURN_DAMAGE_STATUS (debuff de dano contínuo)
    # ============================================================

    @handler("APPLY_START_OF_TURN_DAMAGE_STATUS")
    def _apply_sot_damage(state, eff, source_owner, source_minion, ctx):
        """Marca um lacaio com dano-por-turno. Aplicamos via tag custom +
        modifier no GameState."""
        amount = eff.get("amount", 1)
        target_desc = eff.get("target") or {}
        targets = targeting.resolve_targets(state, target_desc, source_owner,
                                            source_minion, ctx.get("chosen_target"))
        for t in targets:
            if isinstance(t, Minion):
                state.pending_modifiers.append({
                    "kind": "minion_sot_damage",
                    "minion_id": t.instance_id,
                    "amount": amount,
                    "expires_on": "minion_dies",
                })
                if "POISONED" not in t.tags:
                    t.tags.append("POISONED")
                state.log_event({"type": "apply_sot_damage",
                                 "minion": t.instance_id, "amount": amount})
