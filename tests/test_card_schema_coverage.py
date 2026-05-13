"""Guarda de compatibilidade entre cards.json e engine.

Este teste não exige que todos os efeitos estejam implementados agora. Ele exige
que tudo que ainda falta esteja explicitamente rastreado. Assim, quando o JSON
mudar ou quando uma action for implementada, o teste força atualizar esta lista
ou a documentação de cobertura.
"""
from __future__ import annotations

from game.card_coverage import build_coverage_report


EXPECTED_MISSING_ACTIONS = set()

EXPECTED_UNSUPPORTED_TRIGGERS = set()


def test_card_actions_are_tracked():
    report = build_coverage_report()
    assert report.missing_actions == EXPECTED_MISSING_ACTIONS
    assert report.supported_action_occurrences == 321
    assert report.total_action_occurrences == 321


def test_card_triggers_are_tracked():
    report = build_coverage_report()
    assert report.unsupported_triggers == EXPECTED_UNSUPPORTED_TRIGGERS


def test_target_modes_are_known():
    report = build_coverage_report()
    assert report.unknown_target_modes == set()


def test_condition_types_are_supported_or_tracked():
    report = build_coverage_report()
    assert report.unsupported_conditions == set()
