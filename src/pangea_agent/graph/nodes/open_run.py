from __future__ import annotations

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.workflow_store import load_progress, run_directory
from pangea_agent.models.contract import TaskContract


def open_run(state: PangeaState) -> PangeaState:
    raw_contract = state.get("task_contract")
    if not isinstance(raw_contract, dict):
        raise ValueError("task_contract is required")
    contract = TaskContract.model_validate(raw_contract).model_dump(
        mode="json", exclude_none=True
    )
    run_id = state.get("run_id") or contract.get("run_id")
    if (
        not isinstance(run_id, str)
        or not run_id
        or run_id in {".", ".."}
        or any(char in run_id for char in "/\\")
    ):
        raise ValueError("run_id 必须是非空文件名，且不能包含路径分隔符")
    data_root = str(state.get("data_root") or contract.get("data_root") or "pangea-data")
    opened = {
        **state,
        "run_id": run_id,
        "data_root": data_root,
        "task_contract": contract,
        "workflow_version": contract.get("workflow_version"),
    }
    frozen_path = run_directory(opened) / "inputs" / "task-contract.json"
    progress = load_progress(opened)
    if progress is None:
        write_json(frozen_path, contract)
        return {**opened, "needs_prepare": True}
    if not frozen_path.is_file() or read_json(frozen_path) != contract:
        raise ValueError("当前 task contract 与 Run 的冻结输入不一致")
    return {
        **opened,
        "needs_prepare": False,
        "workflow_version": progress.workflow_version or contract.get("workflow_version"),
        "lifecycle_status": progress.lifecycle_status,
        "stage": progress.stage,
    }
