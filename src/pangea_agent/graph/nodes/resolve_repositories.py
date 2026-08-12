from __future__ import annotations

from pangea_agent.graph.state import PangeaState
from pangea_agent.repositories.resolver import resolve_repositories_from_contract


def resolve_repositories(state: PangeaState) -> PangeaState:
    repositories = resolve_repositories_from_contract(state["task_contract"], state["data_root"])
    return {**state, "repositories": repositories}
