#!/usr/bin/env python3
"""Generate docs/CARD_COVERAGE.md for the current card JSON."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game.card_coverage import build_coverage_report, render_markdown_report


def main() -> int:
    report = build_coverage_report()
    output = ROOT / "docs" / "CARD_COVERAGE.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown_report(report), encoding="utf-8")
    print(f"Wrote {output.relative_to(ROOT)}")
    print(
        "Action coverage: "
        f"{report.supported_action_occurrences}/{report.total_action_occurrences} "
        f"({report.action_occurrence_coverage * 100:.1f}%)"
    )
    print(f"Missing actions: {len(report.missing_actions)}")
    print(f"Unsupported triggers: {len(report.unsupported_triggers)}")
    print(f"Unknown target modes: {len(report.unknown_target_modes)}")
    print(f"Unsupported conditions: {len(report.unsupported_conditions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
