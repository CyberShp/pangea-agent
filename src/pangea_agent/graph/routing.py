from __future__ import annotations

from typing import Literal

from .run_store import load_progress
from .state import PangeaState


StartRoute = Literal["resolve_repositories", "advance_run"]
PreparingRoute = Literal["locate_module", "index_materials", "build_inventory", "make_analysis_units"]
AdvanceRoute = Literal["finalize_report", "end"]


def route_after_contract(state: PangeaState) -> StartRoute:
    progress = load_progress(state)
    if progress is None or progress.phase == "PREPARING":
        return "resolve_repositories"
    return "advance_run"


def route_after_repositories(state: PangeaState) -> PreparingRoute:
    progress = load_progress(state)
    if progress is None or progress.phase != "PREPARING":
        raise ValueError("初始化路由缺少 PREPARING progress")
    init_step = progress.init_step or "CONTRACT_FROZEN"
    return {
        "CONTRACT_FROZEN": "locate_module",
        "SCOPE_READY": "index_materials",
        "INDEX_READY": "build_inventory",
        "INVENTORY_READY": "make_analysis_units",
    }[init_step]


def route_after_advance(state: PangeaState) -> AdvanceRoute:
    return "finalize_report" if state.get("phase") == "READY_TO_FINALIZE" and "quality_report" in state else "end"
