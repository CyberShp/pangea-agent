from __future__ import annotations

import re
from pathlib import Path

from pangea_agent.agent_io import write_json
from pangea_agent.documents.coverage import match_coverage_records
from pangea_agent.graph.run_store import analysis_result_path, analysis_task_path, run_directory, save_progress
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.validation import validate_nonoverlapping_units
from pangea_agent.models.run import AgentSession, RunProgress
from pangea_agent.models.worker import AnalysisUnit, WorkerTask


_HIGH_IMPACT_ASSERT_RE = re.compile(
    r"\bassert\s*\([^\n]*(?:\bfalse\b|(?:STAILQ|TAILQ|SLIST|LIST)_EMPTY|\bref\s*>\s*0)[^\n]*\)"
)
_ABORT_RE = re.compile(r"\babort\s*\(")
_CODE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}


def _failure_signal_focus(signal: str) -> str:
    if re.search(r"\bassert\s*\(\s*false\s*\)", signal):
        return (
            "分别重放 Debug 与 Release：Debug 会在后续清理前终止，不能用 assert 后的清理或返回"
            "排除该模式；Release 继续核对清理后的最终状态。"
        )
    if re.search(r"(?:STAILQ|TAILQ|SLIST|LIST)_EMPTY", signal):
        return (
            "追踪容器元素的产生、归还和公开移除入口；实现注释或 assert 本身不是调用方契约，"
            "只有公开契约或入口强制检查才能证明该状态不可达。"
        )
    if re.search(r"\bref\s*>\s*0", signal):
        return "核对所有增加与减少引用的路径，尤其检查增加失败后调用方是否仍会执行减少。"
    if _ABORT_RE.search(signal):
        return "反向确认公开入口和支持模式是否可达该终止点，并检查终止前已经发生的副作用。"
    return "追踪该状态的写入者、公开入口和失败后的最终状态；断言本身不能证明调用方保证。"


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


def _failure_signal_context(unit: AnalysisUnit, repositories: list[dict]) -> list[dict]:
    repository = next((item for item in repositories if item["repo_id"] == unit.repo_id), None)
    if repository is None:
        return []
    root = Path(repository["source_root"])
    signals: list[dict] = []
    for relative in sorted(dict.fromkeys([*unit.source_scope, *unit.context_scope])):
        path = root / relative
        if path.suffix.lower() not in _CODE_SUFFIXES or not path.is_file():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            match = _HIGH_IMPACT_ASSERT_RE.search(line) or _ABORT_RE.search(line)
            if match:
                signal = line.strip()
                signals.append({
                    "path": relative,
                    "line": line_number,
                    "signal": signal,
                    "analysis_focus": _failure_signal_focus(signal),
                })
    return signals


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
            failure_signal_context=_failure_signal_context(unit, unit_repositories),
            attempt=0,
            result_path=str(analysis_result_path(state, unit.unit_id, 0)),
        )
        path = analysis_task_path(state, unit.unit_id)
        write_json(path, task.model_dump(mode="json"))
        task_paths.append(str(path))
    progress = RunProgress(
        run_id=state["run_id"],
        phase="WAITING_ANALYSIS",
        analysis_units=[unit["unit_id"] for unit in units],
        agent_sessions={
            f"analysis:{unit['unit_id']}": AgentSession(
                role="analysis", unit_id=unit["unit_id"], stage="analysis"
            )
            for unit in units
        },
    )
    save_progress(state, progress)
    return {**state, "phase": progress.phase, "agent_task_paths": task_paths}
