"""Lote 29 - features/animações e correções de Caverna/Dorminhoco.

- REVEAL_TOP_CARD com BOTH_DECKS registra duas cartas reveladas.
- DRAW_HIGHEST_COST_REVEALED_CARD e DISCARD_LOWEST_COST_REVEALED_CARD logam eventos completos para animação.
"""
from __future__ import annotations

from .state import CardInHand, MAX_HAND_SIZE
from .cards import get_card


def register_lote29_features_handlers(handler):
    @handler("REVEAL_TOP_CARD")
    def _reveal_top_card_both_decks(state, eff, source_owner, source_minion, ctx):
        target_desc = eff.get("target") or {"mode": "SELF_DECK"}
        mode = target_desc.get("mode")

        if mode == "BOTH_DECKS":
            revealed = []
            for p in state.players:
                if p.deck:
                    revealed.append((p.player_id, p.deck[0]))
            ctx["revealed_per_deck"] = revealed
            if revealed:
                state.log_event({
                    "type": "reveal_top_each_deck",
                    "cards": [{"owner": pid, "card_id": cid} for pid, cid in revealed],
                    "source_card_id": ctx.get("source_card_id"),
                })
            return

        if mode == "OPPONENT_DECK":
            p = state.opponent_of(source_owner)
        else:
            p = state.players[source_owner]
        if not p.deck:
            return
        cid = p.deck[0]
        ctx["revealed_card"] = cid
        ctx["revealed_card_owner"] = p.player_id
        state.log_event({"type": "reveal_top_card", "player": p.player_id, "card_id": cid})

    @handler("DISCARD_LOWEST_COST_REVEALED_CARD")
    def _discard_lowest_revealed_with_card_id(state, eff, source_owner, source_minion, ctx):
        revealed = ctx.get("revealed_per_deck") or []
        if not revealed:
            return
        def sort_key(item):
            pid, cid = item
            cost = (get_card(cid) or {}).get("cost", 0)
            opp_first = 0 if pid != source_owner else 1
            return (cost, opp_first)
        loser_pid, loser_cid = sorted(revealed, key=sort_key)[0]
        loser = state.players[loser_pid]
        if loser.deck and loser.deck[0] == loser_cid:
            loser.deck.pop(0)
            state.log_event({
                "type": "discard_lowest_revealed",
                "owner": loser_pid,
                "player": loser_pid,
                "card_id": loser_cid,
                "source_card_id": ctx.get("source_card_id"),
            })

    @handler("DRAW_HIGHEST_COST_REVEALED_CARD")
    def _draw_highest_revealed_with_animation_data(state, eff, source_owner, source_minion, ctx):
        revealed = ctx.get("revealed_per_deck")
        if not revealed:
            me = state.players[source_owner]
            opp = state.opponent_of(source_owner)
            revealed = []
            if me.deck:
                revealed.append((source_owner, me.deck[0]))
            if opp.deck:
                revealed.append((opp.player_id, opp.deck[0]))
            if revealed:
                ctx["revealed_per_deck"] = revealed
                state.log_event({
                    "type": "reveal_top_each_deck",
                    "cards": [{"owner": pid, "card_id": cid} for pid, cid in revealed],
                    "source_card_id": ctx.get("source_card_id"),
                })
        if not revealed:
            return

        tie = eff.get("tie_behavior", "NO_EFFECT")
        with_cost = [(pid, cid, (get_card(cid) or {}).get("cost", 0)) for pid, cid in revealed]
        max_cost = max(c for _, _, c in with_cost)
        winners = [(pid, cid) for pid, cid, cost in with_cost if cost == max_cost]
        if len(winners) > 1 and tie == "NO_EFFECT":
            state.log_event({"type": "draw_highest_revealed_tie", "source_card_id": ctx.get("source_card_id")})
            return

        winner_pid, winner_cid = winners[0]
        winner = state.players[winner_pid]
        if winner.deck and winner.deck[0] == winner_cid:
            winner.deck.pop(0)
            if len(winner.hand) < MAX_HAND_SIZE:
                ch = CardInHand(
                    instance_id=__import__("game.state", fromlist=["gen_id"]).gen_id("h_"),
                    card_id=winner_cid,
                    revealed=True,
                )
                winner.hand.append(ch)
                state.log_event({
                    "type": "draw_highest_revealed",
                    "winner": winner_pid,
                    "player": winner_pid,
                    "card_id": winner_cid,
                    "instance_id": ch.instance_id,
                    "reason": "effect",
                    "source_card_id": ctx.get("source_card_id"),
                })
            else:
                state.log_event({"type": "burn", "player": winner_pid, "card_id": winner_cid})
