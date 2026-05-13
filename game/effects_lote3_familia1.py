"""
LOTE 3 - Família 1: Manipulação de Deck (12 ações, 10 cartas)

Ações cobertas:
- LOOK_TOP_CARDS já existe; aqui REORDER_TOP_CARDS reusa o ctx.revealed_cards
- REORDER_TOP_CARDS    - Lamboia: reorganiza top 3
- OPTIONAL_SWAP_REVEALED_TOP_CARDS - Lamboia 3 Anos: troca top de cada deck
- REVEAL_TOP_CARD_EACH_DECK   - Viní 3 Anos Abridor de Caixa
- DISCARD_LOWEST_COST_REVEALED_CARD - Viní 3 Anos Abridor de Caixa
- DRAW_HIGHEST_COST_REVEALED_CARD   - Stonks
- REVEAL_TOP_CARD_AND_CHOOSE_DRAW   - Mario (trigger ON_DRAW)
- REPLACE_DRAW_WITH_PLAY_TOP_CARD   - Portal (passivo)
- PLAY_TOP_CARD_FROM_DECK           - Aulão
- PLAY_FROM_DECK                    - Fome
- ADD_CARD_TO_DECK_POSITION_AND_SET_COST - Muriel
- SHUFFLE_THIS_INTO_DECK            - Moeda Perdida
- TRANSFORM_THIS_CARD               - Moeda Perdida (trigger ON_DRAW)

NOTA sobre triggers novos: introduzimos o trigger ON_DRAW (disparado por
draw_card no momento em que a carta sai do deck pra mão). A integração
fica em effects.py:draw_card e em engine.start_turn.
"""
from __future__ import annotations
from typing import Optional
from .state import GameState, Minion, PlayerState, CardInHand, gen_id, MAX_HAND_SIZE, MAX_BOARD_SIZE
from .cards import get_card, all_cards, card_has_tribe
from . import targeting


def register_familia1_handlers(handler):
    """Registra os handlers da Família 1 do Lote 3."""

    # ============================================================
    # REORDER_TOP_CARDS
    # ============================================================

    @handler("REORDER_TOP_CARDS")
    def _reorder_top(state, eff, source_owner, source_minion, ctx):
        """Lamboia: olha top N cartas e reorganiza.
        
        Como esta é uma decisão do jogador que normalmente exigiria UI, na
        ausência de cliente interativo aplicamos uma heurística: ordena por
        custo crescente (jogador racional pega o mais barato primeiro).
        Se quiser controle manual, o cliente pode enviar via ctx['reorder']
        uma permutação de índices.
        """
        amount = eff.get("amount", 3)
        p = state.players[source_owner]
        if len(p.deck) == 0:
            return
        n = min(amount, len(p.deck))
        top_cards = p.deck[:n]
        rest = p.deck[n:]

        # Se cliente enviou ordem específica
        client_order = ctx.get("reorder")
        if isinstance(client_order, list) and len(client_order) == n:
            try:
                top_cards = [top_cards[i] for i in client_order]
            except (IndexError, TypeError):
                pass  # fallback: heurística
        elif getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "reorder_top_cards",
                "owner": source_owner,
                "cards": list(top_cards),
                "amount": n,
            }
            state.log_event({
                "type": "choice_required",
                "kind": "reorder_top_cards",
                "player": source_owner,
                "amount": n,
            })
            return
        else:
            # Heurística: ordena por custo crescente (mais barato no topo)
            top_cards = sorted(top_cards,
                                key=lambda cid: (get_card(cid) or {}).get("cost", 99))
        
        p.deck = top_cards + rest
        state.log_event({
            "type": "reorder_top_cards",
            "player": source_owner, "amount": n,
        })


    # ============================================================
    # CHOOSE_ONE_DRAW_ONE_DISCARD_OTHER (SAS)
    # ============================================================

    @handler("CHOOSE_ONE_DRAW_ONE_DISCARD_OTHER")
    def _choose_one_draw_discard_other(state, eff, source_owner, source_minion, ctx):
        """SAS: olha as primeiras N cartas, compra uma e descarta as outras.

        Em partidas reais abre pending_choice para o cliente. Em testes/unitário,
        usa heurística simples: compra a carta de menor custo e descarta o resto.
        """
        p = state.players[source_owner]
        target = eff.get("target") or {}
        count = target.get("count", eff.get("count", 2))
        n = min(count, len(p.deck))
        if n <= 0:
            return
        top_cards = list(p.deck[:n])

        if getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "choose_draw_discard",
                "owner": source_owner,
                "cards": top_cards,
                "amount": eff.get("amount", 1),
            }
            state.log_event({"type": "choice_required",
                             "kind": "choose_draw_discard",
                             "player": source_owner,
                             "cards": list(top_cards)})
            return

        # Fallback determinístico: compra a de menor custo.
        chosen_index = min(range(n), key=lambda i: (get_card(top_cards[i]) or {}).get("cost", 99))
        chosen = top_cards[chosen_index]
        del p.deck[:n]
        if len(p.hand) < MAX_HAND_SIZE:
            new_card = CardInHand(instance_id=gen_id("h_"), card_id=chosen)
            p.hand.append(new_card)
            state.last_drawn_card_instance_ids = [new_card.instance_id]
            state.log_event({"type": "draw_from_choice",
                             "player": source_owner,
                             "card_id": chosen,
                             "instance_id": new_card.instance_id})
        else:
            state.log_event({"type": "burn", "player": source_owner, "card_id": chosen})
        for i, cid in enumerate(top_cards):
            if i != chosen_index:
                state.log_event({"type": "discard_from_deck_choice",
                                 "player": source_owner,
                                 "card_id": cid})

    # ============================================================
    # OPTIONAL_SWAP_REVEALED_TOP_CARDS
    # ============================================================

    @handler("OPTIONAL_SWAP_REVEALED_TOP_CARDS")
    def _swap_revealed_tops(state, eff, source_owner, source_minion, ctx):
        """Lamboia 3 Anos: jogador pode TROCAR os topos dos 2 decks.
        Heurística: troca SEMPRE que o oponente tem carta mais cara
        (vantajoso para o jogador). Cliente pode forçar via ctx['swap_tops'].
        """
        me = state.players[source_owner]
        opp = state.opponent_of(source_owner)
        if not me.deck or not opp.deck:
            return
        my_top_cost = (get_card(me.deck[0]) or {}).get("cost", 0)
        opp_top_cost = (get_card(opp.deck[0]) or {}).get("cost", 0)
        
        client_choice = ctx.get("swap_tops")
        if client_choice is True:
            do_swap = True
        elif client_choice is False:
            do_swap = False
        elif getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "swap_revealed_top_cards",
                "owner": source_owner,
                "my_top": me.deck[0],
                "opponent_top": opp.deck[0],
            }
            state.log_event({
                "type": "choice_required",
                "kind": "swap_revealed_top_cards",
                "player": source_owner,
                "my_top": me.deck[0],
                "opponent_top": opp.deck[0],
            })
            return
        else:
            # Heurística: troca se o do oponente é melhor
            do_swap = opp_top_cost > my_top_cost

        if do_swap:
            me.deck[0], opp.deck[0] = opp.deck[0], me.deck[0]
            state.log_event({"type": "swap_revealed_tops", "swapped": True})
        else:
            state.log_event({"type": "swap_revealed_tops", "swapped": False})

    # ============================================================
    # REVEAL_TOP_CARD_EACH_DECK
    # ============================================================

    @handler("REVEAL_TOP_CARD_EACH_DECK")
    def _reveal_top_each(state, eff, source_owner, source_minion, ctx):
        """Revela o topo do deck de AMBOS jogadores. Guarda em ctx.revealed_cards
        como lista de tuplas (owner_pid, card_id) para handlers subsequentes.
        """
        me = state.players[source_owner]
        opp = state.opponent_of(source_owner)
        revealed = []
        if me.deck:
            revealed.append((source_owner, me.deck[0]))
        if opp.deck:
            revealed.append((opp.player_id, opp.deck[0]))
        ctx["revealed_per_deck"] = revealed
        state.log_event({
            "type": "reveal_top_each_deck",
            "cards": [{"owner": pid, "card_id": cid} for pid, cid in revealed],
        })

    # ============================================================
    # DISCARD_LOWEST_COST_REVEALED_CARD
    # ============================================================

    @handler("DISCARD_LOWEST_COST_REVEALED_CARD")
    def _discard_lowest_revealed(state, eff, source_owner, source_minion, ctx):
        """Viní 3 Anos Abridor de Caixa: descarta a revelada de menor custo.
        Em caso de empate, descarta a do oponente (vantajoso).
        """
        revealed = ctx.get("revealed_per_deck") or []
        if not revealed:
            return
        # Ordena: custo asc, e em empate, oponente primeiro
        def sort_key(item):
            pid, cid = item
            cost = (get_card(cid) or {}).get("cost", 0)
            # oponente=0 vem antes (descartado em caso de empate)
            opp_first = 0 if pid != source_owner else 1
            return (cost, opp_first)
        revealed_sorted = sorted(revealed, key=sort_key)
        loser_pid, loser_cid = revealed_sorted[0]
        # Remove do deck do dono
        loser_player = state.players[loser_pid]
        if loser_player.deck and loser_player.deck[0] == loser_cid:
            loser_player.deck.pop(0)
            state.log_event({
                "type": "discard_lowest_revealed",
                "owner": loser_pid, "card_id": loser_cid,
            })

    # ============================================================
    # DRAW_HIGHEST_COST_REVEALED_CARD
    # ============================================================

    @handler("DRAW_HIGHEST_COST_REVEALED_CARD")
    def _draw_highest_revealed(state, eff, source_owner, source_minion, ctx):
        """Stonks: o dono do topo MAIS CARO recebe a carta para a mão.
        Em empate respeita tie_behavior (default: NO_EFFECT).
        """
        # Stonks usa REVEAL_TOP_CARD com mode=BOTH_DECKS antes; vou reusar
        # a lógica de revelar aqui se ainda não estiver no ctx
        revealed = ctx.get("revealed_per_deck")
        if not revealed:
            # Tenta reler topo dos 2 decks
            me = state.players[source_owner]
            opp = state.opponent_of(source_owner)
            revealed = []
            if me.deck: revealed.append((source_owner, me.deck[0]))
            if opp.deck: revealed.append((opp.player_id, opp.deck[0]))
        if not revealed:
            return

        tie = eff.get("tie_behavior", "NO_EFFECT")
        # Pega custo de cada
        with_cost = [(pid, cid, (get_card(cid) or {}).get("cost", 0)) for pid, cid in revealed]
        max_cost = max(item[2] for item in with_cost)
        winners = [(pid, cid) for pid, cid, c in with_cost if c == max_cost]
        if len(winners) > 1 and tie == "NO_EFFECT":
            state.log_event({"type": "draw_highest_revealed_tie"})
            return
        winner_pid, winner_cid = winners[0]
        winner = state.players[winner_pid]
        # Remove carta do deck e coloca na mão
        if winner.deck and winner.deck[0] == winner_cid:
            winner.deck.pop(0)
            if len(winner.hand) < MAX_HAND_SIZE:
                winner.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=winner_cid, revealed=True))
                state.log_event({
                    "type": "draw_highest_revealed",
                    "winner": winner_pid, "card_id": winner_cid,
                })
            else:
                state.log_event({"type": "burn", "player": winner_pid})

    # ============================================================
    # REVEAL_TOP_CARD_AND_CHOOSE_DRAW
    # ============================================================

    @handler("REVEAL_TOP_CARD_AND_CHOOSE_DRAW")
    def _reveal_choose_draw(state, eff, source_owner, source_minion, ctx):
        """Mario: trigger ON_DRAW. Quando comprar Mario, revela top do deck e
        o jogador escolhe se quer comprar a carta revelada (em vez da Mario).
        Heurística: pega a de maior custo (mais valiosa).
        Cliente pode controlar via ctx['choose_revealed'] (True/False).
        """
        p = state.players[source_owner]
        if not p.deck:
            return
        revealed_cid = p.deck[0]
        revealed_cost = (get_card(revealed_cid) or {}).get("cost", 0)
        mario_cost = (get_card("mario") or {}).get("cost", 0)
        
        choose_revealed = ctx.get("choose_revealed")
        if choose_revealed is None:
            # Heurística: escolhe a de maior custo (mais "valor" intuitivo)
            choose_revealed = revealed_cost > mario_cost

        if choose_revealed:
            # Compra a revelada e descarta Mario
            p.deck.pop(0)
            if len(p.hand) < MAX_HAND_SIZE:
                p.hand.append(CardInHand(instance_id=gen_id("h_"), card_id=revealed_cid, revealed=True))
                state.log_event({
                    "type": "reveal_choose_draw_picked_revealed",
                    "card_id": revealed_cid,
                })
            # Mario foi comprada e descartada (não vai pra mão)
            # NOTA: a Mario já foi colocada na mão pelo draw_card. Removemos.
            mario_card = ctx.get("just_drawn_card")
            if mario_card and mario_card in p.hand:
                p.hand.remove(mario_card)
        else:
            state.log_event({"type": "reveal_choose_draw_kept_mario"})

    # ============================================================
    # REPLACE_DRAW_WITH_PLAY_TOP_CARD
    # ============================================================

    @handler("REPLACE_DRAW_WITH_PLAY_TOP_CARD")
    def _replace_draw_play(state, eff, source_owner, source_minion, ctx):
        """Portal: passivo. No início do turno, ao invés de comprar, JOGA
        a carta do topo direto. Marcamos pending_modifier.
        """
        # Pendente até o fim do turno do dono
        state.pending_modifiers.append({
            "kind": "replace_next_draw_with_play",
            "owner": source_owner,
            "consumed": False,
            "expires_on": "end_of_turn",
        })
        state.log_event({"type": "replace_draw_play_pending"})

    # ============================================================
    # PLAY_TOP_CARD_FROM_DECK
    # ============================================================

    @handler("PLAY_TOP_CARD_FROM_DECK")
    def _play_top_card(state, eff, source_owner, source_minion, ctx):
        """Aulão: joga a próxima carta do topo do deck SEM custo.
        Para spells precisam de alvo: pegamos um alvo automático válido
        (mais "fraco" se for ofensivo, ou aleatório).
        """
        amount = eff.get("amount", 1)
        from . import engine as _engine
        for _ in range(amount):
            p = state.players[source_owner]
            if not p.deck:
                return
            cid = p.deck.pop(0)
            card = get_card(cid)
            if not card:
                continue
            # Aplica a carta como se fosse jogada com mana 0 e sem alvo
            # (handlers tratam alvo nulo como no-op para efeitos que precisam)
            _play_card_free(state, source_owner, card)
            state.log_event({"type": "play_top_card_from_deck", "card_id": cid})

    # ============================================================
    # PLAY_FROM_DECK (com filtros)
    # ============================================================

    @handler("PLAY_FROM_DECK")
    def _play_from_deck(state, eff, source_owner, source_minion, ctx):
        """Fome: joga a primeira COMIDA do deck que custe ≤3."""
        target_desc = eff.get("target") or {}
        flt = target_desc.get("filter") or {}
        max_cost = flt.get("max_cost")
        tribe = flt.get("tribe")
        ctype = flt.get("type")  # MINION ou SPELL
        selection = target_desc.get("selection", "FIRST_MATCH")
        
        p = state.players[source_owner]
        match_idx = None
        for i, cid in enumerate(p.deck):
            c = get_card(cid)
            if not c:
                continue
            if ctype and c.get("type") != ctype:
                continue
            if max_cost is not None and (c.get("cost") or 0) > max_cost:
                continue
            if tribe and not card_has_tribe(c, tribe):
                continue
            match_idx = i
            if selection == "FIRST_MATCH":
                break
        if match_idx is None:
            state.log_event({"type": "play_from_deck_no_match"})
            return
        cid = p.deck.pop(match_idx)
        card = get_card(cid)
        if not card:
            return
        _play_card_free(state, source_owner, card)
        state.log_event({"type": "play_from_deck", "card_id": cid})

    # ============================================================
    # ADD_CARD_TO_DECK_POSITION_AND_SET_COST
    # ============================================================

    @handler("ADD_CARD_TO_DECK_POSITION_AND_SET_COST")
    def _add_card_to_deck_pos(state, eff, source_owner, source_minion, ctx):
        """Muriel: adiciona Hello World no meio do deck com custo 1."""
        new_card_id = eff.get("card_id")
        position = eff.get("position", "MIDDLE")
        new_cost = eff.get("cost")
        if not new_card_id:
            return
        p = state.players[source_owner]
        # Determina índice
        if position == "TOP":
            idx = 0
        elif position == "BOTTOM":
            idx = len(p.deck)
        else:  # MIDDLE
            idx = len(p.deck) // 2
        # IMPORTANTE: o "set_cost" não pode ser permanente no JSON da carta
        # original (é uma cópia modificada). Como nosso deck é list[card_id],
        # sem instance, vamos REGISTRAR a modificação em um dict no GameState
        # que é consultado quando a carta sai pra mão.
        if not hasattr(state, "deck_card_modifiers"):
            state.deck_card_modifiers = {}
        # Usa um marker interno: o card_id passa a ser uma "tupla" representando
        # cópia única. Mas como o tipo do deck é str, vamos usar uma chave
        # única gerada e armazenar override em deck_card_modifiers.
        marker = f"{new_card_id}__mod__{gen_id('')}"
        state.deck_card_modifiers[marker] = {
            "card_id": new_card_id,
            "cost_override": new_cost,
        }
        p.deck.insert(idx, marker)
        state.log_event({
            "type": "add_card_to_deck_position",
            "card_id": new_card_id, "position": position, "cost": new_cost,
        })

    # ============================================================
    # SHUFFLE_THIS_INTO_DECK
    # ============================================================

    @handler("SHUFFLE_THIS_INTO_DECK")
    def _shuffle_this_into_deck(state, eff, source_owner, source_minion, ctx):
        """Moeda Perdida: a própria carta vai pro meio do deck.
        ctx['hand_card_id'] tem o instance_id da carta que está sendo jogada
        (vamos passar isso pelo engine.play_card).
        """
        position = eff.get("position", "MIDDLE")
        # A carta original está em ctx.source_card_id
        card_id = ctx.get("source_card_id")
        if not card_id:
            return
        p = state.players[source_owner]
        if position == "TOP":
            idx = 0
        elif position == "BOTTOM":
            idx = len(p.deck)
        else:
            idx = len(p.deck) // 2
        p.deck.insert(idx, card_id)
        state.log_event({"type": "shuffle_this_into_deck", "card_id": card_id})

    # ============================================================
    # TRANSFORM_THIS_CARD
    # ============================================================

    @handler("TRANSFORM_THIS_CARD")
    def _transform_this_card(state, eff, source_owner, source_minion, ctx):
        """Moeda Perdida (ON_DRAW): vira uma Moeda quando comprada.
        ctx['just_drawn_card'] é a CardInHand recém-comprada.
        """
        new_card = eff.get("card") or {}
        new_id = new_card.get("id", "coin")
        just_drawn = ctx.get("just_drawn_card")
        if just_drawn is None:
            return
        # Substitui o card_id da CardInHand
        just_drawn.card_id = new_id
        # Reseta modifiers
        just_drawn.cost_modifier = 0
        just_drawn.cost_override = None
        state.log_event({"type": "transform_this_card", "new_id": new_id})


def _play_card_free(state: GameState, owner: int, card: dict):
    """Joga uma carta SEM gastar mana. Usado por PLAY_TOP_CARD_FROM_DECK e
    PLAY_FROM_DECK. Não dispara battlecry de cartas que pedem alvo (vira no-op
    se não há alvo legal).
    """
    from . import effects, targeting
    from .state import Minion
    p = state.players[owner]
    is_minion = card.get("type") == "MINION"

    if is_minion:
        if len(p.board) >= MAX_BOARD_SIZE:
            return
        new_minion = Minion(
            instance_id=gen_id("m_"),
            card_id=card.get("id"),
            name=card.get("name"),
            attack=card.get("attack") or 0,
            health=card.get("health") or 1,
            max_health=card.get("health") or 1,
            tags=list(card.get("tags") or []),
            tribes=list(card.get("tribes") or []),
            effects=list(card.get("effects") or []),
            owner=owner,
            divine_shield="DIVINE_SHIELD" in (card.get("tags") or []),
            summoning_sick=True,
        )
        p.board.append(new_minion)
        state.log_event({"type": "summon", "owner": owner, "minion": new_minion.to_dict()})
        # Battlecry: tenta auto-resolver, com chosen_target=None
        # (handlers tratam alvo ausente como no-op)
        # MAS pra disparar é precisamos um chosen_target apropriado se ON_PLAY tem CHOSEN.
        # Para simplificação, dispara ON_PLAY apenas se o efeito não pede alvo.
        for eff in card.get("effects") or []:
            if eff.get("trigger") != "ON_PLAY":
                continue
            tgt = eff.get("target") or {}
            if tgt.get("mode") == "CHOSEN":
                continue  # pula efeitos que precisam de alvo escolhido
            effects.resolve_effect(state, eff, owner, new_minion,
                                    {"chosen_target": None, "is_spell": False})
    else:
        # SPELL: aplica efeitos, pulando os que pedem CHOSEN sem alvo
        for eff in card.get("effects") or []:
            if eff.get("trigger") != "ON_PLAY":
                continue
            tgt = eff.get("target") or {}
            if tgt.get("mode") == "CHOSEN":
                continue
            effects.resolve_effect(state, eff, owner, None,
                                    {"chosen_target": None, "is_spell": True})
