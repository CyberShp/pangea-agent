from __future__ import annotations

from pathlib import Path
from typing import Literal

from .state import PangeaState


StartRoute = Literal["resolve_repositories", "advance_run"]
AdvanceRoute = Literal["finalize_report", "end"]


def route_after_contract(state: PangeaState) -> StartRoute:
    progress = Path(state["data_root"]) / "runs" / state["run_id"] / "progress.json"
    return "advance_run" if progress.exists() else "resolve_repositories"


def route_after_advance(state: PangeaState) -> AdvanceRoute:
    return "finalize_report" if state.get("phase") == "READY_TO_FINALIZE" and "quality_report" in state else "end"
