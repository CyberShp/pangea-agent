from __future__ import annotations

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.workflow_store import load_progress, run_directory
from pangea_agent.models.contract import TaskContract
from pangea_agent.runtime_provenance import runtime_identity


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
    if progress is not None and progress.run_id != run_id:
        raise ValueError("Run 进度身份与当前 run_id 不一致")
    if progress is None and contract.get("incremental_request"):
        from pangea_agent.documents.incremental import _run
        parent = _run(data_root, contract["incremental_request"]["parent_run_id"])
        parent_contract = read_json(parent / "inputs" / "task-contract.json")
        if contract.get("source_investigation_policy") != parent_contract.get("source_investigation_policy"):
            raise ValueError("增量分析必须沿用父 Run 的源码调查策略")
    if progress is None and not frozen_path.is_file():
        from pangea_agent.inventory.selected_directories import validate_new_selections
        validate_new_selections(contract, data_root)
        contract["runtime_provenance"] = {**(contract.get("runtime_provenance") or {}), "agent": runtime_identity()}
        write_json(frozen_path, contract)
        return {**opened, "needs_prepare": True}
    frozen = read_json(frozen_path) if frozen_path.is_file() else None
    # The Agent identity is generated at creation, not supplied by direct CLI callers.
    # Reopening their original request keeps that frozen identity even after an upgrade.
    if frozen and not (contract.get("runtime_provenance") or {}).get("agent"):
        agent_identity = (frozen.get("runtime_provenance") or {}).get("agent")
        if agent_identity:
            contract["runtime_provenance"] = {**(contract.get("runtime_provenance") or {}), "agent": agent_identity}
    if frozen != contract:
        raise ValueError("当前 task contract 与 Run 的冻结输入不一致")
    if progress is None:
        # A caller may put a new request at the frozen-contract pathname.
        # Only an established Run with progress can retain historical scopes.
        from pangea_agent.inventory.selected_directories import validate_new_selections
        validate_new_selections(contract, data_root)
        return {**opened, "needs_prepare": True}
    return {
        **opened,
        "needs_prepare": False,
        "workflow_version": progress.workflow_version or contract.get("workflow_version"),
        "lifecycle_status": progress.lifecycle_status,
        "stage": progress.stage,
    }
