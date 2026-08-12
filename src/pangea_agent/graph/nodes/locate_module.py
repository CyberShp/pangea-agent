from __future__ import annotations

from pangea_agent.graph.state import PangeaState


def locate_module(state: PangeaState) -> PangeaState:
    """Resolve module scope from the task contract.

    First version trusts explicit source_scope. Future versions may add fuzzy
    module location, but must not scan outside registered repositories.
    """

    contract = state["task_contract"]
    scope = contract.get("source_scope") or []
    if isinstance(scope, str):
        scope = [scope]
    return {**state, "module_scope": list(scope)}
