from __future__ import annotations

from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.scope_expander import expand_analysis_scope


def locate_module(state: PangeaState) -> PangeaState:
    """Resolve module scope from the task contract.

    First version trusts explicit source_scope. Future versions may add fuzzy
    module location, but must not scan outside registered repositories.
    """

    contract = state["task_contract"]
    scope = contract.get("source_scope") or []
    if isinstance(scope, str):
        scope = [scope]
    expansion = expand_analysis_scope(
        state.get("repositories", []),
        list(scope),
        target=str(contract.get("target", "")),
        focus=list(contract.get("focus", [])),
    )
    expanded_scope = [
        path
        for group in expansion["groups"]
        for path in group["code_paths"]
    ]
    return {
        **state,
        "module_scope": list(dict.fromkeys(expanded_scope)),
        "scope_expansion": expansion,
    }
