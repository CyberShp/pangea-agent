from __future__ import annotations

from typing import Literal

from .run_store import load_progress
from .state import PangeaState


StartRoute = Literal["resolve_repositories", "index_materials", "build_inventory", "make_analysis_units", "advance_run"]
AdvanceRoute = Literal["finalize_report", "end"]


def route_after_contract(state: PangeaState) -> StartRoute:
    progress = load_progress(state)
    if progress is None or progress.phase != "PREPARING":
        return "advance_run"
    return {
        "CONTRACT_FROZEN": "resolve_repositories",
        "SOURCE_READY": "index_materials",
        "INDEX_READY": "build_inventory",
        "INVENTORY_READY": "make_analysis_units",
    }[progress.init_step or "CONTRACT_FROZEN"]


def route_after_advance(state: PangeaState) -> AdvanceRoute:
    return "finalize_report" if state.get("phase") == "READY_TO_FINALIZE" and "quality_report" in state else "end"
