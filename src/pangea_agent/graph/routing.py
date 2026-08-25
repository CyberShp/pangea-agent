from __future__ import annotations

from typing import Literal

from pangea_agent.graph.state import PangeaState


OpenRoute = Literal["prepare_inputs", "advance_workflow"]
AdvanceRoute = Literal["finalize_workflow", "end"]


def route_after_open(state: PangeaState) -> OpenRoute:
    return "prepare_inputs" if state.get("needs_prepare") else "advance_workflow"


def route_after_advance(state: PangeaState) -> AdvanceRoute:
    return "finalize_workflow" if state.get("ready_to_finalize") else "end"
