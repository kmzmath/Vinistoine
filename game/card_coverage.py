"""Card schema coverage helpers.

This module inspects ``game/data/cards.json`` and compares the vocabulary used
by the cards (actions, triggers, target modes and conditions) against what the
engine currently knows how to handle.

It is intentionally dependency-free so it can run in pytest, locally, and on
Render build shells without extra tooling.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .cards import all_cards, load_cards
from . import effects


# Triggers that are directly fired by the current engine/effects code.
# Some triggers are dynamic helpers rather than direct fire_minion_trigger calls.
ENGINE_SUPPORTED_TRIGGERS: set[str] = {
    "ON_PLAY",
    "ON_DRAW",
    "ON_DEATH",
    "START_OF_TURN",
    "ON_TURN_START",
    "END_OF_TURN",
    "END_OF_YOUR_TURN",
    "ON_END_TURN",
    "AFTER_FRIENDLY_MINION_PLAY",
    "ON_FRIENDLY_MINION_PLAYED",
    "AFTER_YOU_PLAY_MINION",
    "AFTER_YOU_PLAY_CARD",
    "OPPONENT_SPELL_PLAYED",
    "AFTER_ATTACK",
    "ON_ATTACK_MINION",
    # Evaluated by cost calculation / keyword helpers rather than a trigger loop.
    "IN_HAND",
    "COST_MODIFIER",
    "WHILE_STEALTHED",
    "WHILE_DAMAGED",
    "PASSIVE",
    "ON_FRIENDLY_CHARACTER_ATTACKED",
    "ON_PLAY_EMPOWERED",
    "ON_EMPOWER",
    "ON_COMBO",
    "AURA",
    "ON_PLAY_CARD",
    "ON_FRIENDLY_SUMMON",
    "ON_SELF_HERO_TAKES_DAMAGE",
    "ON_DAMAGE_DEALT_BY_SELF",
    "ON_DAMAGE_TAKEN",
    "DURING_YOUR_TURN",
    "ACTIVATED_ABILITY",
    "ON_SUMMONED_COPY_DEATH",
    "FRIENDLY_MINIONS_SUMMONED",
    "ON_SELF_TAKE_DAMAGE",
}


# Target modes that ``targeting.resolve_targets`` can resolve into concrete
# Minion/PlayerState objects.
RESOLVER_TARGET_MODES: set[str] = {
    "SELF",
    "SELF_PLAYER",
    "SELF_HERO",
    "OPPONENT_PLAYER",
    "OPPONENT_HERO",
    "ALL_FRIENDLY_MINIONS",
    "FRIENDLY_MINIONS",
    "ALL_OTHER_FRIENDLY_MINIONS",
    "OTHER_FRIENDLY_MINIONS",
    "ALL_ENEMY_MINIONS",
    "ENEMY_MINIONS",
    "ALL_MINIONS",
    "ALL_OTHER_MINIONS",
    "ALL_MINIONS_EXCEPT_CHOSEN",
    "ALL_CHARACTERS",
    "ALL_OTHER_CHARACTERS",
    "ALL_ENEMIES",
    "FRIENDLY_CHARACTERS",
    "SELF_BOARD",
    "MINIONS_WITH_TRIBE",
    "ALL_MINIONS_EXCEPT_TRIBE",
    "ENEMY_MINIONS_EXCEPT_TRIBE",
    "DAMAGED_MINION",
    "CHOSEN",
    "SAME_AS_PREVIOUS_TARGET",
    "SAME_AS_PREVIOUS",
    "REVEALED_CARD",
    "ADJACENT_MINIONS",
    "ADJACENT_FRIENDLY_MINIONS",
    "ADJACENT_TO_PREVIOUS_TARGET",
    "ADJACENT_TO_CHOSEN_MINION",
    "ADJACENT_TO_ATTACK_TARGET",
    "RANDOM_ENEMY_MINION",
    "RANDOM_FRIENDLY_MINION",
    "RANDOM_MINION",
    "RANDOM_ENEMY_CHARACTER",
}


# Target modes that are intentionally action-local or choice/local-context based.
# They should not be resolved globally as Minion/PlayerState targets.
ACTION_LOCAL_TARGET_MODES: set[str] = {
    "SELF_CARD",
    "SELF_DECK",
    "SELF_DECK_TOP",
    "OPPONENT_DECK",
    "SELF_HAND",
    "OPPONENT_HAND",
    "BOTH_DECKS",
    "BOTH_PLAYERS_HAND",
    "OWNER_OF_HIGHEST_COST_REVEALED_CARD",
    "REVEALED_CARDS",
    "CARDS_DRAWN_BY_PREVIOUS_EFFECT",
    "NEXT_CARD_PLAYED_THIS_TURN",
    "FRIENDLY_GRAVEYARD",
    "FRIENDLY_HAND_AND_DECK",
    "DRAWN_CARD",
    "RETURNED_CARD",
    "PLAYED_MINION",
    "DAMAGE_SOURCE",
    "ORIGINAL_SELF",
    "CHOSEN_EACH",
    "CHOSEN_FRIENDLY_HAND_CARD",
    "CHOSEN_FRIENDLY_HAND_CARDS",
    "CHOSEN_ADJACENT_DIRECTION",
}

KNOWN_TARGET_MODES: set[str] = RESOLVER_TARGET_MODES | ACTION_LOCAL_TARGET_MODES


SUPPORTED_CONDITIONS: set[str] = {
    "FRIENDLY_MINION_TRIBE_EXISTS",
    "FRIENDLY_MINION_COUNT_GTE",
    "FRIENDLY_MINION_COUNT_AT_LEAST",
    "ENEMY_MINION_COUNT_GTE",
    "HAND_SIZE_GTE",
    "FRIENDLY_MINION_EXISTS",
    "ENEMY_MINION_EXISTS",
    "TARGET_HAS_TRIBE",
    "TARGET_IS_FROZEN",
    "TARGET_ATTACK_LESS_THAN_SELF_ATTACK",
    "ONLY_FRIENDLY_MINION",
    "PLAYED_CARD_TRIBE",
    "CARD_TRIBE",
    "SUMMONED_MINION_TRIBE",
    "OPPONENT_HAS_MORE_CARDS_IN_HAND",
    "SELF_DAMAGED",
    "ATTACKED_ENEMY",
    "ATTACKED_OPPONENT_HERO",
    "ATTACKED_ENEMY_HERO",
    "DIED_DURING_OPPONENT_TURN",
    "SELF_COULD_ATTACK_BUT_DID_NOT",
}


@dataclass(frozen=True)
class CoverageReport:
    cards_total: int
    action_counts: Counter[str]
    trigger_counts: Counter[str]
    target_mode_counts: Counter[str]
    condition_counts: Counter[str]
    action_cards: dict[str, set[str]]
    trigger_cards: dict[str, set[str]]
    target_mode_cards: dict[str, set[str]]
    condition_cards: dict[str, set[str]]

    @property
    def implemented_actions(self) -> set[str]:
        return set(effects.HANDLERS)

    @property
    def used_actions(self) -> set[str]:
        return set(self.action_counts)

    @property
    def missing_actions(self) -> set[str]:
        return self.used_actions - self.implemented_actions

    @property
    def supported_action_occurrences(self) -> int:
        return sum(n for action, n in self.action_counts.items() if action in self.implemented_actions)

    @property
    def total_action_occurrences(self) -> int:
        return sum(self.action_counts.values())

    @property
    def action_occurrence_coverage(self) -> float:
        total = self.total_action_occurrences
        return 0.0 if total == 0 else self.supported_action_occurrences / total

    @property
    def unsupported_triggers(self) -> set[str]:
        return set(self.trigger_counts) - ENGINE_SUPPORTED_TRIGGERS

    @property
    def unknown_target_modes(self) -> set[str]:
        return set(self.target_mode_counts) - KNOWN_TARGET_MODES

    @property
    def unsupported_conditions(self) -> set[str]:
        return set(self.condition_counts) - SUPPORTED_CONDITIONS


def _walk_effect(effect: dict[str, Any], card_id: str, report_data: dict[str, Any]) -> None:
    action = effect.get("action")
    if action:
        report_data["action_counts"][action] += 1
        report_data["action_cards"][action].add(card_id)

    trigger = effect.get("trigger")
    if trigger:
        report_data["trigger_counts"][trigger] += 1
        report_data["trigger_cards"][trigger].add(card_id)

    target = effect.get("target")
    if isinstance(target, dict):
        mode = target.get("mode")
        if mode:
            report_data["target_mode_counts"][mode] += 1
            report_data["target_mode_cards"][mode].add(card_id)

    condition = effect.get("condition")
    if isinstance(condition, dict):
        condition_type = condition.get("type")
        if condition_type:
            report_data["condition_counts"][condition_type] += 1
            report_data["condition_cards"][condition_type].add(card_id)

    for key in ("effects", "additional_effects", "choices", "then", "else", "sequence"):
        nested = effect.get(key)
        if isinstance(nested, list):
            for child in nested:
                if isinstance(child, dict):
                    _walk_effect(child, card_id, report_data)
        elif isinstance(nested, dict):
            _walk_effect(nested, card_id, report_data)


def build_coverage_report() -> CoverageReport:
    load_cards()
    data = {
        "action_counts": Counter(),
        "trigger_counts": Counter(),
        "target_mode_counts": Counter(),
        "condition_counts": Counter(),
        "action_cards": defaultdict(set),
        "trigger_cards": defaultdict(set),
        "target_mode_cards": defaultdict(set),
        "condition_cards": defaultdict(set),
    }
    cards = all_cards()
    for card in cards:
        card_id = card.get("id", "<missing-id>")
        for effect in card.get("effects") or []:
            if isinstance(effect, dict):
                _walk_effect(effect, card_id, data)

    return CoverageReport(
        cards_total=len(cards),
        action_counts=data["action_counts"],
        trigger_counts=data["trigger_counts"],
        target_mode_counts=data["target_mode_counts"],
        condition_counts=data["condition_counts"],
        action_cards=dict(data["action_cards"]),
        trigger_cards=dict(data["trigger_cards"]),
        target_mode_cards=dict(data["target_mode_cards"]),
        condition_cards=dict(data["condition_cards"]),
    )


def _md_table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    headers = list(headers)
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return out


def render_markdown_report(report: CoverageReport | None = None) -> str:
    report = report or build_coverage_report()
    pct = report.action_occurrence_coverage * 100
    lines: list[str] = [
        "# Cobertura do `cards.json`",
        "",
        "Relatório gerado por `python scripts/card_coverage.py`.",
        "",
        "## Resumo",
        "",
        f"- Cartas carregadas: **{report.cards_total}**",
        f"- Actions distintas usadas no JSON: **{len(report.used_actions)}**",
        f"- Handlers registrados na engine: **{len(report.implemented_actions)}**",
        f"- Actions usadas pelo JSON sem handler: **{len(report.missing_actions)}**",
        f"- Ocorrências de action cobertas: **{report.supported_action_occurrences}/{report.total_action_occurrences}** ({pct:.1f}%)",
        f"- Triggers distintas: **{len(report.trigger_counts)}**",
        f"- Triggers ainda não disparadas pela engine: **{len(report.unsupported_triggers)}**",
        f"- Target modes distintos: **{len(report.target_mode_counts)}**",
        f"- Target modes desconhecidos: **{len(report.unknown_target_modes)}**",
        f"- Condition types distintos: **{len(report.condition_counts)}**",
        f"- Conditions sem suporte explícito: **{len(report.unsupported_conditions)}**",
        "",
    ]

    if report.missing_actions:
        lines.extend(["## Actions sem handler", ""])
        rows = []
        for action in sorted(report.missing_actions):
            cards = sorted(report.action_cards.get(action, set()))
            sample = ", ".join(cards[:4]) + ("..." if len(cards) > 4 else "")
            rows.append((action, report.action_counts[action], len(cards), sample))
        lines.extend(_md_table(["Action", "Ocorrências", "Cartas", "Exemplos"], rows))
        lines.append("")

    if report.unsupported_triggers:
        lines.extend(["## Triggers não disparadas diretamente", ""])
        rows = []
        for trigger in sorted(report.unsupported_triggers):
            cards = sorted(report.trigger_cards.get(trigger, set()))
            sample = ", ".join(cards[:4]) + ("..." if len(cards) > 4 else "")
            rows.append((trigger, report.trigger_counts[trigger], len(cards), sample))
        lines.extend(_md_table(["Trigger", "Ocorrências", "Cartas", "Exemplos"], rows))
        lines.append("")

    if report.unknown_target_modes:
        lines.extend(["## Target modes desconhecidos", ""])
        rows = []
        for mode in sorted(report.unknown_target_modes):
            cards = sorted(report.target_mode_cards.get(mode, set()))
            sample = ", ".join(cards[:4]) + ("..." if len(cards) > 4 else "")
            rows.append((mode, report.target_mode_counts[mode], len(cards), sample))
        lines.extend(_md_table(["Mode", "Ocorrências", "Cartas", "Exemplos"], rows))
        lines.append("")

    if report.unsupported_conditions:
        lines.extend(["## Conditions sem suporte explícito", ""])
        rows = []
        for condition in sorted(report.unsupported_conditions):
            cards = sorted(report.condition_cards.get(condition, set()))
            sample = ", ".join(cards[:4]) + ("..." if len(cards) > 4 else "")
            rows.append((condition, report.condition_counts[condition], len(cards), sample))
        lines.extend(_md_table(["Condition", "Ocorrências", "Cartas", "Exemplos"], rows))
        lines.append("")

    lines.extend([
        "## Interpretação",
        "",
        "- `Actions sem handler` são a prioridade funcional: a carta pode ser jogada, mas o efeito é registrado como `unimplemented_action`.",
        "- `Triggers não disparadas diretamente` indicam efeitos passivos ou eventos que ainda precisam de integração na engine.",
        "- `Target modes desconhecidos` devem ser resolvidos antes de criar novas cartas com esses modos.",
        "- `Conditions sem suporte explícito` fazem `CONDITIONAL_EFFECTS` retornar falso e registrar `unimplemented_condition`.",
        "",
    ])
    return "\n".join(lines)
