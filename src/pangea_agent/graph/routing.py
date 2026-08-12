from __future__ import annotations

from typing import Literal

from .state import PangeaState


QualityRoute = Literal["finalize_report", "generate_test_cases"]


def route_after_quality_gate(state: PangeaState) -> QualityRoute:
    """Route once after the quality gate.

    First version only allows one rework loop. Unresolved items should be
    recorded instead of blocking the whole run.
    """

    report = state.get("quality_report", {})
    if report.get("status") == "REWORK" and not report.get("rework_attempted"):
        return "generate_test_cases"
    return "finalize_report"
