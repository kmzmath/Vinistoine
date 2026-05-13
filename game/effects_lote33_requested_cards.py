"""Lote 33 - cartas solicitadas pelo usuário.

Implementa efeitos customizados para Carvalho, Dedé Santana, Sub, Dinomancia,
LAGD e Maldição do Viní Sombrio.
"""
from __future__ import annotations

from .state import Minion, gen_id, MAX_BOARD_SIZE
from .cards import all_cards, get_card, is_collectible_card
from . import targeting


def _public_card_option(card_id: str) -> dict:
    card = get_card(card_id) or {}
    return {"card_id": card_id, "name": card.get("name", card_id)}


def _random_collectible_card_ids(state, amount: int = 3) -> list[str]:
    pool = [c["id"] for c in all_cards() if is_collectible_card(c.get("id"))]
    if not pool:
        return []
    return [state.rng.choice(pool) for _ in range(amount)]


def _finish_carvalho_deck_build(state, player_id: int, picked: list[str]):
    p = state.players[player_id]
    new_deck = list(picked)
    state.rng.shuffle(new_deck)
    p.deck = new_deck
    state.log_event({
        "type": "carvalho_deck_replaced",
        "player": player_id,
        "count": len(new_deck),
        "card_ids": list(new_deck),
    })


def _start_or_continue_carvalho_choice(state, player_id: int, picked: list[str], total: int):
    if len(picked) >= total:
        state.pending_choice = None
        _finish_carvalho_deck_build(state, player_id, picked)
        return
    options = _random_collectible_card_ids(state, 3)
    state.pending_choice = {
        "choice_id": gen_id("choice_"),
        "owner": player_id,
        "kind": "build_replacement_deck",
        "step": len(picked) + 1,
        "total": total,
        "picked": list(picked),
        "options": [_public_card_option(cid) for cid in options],
    }


def resolve_build_replacement_deck_choice(state, player_id: int, choice: dict, response: dict) -> tuple[bool, str]:
    options = list(choice.get("options") or [])
    try:
        idx = int(response.get("index", response.get("chosen_index", 0)))
    except Exception:
        return False, "Opção inválida"
    if idx < 0 or idx >= len(options):
        return False, "Opção inválida"
    picked = list(choice.get("picked") or [])
    picked.append(options[idx].get("card_id"))
    total = int(choice.get("total", 10) or 10)
    state.log_event({
        "type": "carvalho_pick",
        "player": player_id,
        "step": int(choice.get("step", len(picked)) or len(picked)),
        "card_id": options[idx].get("card_id"),
    })
    _start_or_continue_carvalho_choice(state, player_id, picked, total)
    return True, "OK"


def handle_return_spell_on_death(state, minion: Minion, owner: int):
    """Processa marcas do Sub quando o lacaio encantado morre."""
    keep = []
    for pm in state.pending_modifiers:
        if pm.get("kind") != "return_spell_to_deck_on_minion_death":
            keep.append(pm)
            continue
        if pm.get("minion_id") != minion.instance_id:
            keep.append(pm)
            continue
        spell_id = pm.get("card_id") or "sub"
        deck_owner = int(pm.get("owner", owner))
        state.players[deck_owner].deck.insert(0, spell_id)
        state.log_event({
            "type": "spell_returned_to_deck_on_death",
            "player": deck_owner,
            "minion": minion.instance_id,
            "card_id": spell_id,
            "position": "TOP",
        })
    state.pending_modifiers = keep



def _make_minion_copy(template: Minion, owner: int, attack: int | None = None, health: int | None = None) -> Minion:
    copy_health = template.health if health is None else health
    copy_attack = template.attack if attack is None else attack
    return Minion(
        instance_id=gen_id("m_"),
        card_id=template.card_id,
        name=template.name,
        attack=max(0, int(copy_attack)),
        health=max(1, int(copy_health)),
        max_health=max(1, int(template.max_health if health is None else health)),
        tags=[t for t in list(template.tags or []) if not str(t).startswith("_AURA_")],
        tribes=list(template.tribes or []),
        effects=[dict(e) for e in (template.effects or [])],
        owner=owner,
        divine_shield=bool(template.divine_shield),
        frozen=bool(template.frozen),
        freeze_pending=bool(template.freeze_pending),
        silenced=bool(template.silenced),
        cant_attack=bool(template.cant_attack),
        immune=bool(template.immune),
        skip_next_attack=bool(template.skip_next_attack),
        summoning_sick=True,
    )


def _minion_from_card_id(card_id: str, owner: int, attack: int | None = None, health: int | None = None) -> Minion | None:
    card = get_card(card_id) or {}
    if card.get("type") != "MINION":
        return None
    hp = int(card.get("health") or 1) if health is None else int(health)
    atk = int(card.get("attack") or 0) if attack is None else int(attack)
    tags = list(card.get("tags") or [])
    return Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=card.get("name", card_id),
        attack=max(0, atk),
        health=max(1, hp),
        max_health=max(1, hp),
        tags=tags,
        tribes=list(card.get("tribes") or []),
        effects=[dict(e) for e in (card.get("effects") or [])],
        owner=owner,
        divine_shield="DIVINE_SHIELD" in tags,
        summoning_sick=True,
    )


def _has_deathrattle(card: dict) -> bool:
    return "DEATHRATTLE" in (card.get("tags") or []) or any(
        e.get("trigger") == "ON_DEATH" for e in (card.get("effects") or [])
    )


def _play_hand_card_instance_free(state, owner: int, hand_instance_id: str):
    p = state.players[owner]
    hand_card = next((c for c in p.hand if c.instance_id == hand_instance_id), None)
    if not hand_card:
        return
    card = get_card(hand_card.card_id) or {}
    p.hand.remove(hand_card)
    from .effects_lote3_familia1 import _play_card_free
    _play_card_free(state, owner, card)
    state.log_event({"type": "play_drawn_card_free", "player": owner, "card_id": hand_card.card_id})


def process_start_turn_transforms(state, player_id: int):
    keep = []
    for pm in list(state.pending_modifiers):
        if pm.get("kind") != "transform_trunk_into_rei_arvore_start_turn":
            keep.append(pm)
            continue
        if int(pm.get("owner", -1)) != player_id:
            keep.append(pm)
            continue
        found = state.find_minion(pm.get("minion_id"))
        if not found:
            continue
        trunk, owner = found
        if trunk.card_id != "tronco":
            continue
        board = state.players[owner].board
        try:
            idx = board.index(trunk)
        except ValueError:
            continue
        new_m = _minion_from_card_id("rei_arvore", owner)
        if not new_m:
            continue
        board[idx] = new_m
        state.log_event({
            "type": "transform",
            "player": owner,
            "old_card_id": "tronco",
            "new_card_id": "rei_arvore",
            "old_minion_id": trunk.instance_id,
            "new_minion": new_m.to_dict(),
        })
    state.pending_modifiers = keep

def register_lote33_requested_cards_handlers(handler):
    @handler("BUILD_REPLACEMENT_DECK_DISCOVER")
    def _build_replacement_deck_discover(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        destroyed = list(p.deck)
        p.deck = []
        total = int(eff.get("amount", 10) or 10)
        state.log_event({
            "type": "deck_destroyed_for_replacement",
            "player": source_owner,
            "count": len(destroyed),
        })
        if getattr(state, "manual_choices", False):
            _start_or_continue_carvalho_choice(state, source_owner, [], total)
            return
        picked = []
        for _ in range(total):
            options = _random_collectible_card_ids(state, 3)
            if not options:
                break
            picked.append(options[0])
        _finish_carvalho_deck_build(state, source_owner, picked)

    @handler("STEAL_OPPONENT_TOP_DECK_CARDS")
    def _steal_opponent_top_deck_cards(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 4) or 4)
        me = state.players[source_owner]
        opp = state.opponent_of(source_owner)
        stolen = opp.deck[:amount]
        if not stolen:
            return
        del opp.deck[:len(stolen)]
        me.deck = list(stolen) + me.deck
        state.log_event({
            "type": "reveal_and_steal_top_deck_cards",
            "player": source_owner,
            "opponent": opp.player_id,
            "cards": [{"owner": opp.player_id, "card_id": cid} for cid in stolen],
            "card_ids": list(stolen),
            "amount": len(stolen),
        })

    @handler("BUFF_AND_RETURN_SPELL_ON_DEATH")
    def _buff_and_return_spell_on_death(state, eff, source_owner, source_minion, ctx):
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"),
                                            is_spell=bool(ctx.get("is_spell")))
        atk = int(eff.get("attack", eff.get("attack_bonus", 0)) or 0)
        hp = int(eff.get("health", eff.get("health_bonus", 0)) or 0)
        spell_id = eff.get("card_id") or ctx.get("source_card_id") or "sub"
        for t in targets:
            if not isinstance(t, Minion):
                continue
            t.attack += atk
            t.max_health += hp
            t.health += hp
            state.pending_modifiers.append({
                "kind": "return_spell_to_deck_on_minion_death",
                "owner": source_owner,
                "minion_id": t.instance_id,
                "card_id": spell_id,
            })
            state.log_event({
                "type": "buff_spell_attached",
                "player": source_owner,
                "minion": t.instance_id,
                "card_id": spell_id,
                "attack_delta": atk,
                "health_delta": hp,
            })

    @handler("REVIVE_FIRST_FRIENDLY_DEAD_AS_BEAST")
    def _revive_first_friendly_dead_as_beast(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        if len(p.board) >= MAX_BOARD_SIZE:
            return
        rec = next((r for r in state.graveyard if r.get("owner") == source_owner), None)
        if not rec:
            state.log_event({"type": "no_friendly_dead_minion", "player": source_owner})
            return
        card_id = rec.get("card_id")
        card = get_card(card_id) or {}
        if card.get("type") != "MINION":
            return
        tags = list(card.get("tags") or [])
        if "CHARGE" not in tags:
            tags.append("CHARGE")
        m = Minion(
            instance_id=gen_id("m_"),
            card_id=card_id,
            name=card.get("name", rec.get("name", card_id)),
            attack=9,
            health=9,
            max_health=9,
            tags=tags,
            tribes=["FERA"],
            effects=list(card.get("effects") or []),
            owner=source_owner,
            divine_shield="DIVINE_SHIELD" in tags,
            summoning_sick=True,
        )
        p.board.append(m)
        state.log_event({"type": "resummon", "owner": source_owner, "card_id": card_id, "minion": m.to_dict()})

    @handler("RECRUIT_MINIONS_SET_2_2_WITH_TAGS")
    def _recruit_minions_set_2_2_with_tags(state, eff, source_owner, source_minion, ctx):
        amount = int(eff.get("amount", 5) or 5)
        p = state.players[source_owner]
        recruited = 0
        idx = 0
        tags_to_add = list(eff.get("tags") or ["DIVINE_SHIELD", "RUSH"])
        while recruited < amount and idx < len(p.deck) and len(p.board) < MAX_BOARD_SIZE:
            cid = p.deck[idx]
            card = get_card(cid) or {}
            if card.get("type") != "MINION":
                idx += 1
                continue
            p.deck.pop(idx)
            tags = list(card.get("tags") or [])
            for tag in tags_to_add:
                if tag not in tags:
                    tags.append(tag)
            m = Minion(
                instance_id=gen_id("m_"),
                card_id=cid,
                name=card.get("name", cid),
                attack=2,
                health=2,
                max_health=2,
                tags=tags,
                tribes=list(card.get("tribes") or []),
                effects=list(card.get("effects") or []),
                owner=source_owner,
                divine_shield="DIVINE_SHIELD" in tags,
                summoning_sick=True,
            )
            p.board.append(m)
            recruited += 1
            state.log_event({"type": "recruit", "player": source_owner, "minion": m.to_dict(), "set_stats": [2, 2]})

    @handler("DESTROY_ALL_DISCARD_HANDS_DRAW")
    def _destroy_all_discard_hands_draw(state, eff, source_owner, source_minion, ctx):
        draw_amount = int(eff.get("draw", 5) or 5)
        for m in list(state.all_minions()):
            m.health = 0
        for p in state.players:
            discarded = list(p.hand)
            p.hand.clear()
            for card in discarded:
                state.log_event({
                    "type": "discard",
                    "player": p.player_id,
                    "instance_id": card.instance_id,
                    "card_id": card.card_id,
                })
        # Import tardio para evitar ciclo durante registro de handlers.
        from .effects import draw_card
        for p in state.players:
            draw_card(state, p, draw_amount)

    @handler("HALVE_STATS_ROUNDED_DOWN")
    def _halve_stats_rounded_down_marker(state, eff, source_owner, source_minion, ctx):
        # Efeito contínuo aplicado por engine.apply_continuous_effects.
        return

    @handler("REPLACE_ALL_MINIONS_WITH_TRUNKS_NO_DEATH")
    def _replace_all_minions_with_trunks_no_death(state, eff, source_owner, source_minion, ctx):
        for player in state.players:
            new_board = []
            for old in list(player.board):
                if source_minion is not None and old is source_minion:
                    new_board.append(old)
                    continue
                trunk = _minion_from_card_id("tronco", player.player_id)
                if trunk:
                    new_board.append(trunk)
                    state.log_event({
                        "type": "replace_minion_no_death",
                        "owner": player.player_id,
                        "old_minion_id": old.instance_id,
                        "old_card_id": old.card_id,
                        "new_card_id": "tronco",
                        "new_minion": trunk.to_dict(),
                    })
            player.board = new_board

    @handler("SUMMON_TRUNK_THEN_TRANSFORM_NEXT_OWNER_TURN")
    def _summon_trunk_then_transform_next_owner_turn(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        if len(p.board) >= MAX_BOARD_SIZE:
            return
        position = ctx.get("death_index")
        if position is None:
            position = len(p.board)
        position = max(0, min(int(position), len(p.board)))
        trunk = _minion_from_card_id("tronco", source_owner)
        if not trunk:
            return
        p.board.insert(position, trunk)
        state.pending_modifiers.append({
            "kind": "transform_trunk_into_rei_arvore_start_turn",
            "owner": source_owner,
            "minion_id": trunk.instance_id,
            "card_id": "rei_arvore",
        })
        state.log_event({"type": "summon", "owner": source_owner, "minion": trunk.to_dict()})
        state.log_event({"type": "scheduled_transform", "owner": source_owner,
                         "minion": trunk.instance_id, "new_card_id": "rei_arvore"})

    @handler("DRAW_THREE_CHOOSE_ONE_PLAY_FREE")
    def _draw_three_choose_one_play_free(state, eff, source_owner, source_minion, ctx):
        from .effects import draw_card
        amount = int(eff.get("amount", 3) or 3)
        p = state.players[source_owner]
        before = {c.instance_id for c in p.hand}
        draw_card(state, p, amount)
        drawn = [c for c in p.hand if c.instance_id not in before]
        if not drawn:
            state.log_event({
                "type": "free_play_choice_skipped",
                "player": source_owner,
                "reason": "no_drawn_cards",
                "drawn": 0,
                "required": amount,
            })
            return
        if getattr(state, "manual_choices", False):
            state.pending_choice = {
                "choice_id": gen_id("choice_"),
                "kind": "choose_drawn_card_to_play_free",
                "owner": source_owner,
                "cards": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in drawn],
            }
            state.log_event({"type": "choice_required", "kind": "choose_drawn_card_to_play_free",
                             "player": source_owner})
            return
        chosen = drawn[0]
        _play_hand_card_instance_free(state, source_owner, chosen.instance_id)

    @handler("VINISH_EMPTY_WIN")
    def _vinish_empty_win(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        if p.hand or p.deck or p.board:
            return
        state.log_event({"type": "vinish_deathrattle_success",
                         "player": source_owner,
                         "target_player": 1 - source_owner})
        state.phase = "ENDED"
        state.winner = source_owner
        state.log_event({"type": "game_end", "winner": source_owner, "reason": "vinish"})

    @handler("SUMMON_DEATHRATTLE_COPIES_FROM_DECK_AND_GRAVEYARD")
    def _summon_deathrattle_copies_from_deck_and_graveyard(state, eff, source_owner, source_minion, ctx):
        p = state.players[source_owner]
        card_ids = []
        for cid in list(p.deck):
            card = get_card(cid) or {}
            if card.get("type") == "MINION" and _has_deathrattle(card):
                card_ids.append(cid)
        for rec in state.graveyard:
            if rec.get("owner") != source_owner:
                continue
            cid = rec.get("card_id")
            card = get_card(cid) or {}
            if card.get("type") == "MINION" and _has_deathrattle(card):
                card_ids.append(cid)
        for cid in card_ids:
            if len(p.board) >= MAX_BOARD_SIZE:
                break
            m = _minion_from_card_id(cid, source_owner, attack=1, health=1)
            if not m:
                continue
            if "TAUNT" not in m.tags:
                m.tags.append("TAUNT")
            m.divine_shield = "DIVINE_SHIELD" in m.tags
            p.board.append(m)
            state.log_event({"type": "summon_deathrattle_copy", "owner": source_owner,
                             "card_id": cid, "minion": m.to_dict()})

    @handler("SUMMON_EXACT_COPY_OF_CHOSEN_MINION")
    def _summon_exact_copy_of_chosen_minion(state, eff, source_owner, source_minion, ctx):
        targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                            source_minion, ctx.get("chosen_target"),
                                            is_spell=bool(ctx.get("is_spell")))
        p = state.players[source_owner]
        for t in targets:
            if not isinstance(t, Minion) or len(p.board) >= MAX_BOARD_SIZE:
                continue
            new_m = _make_minion_copy(t, source_owner)
            p.board.append(new_m)
            state.log_event({"type": "summon_exact_copy", "owner": source_owner,
                             "source": t.instance_id, "copy": new_m.to_dict()})

    @handler("SUMMON_FIRST_FRIENDLY_MINION_FROM_GRAVEYARD_AS_BEAST_9_9_CHARGE")
    def _alias_dinomancia(state, eff, source_owner, source_minion, ctx):
        _revive_first_friendly_dead_as_beast(state, eff, source_owner, source_minion, ctx)
