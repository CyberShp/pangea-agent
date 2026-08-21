from __future__ import annotations

from typing import Literal

from .run_store import load_progress
from .state import PangeaState


StartRoute = Literal[
    "resolve_repositories",
    "index_materials",
    "build_inventory",
    "make_analysis_units",
    "accept_source_checkpoint",
    "accept_risk_analysis",
    "accept_test_generation",
    "accept_independent_review",
    "accept_comparison_review",
    "accept_rework",
    "accept_rework_verification",
    "resume_terminal",
    "apply_run_event",
]
AdvanceRoute = Literal["finalize_report", "end"]


def route_after_contract(state: PangeaState) -> StartRoute:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("progress.json 不存在")
    if state.get("event"):
        return "apply_run_event"
    if progress.phase != "PREPARING":
        return {
            "WAITING_SOURCE_CHECKPOINT": "accept_source_checkpoint",
            "WAITING_RISK_ANALYSIS": "accept_risk_analysis",
            "WAITING_TEST_GENERATION": "accept_test_generation",
            "WAITING_INDEPENDENT_REVIEW": "accept_independent_review",
            "WAITING_COMPARISON_REVIEW": "accept_comparison_review",
            "WAITING_REWORK": "accept_rework",
            "WAITING_REWORK_VERIFICATION": "accept_rework_verification",
            "READY_TO_FINALIZE": "resume_terminal",
            "COMPLETE": "resume_terminal",
            "INCOMPLETE": "resume_terminal",
        }[progress.phase]
    return {
        "CONTRACT_FROZEN": "resolve_repositories",
        "SOURCE_READY": "index_materials",
        "INDEX_READY": "build_inventory",
        "INVENTORY_READY": "make_analysis_units",
    }[progress.init_step or "CONTRACT_FROZEN"]


def route_after_advance(state: PangeaState) -> AdvanceRoute:
    return "finalize_report" if state.get("phase") == "READY_TO_FINALIZE" and "quality_report" in state else "end"


def route_after_stage(state: PangeaState) -> str:
    return state.get("next_node", "end")
