"""Lote 19 — triggers de dano, summon e carta jogada.

Triggers integrados:
- ON_DAMAGE_TAKEN
- ON_DAMAGE_DEALT_BY_SELF
- ON_SELF_HERO_TAKES_DAMAGE
- ON_FRIENDLY_SUMMON
- ON_PLAY_CARD

Actions auxiliares:
- REFLECT_DAMAGE
- GAIN_ATTACK_EQUAL_TO_DAMAGE_TAKEN
"""
from __future__ import annotations

from .state import Minion, PlayerState
from . import targeting


def _ctx_amount(ctx: dict, raw, default: int = 0) -> int:
    if raw == "DAMAGE_TAKEN":
        return int(ctx.get("damage_taken", default) or 0)
    if raw == "DAMAGE_DEALT":
        return int(ctx.get("damage_dealt", default) or 0)
    try:
        return int(raw)
    except Exception:
        return int(default or 0)


def register_lote19_handlers(handler):
    @handler("REFLECT_DAMAGE")
    def _reflect_damage(state, eff, source_owner, source_minion, ctx):
        """Tronco: reflete o dano recebido na fonte do dano."""
        amount = _ctx_amount(ctx, eff.get("amount"), ctx.get("damage_taken", 0))
        if amount <= 0:
            return
        target_desc = dict(eff.get("target") or {})
        if target_desc.get("mode") == "DAMAGE_SOURCE":
            targets = targeting.resolve_targets(
                state, target_desc, source_owner, source_minion,
                ctx.get("damage_source_minion_id"),
            )
        else:
            targets = targeting.resolve_targets(
                state, target_desc, source_owner, source_minion,
                ctx.get("chosen_target"),
            )

        from .effects import damage_character
        for t in targets:
            if isinstance(t, (Minion, PlayerState)):
                damage_character(
                    state, t, amount,
                    source_owner=source_owner,
                    source_minion=source_minion,
                    is_spell=False,
                )
                state.log_event({
                    "type": "reflect_damage",
                    "source": source_minion.instance_id if source_minion else None,
                    "target": t.instance_id if isinstance(t, Minion) else f"hero:{t.player_id}",
                    "amount": amount,
                })

    @handler("GAIN_ATTACK_EQUAL_TO_DAMAGE_TAKEN")
    def _gain_attack_equal_to_damage_taken(state, eff, source_owner, source_minion, ctx):
        """Baiano: ganha ataque igual ao dano recebido."""
        amount = _ctx_amount(ctx, eff.get("amount", "DAMAGE_TAKEN"), ctx.get("damage_taken", 0))
        if amount <= 0:
            return
        targets = targeting.resolve_targets(
            state, eff.get("target") or {"mode": "SELF"},
            source_owner, source_minion, ctx.get("chosen_target"),
        )
        for t in targets:
            if isinstance(t, Minion):
                t.attack += amount
                state.log_event({
                    "type": "gain_attack_equal_to_damage_taken",
                    "minion": t.instance_id,
                    "amount": amount,
                })


def fire_damage_taken_trigger(state, damaged_minion: Minion, amount: int,
                              source_owner: int, source_minion: Minion | None,
                              is_spell: bool = False):
    """Dispara ON_DAMAGE_TAKEN em quem recebeu dano."""
    if amount <= 0 or damaged_minion.silenced:
        return
    from .effects import fire_minion_trigger
    fire_minion_trigger(
        state, damaged_minion, "ON_DAMAGE_TAKEN",
        extra_ctx={
            "damage_taken": amount,
            "chosen_target": source_minion.instance_id if source_minion else None,
            "damage_source_minion_id": source_minion.instance_id if source_minion else None,
            "damage_source_owner": source_owner,
            "is_spell": is_spell,
        },
    )


def fire_damage_dealt_by_self_trigger(state, source_minion: Minion | None,
                                      damaged_target, amount: int):
    """Dispara ON_DAMAGE_DEALT_BY_SELF na fonte do dano."""
    if amount <= 0 or source_minion is None or source_minion.silenced:
        return
    if not isinstance(damaged_target, Minion):
        return
    from .effects import fire_minion_trigger
    fire_minion_trigger(
        state, source_minion, "ON_DAMAGE_DEALT_BY_SELF",
        extra_ctx={
            "damage_dealt": amount,
            "chosen_target": damaged_target.instance_id,
            "damaged_minion_id": damaged_target.instance_id,
        },
    )


def fire_self_hero_takes_damage_triggers(state, player: PlayerState, amount: int,
                                         source_owner: int, source_minion: Minion | None):
    """Dispara ON_SELF_HERO_TAKES_DAMAGE nos lacaios do herói ferido."""
    if amount <= 0:
        return
    from .effects import fire_minion_trigger
    for m in list(player.board):
        fire_minion_trigger(
            state, m, "ON_SELF_HERO_TAKES_DAMAGE",
            extra_ctx={
                "damage_taken": amount,
                "chosen_target": source_minion.instance_id if source_minion else None,
                "damage_source_minion_id": source_minion.instance_id if source_minion else None,
                "damage_source_owner": source_owner,
            },
        )


def fire_friendly_summon_triggers(state, summoned_minion: Minion, owner: int):
    """Dispara ON_FRIENDLY_SUMMON em outros lacaios aliados."""
    from .effects import fire_minion_trigger
    for m in list(state.players[owner].board):
        if m is summoned_minion:
            continue
        fire_minion_trigger(
            state, m, "ON_FRIENDLY_SUMMON",
            extra_ctx={
                "summoned_minion": summoned_minion.instance_id,
                "summoned_card_id": summoned_minion.card_id,
                "source_card_id": summoned_minion.card_id,
                "chosen_target": summoned_minion.instance_id,
            },
        )
