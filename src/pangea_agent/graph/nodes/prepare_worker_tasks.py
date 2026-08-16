from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import canonical_digest, write_json
from pangea_agent.documents.coverage import match_coverage_records
from pangea_agent.graph.run_store import analysis_result_path, analysis_task_path, run_directory, save_progress, worker_task_digest
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.validation import validate_nonoverlapping_units
from pangea_agent.models.run import RunProgress
from pangea_agent.models.worker import AnalysisUnit, WorkerTask


def _coverage_context(unit: AnalysisUnit, coverage_report: dict) -> list[dict]:
    scopes = [scope.replace("\\", "/").strip("/") or "." for scope in unit.source_scope]
    context: list[dict] = []
    for record in coverage_report.get("matched", []):
        match = record["matches"][0]
        path = match["path"].replace("\\", "/")
        if match["repo_id"] != unit.repo_id:
            continue
        if not any(scope == "." or path == scope or path.startswith(f"{scope}/") for scope in scopes):
            continue
        context.append({
            "repo_id": match["repo_id"],
            "path": path,
            "function": record["function"],
            "count": record["count"],
            "line": match.get("line"),
            "module": record.get("module", ""),
            "coverage_type": record.get("coverage_type", "function"),
            "branch_id": record.get("branch_id"),
            "condition": record.get("condition"),
            "true_count": record.get("true_count"),
            "false_count": record.get("false_count"),
        })
    return sorted(
        context,
        key=lambda item: (
            item["path"], item["line"] or 0, item["function"], item.get("branch_id") or ""
        ),
    )


def prepare_worker_tasks(state: PangeaState) -> PangeaState:
    units = state.get("analysis_units", [])
    if not units:
        raise ValueError("未发现对应 C/C++ 实现：用户指定范围没有可分析源码")
    inventory = state.get("inventory", {})
    inventory_files = {
        (item.get("repo_id"), item.get("path", "").replace("\\", "/"))
        for item in inventory.get("files", [])
    }
    empty_units = []
    for unit in units:
        scopes = [scope.replace("\\", "/").strip("/") or "." for scope in unit["source_scope"]]
        if not any(
            repo_id == unit["repo_id"] and any(
                scope == "." or path == scope or path.startswith(f"{scope}/") for scope in scopes
            )
            for repo_id, path in inventory_files
        ):
            empty_units.append(unit["unit_id"])
    if empty_units:
        raise ValueError(f"未发现对应 C/C++ 实现：{', '.join(empty_units)}")
    validate_nonoverlapping_units(units)
    run_dir = run_directory(state)
    contract_digest = canonical_digest(state["task_contract"])
    inventory_path = run_dir / "inputs" / "inventory.json"
    source_manifest_path = run_dir / "inputs" / "source-manifest.json"
    source_manifest = state.get("source_manifest", {})
    coverage_report = match_coverage_records(source_manifest.get("coverage_records", []), inventory)
    write_json(run_dir / "inputs" / "task-contract.json", state["task_contract"])
    write_json(source_manifest_path, source_manifest)
    write_json(inventory_path, inventory)
    repositories = [
        {"repo_id": repo["repo_id"], "source_root": repo["source_root"], "git": repo.get("git", {})}
        for repo in state.get("repositories", [])
    ]
    missing_inputs = [
        str(path) for path in (inventory_path, source_manifest_path, Path(state["index_path"]))
        if not path.is_file()
    ]
    if missing_inputs:
        raise ValueError(f"worker 冻结输入不存在：{missing_inputs}")
    task_paths: list[str] = []
    for raw_unit in units:
        unit = AnalysisUnit.model_validate(raw_unit)
        unit_repositories = [repo for repo in repositories if repo["repo_id"] == unit.repo_id]
        task = WorkerTask(
            task_type="analysis",
            run_id=state["run_id"],
            target=state["task_contract"]["target"],
            unit=unit,
            repositories=unit_repositories,
            index_path=state["index_path"],
            inventory_path=str(inventory_path),
            source_manifest_path=str(source_manifest_path),
            coverage_context=_coverage_context(unit, coverage_report),
            contract_digest=contract_digest,
            attempt=0,
            input_digest="0" * 64,
            result_path=str(analysis_result_path(state, unit.unit_id, 0)),
        )
        task.input_digest = worker_task_digest(task)
        path = analysis_task_path(state, unit.unit_id)
        write_json(path, task.model_dump(mode="json"))
        task_paths.append(str(path))
    progress = RunProgress(
        run_id=state["run_id"],
        contract_digest=contract_digest,
        phase="WAITING_ANALYSIS",
        analysis_units=[unit["unit_id"] for unit in units],
    )
    save_progress(state, progress)
    return {**state, "phase": progress.phase, "agent_task_paths": task_paths}
