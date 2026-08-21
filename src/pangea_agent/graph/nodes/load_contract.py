from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.run_store import load_progress, save_progress
from pangea_agent.graph.state import PangeaState
from pangea_agent.models.contract import TaskContract
from pangea_agent.models.run import RunProgress


def load_contract(state: PangeaState) -> PangeaState:
    """Validate and freeze the task contract, then restore completed init outputs."""

    raw_contract = state.get("task_contract")
    if not isinstance(raw_contract, dict):
        raise ValueError("task_contract is required")
    contract = TaskContract.model_validate(raw_contract).model_dump(mode="json", exclude_none=True)
    run_id = state.get("run_id") or contract["run_id"]
    if not isinstance(run_id, str) or not run_id or run_id in {".", ".."} or any(char in run_id for char in "/\\"):
        raise ValueError("run_id 必须是非空文件名，且不能包含路径分隔符")
    data_root = state.get("data_root") or contract.get("data_root") or "pangea-data"
    run_dir = Path(data_root, "runs", run_id)
    base_state = {**state, "task_contract": contract, "run_id": run_id, "data_root": data_root}
    frozen_contract_path = run_dir / "inputs" / "task-contract.json"

    progress = load_progress(base_state)
    if progress is None:
        write_json(frozen_contract_path, contract)
        progress = RunProgress(
            workflow_version=2,
            run_id=run_id,
            phase="PREPARING",
            init_step="CONTRACT_FROZEN",
        )
        save_progress(base_state, progress)
    elif frozen_contract_path.is_file() and read_json(frozen_contract_path) != contract:
        raise ValueError("当前 task contract 与已有 Run 不一致，不能继续该 Run")
    elif progress.phase == "PREPARING" and not frozen_contract_path.is_file():
        # Compatibility for a Run that was interrupted before early contract freezing existed.
        write_json(frozen_contract_path, contract)

    restored = {**base_state, "phase": progress.phase}
    if progress.phase != "PREPARING":
        return restored

    init_step = progress.init_step or "CONTRACT_FROZEN"
    if init_step in {"SOURCE_READY", "INDEX_READY", "INVENTORY_READY"}:
        scope_path = run_dir / "inputs" / "scope-expansion.json"
        repositories_path = run_dir / "inputs" / "source-repositories.json"
        if not scope_path.is_file() or not repositories_path.is_file():
            raise ValueError("SOURCE_READY checkpoint 缺少冻结源码清单")
        expansion = read_json(scope_path)
        restored["scope_expansion"] = expansion
        restored["repositories"] = read_json(repositories_path)
        restored["module_scope"] = list(dict.fromkeys(
            path
            for group in expansion.get("groups", [])
            for path in group.get("code_paths", [])
        ))

    if init_step in {"INDEX_READY", "INVENTORY_READY"}:
        manifest_path = run_dir / "inputs" / "source-manifest.json"
        index_path = run_dir / "index.sqlite"
        if not manifest_path.is_file() or not index_path.is_file():
            raise ValueError("初始化 checkpoint 与索引产物不一致，不能跳过 index_materials")
        restored["source_manifest"] = read_json(manifest_path)
        restored["index_path"] = str(index_path)

    if init_step == "INVENTORY_READY":
        inventory_path = run_dir / "inputs" / "inventory.json"
        if not inventory_path.is_file():
            raise ValueError(f"初始化 checkpoint 与产物不一致：缺少 {inventory_path}")
        restored["inventory"] = read_json(inventory_path)

    return restored
