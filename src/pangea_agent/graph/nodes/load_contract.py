from __future__ import annotations

from pathlib import Path

from pangea_agent.graph.state import PangeaState


def load_contract(state: PangeaState) -> PangeaState:
    """Validate that the task contract is present in state.

    CLI entrypoints may load JSON from disk before invoking the graph. This node
    deliberately avoids a draft/confirm/activate lifecycle.
    """

    contract = state.get("task_contract")
    if not isinstance(contract, dict):
        raise ValueError("task_contract is required")
    run_id = state.get("run_id") or contract.get("run_id") or "RUN-local"
    data_root = state.get("data_root") or contract.get("data_root") or "pangea-data"
    Path(data_root, "runs", run_id).mkdir(parents=True, exist_ok=True)
    return {**state, "run_id": run_id, "data_root": data_root}
