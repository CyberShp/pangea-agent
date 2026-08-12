from __future__ import annotations

from pathlib import Path

from pangea_agent.graph.state import PangeaState
from pangea_agent.models.contract import TaskContract


def load_contract(state: PangeaState) -> PangeaState:
    """Validate that the task contract is present in state.

    CLI entrypoints may load JSON from disk before invoking the graph. This node
    deliberately avoids a draft/confirm/activate lifecycle.
    """

    raw_contract = state.get("task_contract")
    if not isinstance(raw_contract, dict):
        raise ValueError("task_contract is required")
    contract = TaskContract.model_validate(raw_contract).model_dump(mode="json", exclude_none=True)
    run_id = state.get("run_id") or contract["run_id"]
    if not isinstance(run_id, str) or not run_id or run_id in {".", ".."} or any(char in run_id for char in "/\\"):
        raise ValueError("run_id 必须是非空文件名，且不能包含路径分隔符")
    data_root = state.get("data_root") or contract.get("data_root") or "pangea-data"
    run_dir = Path(data_root, "runs", run_id)
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    return {**state, "task_contract": contract, "run_id": run_id, "data_root": data_root}
