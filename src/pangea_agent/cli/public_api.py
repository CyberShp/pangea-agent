from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json
from pangea_agent.documents.coverage_query import local_query_skill
from pangea_agent.execution_metrics import summarize_execution
from pangea_agent.execution_view import execution_view, plan_units
from pangea_agent.assets import (
    archive_asset,
    asset_detail,
    import_asset,
    list_assets,
    prepare_asset_extraction,
    review_asset,
    update_asset_result,
)
from pangea_agent.repositories.registry import list_registered_repositories
from pangea_agent.graph.workflow_store import load_progress, save_progress, serialized_run_mutation
from pangea_agent.methodology import (
    complete_methodology_derivation,
    import_methodology_candidates,
    list_methodology_derivations,
    list_methodologies,
    prepare_methodology_derivation,
    run_methodology_manifests,
    set_methodology_status,
    show_methodology_derivation,
    show_methodology,
)
from pangea_agent.report import reports_are_complete


def system_capabilities(data_root: str) -> dict:
    from pangea_agent.analysis_scenarios import PROFILE, scene_options
    return {
        "coverage_query_skill": local_query_skill(),
        "workflow_versions": ["legacy-v1", "source-first-v1"],
        "source_first": {
            "version": "source-first-v1",
            "contract_fields": ["analysis_settings", "runtime_provenance", "analysis_profile", "asset_revisions", "incremental_request"],
            "incremental_analysis": {"modes": ["supplement", "changed-files"], "parent_results": "reference-only", "independent_child_run": True},
            "execution_recovery": {"execution_view": "pangea-execution-view-v1", "host_quiescent_resume": True, "execution_ids": True},
            "analysis_profiles": ["behavior-test-v1", PROFILE],
            "analysis_options_by_profile": {PROFILE: scene_options()},
            "analysis_options": {"scenarios": ["module-analysis"], "modes": ["depth", "speed"], "coverage_input": False},
            "tools": [
                "source_index", "source_read", "source_search", "plan_write",
                "result_write", "result_read", "comparison_read",
                "work_finish", "review_decide",
            ],
        },
        "asset_operations": {"metadata": False, "restore": False, "revisions": False, "item_review": False, "preview": False, "list_filters": False},
        "analysis_languages": ["c_cpp", "lua"],
        "asset_types": [
            "requirement",
            "design",
            "historical_defect",
            "reference",
            "coverage",
            "test_case_example",
        ],
        "repositories": list_registered_repositories(data_root),
        "report_formats": ["html", "markdown"],
        "methodologies": {
            "schema_version": "1.0",
            "candidate_schema_path": str(
                Path(__file__).resolve().parents[3]
                / "schemas"
                / "methodology_candidate.schema.json"
            ),
            "derivation_task_schema_path": str(
                Path(__file__).resolve().parents[3]
                / "schemas"
                / "methodology_derivation_task.schema.json"
            ),
            "derivation_worker_path": str(
                Path(__file__).resolve().parents[3]
                / ".agents"
                / "pangea"
                / "methodology-worker.md"
            ),
            "statuses": ["candidate", "enabled", "disabled"],
            "derivation_statuses": ["pending", "ready", "completed"],
        },
    }


def _run_summary(run_dir: Path) -> dict:
    progress_path = run_dir / "progress.json"
    progress = read_json(progress_path) if progress_path.is_file() else {}
    phase = str(progress.get("phase") or progress.get("stage") or "UNKNOWN")
    stored_lifecycle = progress.get("lifecycle_status")
    if stored_lifecycle:
        lifecycle_status = stored_lifecycle
    elif phase == "COMPLETE":
        lifecycle_status = "complete"
    elif phase == "STOPPED":
        lifecycle_status = "stopped"
    elif phase == "INCOMPLETE":
        lifecycle_status = "complete"
    elif progress_path.is_file():
        lifecycle_status = "running"
    else:
        lifecycle_status = "failed"
    units = plan_units(run_dir, progress)
    completed = set(progress.get("completed_analysis_units", []))
    if progress.get("workflow_version") == "source-first-v1":
        # These handles are explicitly bound by Graph tasks, never inferred
        # from action names or directory scans.
        for action in progress.get("actions", {}).values():
            if action.get("role") != "analysis" or action.get("status") not in {"settled", "accepted"}:
                continue
            try:
                path = Path(action["task_path"]).resolve()
                path.relative_to(run_dir.resolve())
                unit_id = read_json(path).get("unit_id")
                if unit_id:
                    completed.add(unit_id)
            except (OSError, ValueError, KeyError):
                pass
    report_available = (
        lifecycle_status == "complete" and reports_are_complete(run_dir)
    )
    return {
        "run_id": run_dir.name,
        "workflow_version": progress.get("workflow_version"),
        "lifecycle_status": lifecycle_status,
        "phase": phase,
        "stage": progress.get("stage"),
        "quality_status": progress.get("quality_status"),
        "unit_count": len({u["unit_id"] for u in units if isinstance(u, dict) and u.get("unit_id")}) if units is not None else None,
        "completed_unit_count": len(completed),
        "errors": progress.get("errors", []),
        "needs_user": bool(progress.get("needs_user", False)),
        "blocking_reason": progress.get("blocking_reason"),
        "first_finish_revisions": progress.get("first_finish_revisions", {}),
        "accepted_revisions": progress.get("accepted_revisions", {}),
        "report_available": report_available,
    }


def list_runs(data_root: str, *, cursor: int = 0, limit: int = 50) -> dict:
    if cursor < 0:
        raise ValueError("cursor 不能小于 0")
    if limit < 1 or limit > 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    root = Path(data_root) / "runs"
    runs = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    ) if root.exists() else []
    page = runs[cursor : cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "items": [_run_summary(path) for path in page],
        "next_cursor": next_cursor if next_cursor < len(runs) else None,
        "total": len(runs),
    }


def run_detail(data_root: str, run_id: str) -> dict:
    run_dir = Path(data_root) / "runs" / run_id
    if not run_dir.is_dir():
        raise ValueError(f"Run 不存在：{run_id}")
    summary = _run_summary(run_dir)
    progress_path = run_dir / "progress.json"
    summary["progress"] = read_json(progress_path) if progress_path.is_file() else None
    summary["execution_metrics"] = summarize_execution(summary["progress"] or {})
    summary["execution_view"] = execution_view(run_dir, summary["progress"] or {})
    contract_path = run_dir / "inputs" / "task-contract.json"
    contract = read_json(contract_path) if contract_path.is_file() else {}
    summary["incremental_request"] = contract.get("incremental_request")
    if summary["incremental_request"]:
        changes_path = run_dir / "inputs" / "incremental" / "changes.json"
        summary["incremental_changes"] = read_json(changes_path) if changes_path.is_file() else None
    summary["reports"] = {
        "html": str(run_dir / "report.html") if summary["report_available"] else None,
        "markdown": str(run_dir / "report.md") if summary["report_available"] else None,
    }
    summary["methodologies"] = run_methodology_manifests(run_dir)
    return summary


def run_report(data_root: str, run_id: str, report_format: str) -> dict:
    suffix = {"html": ".html", "markdown": ".md"}.get(report_format)
    if suffix is None:
        raise ValueError("format 必须是 html 或 markdown")
    path = Path(data_root) / "runs" / run_id / f"report{suffix}"
    if not _run_summary(path.parent)["report_available"]:
        raise ValueError(f"报告不存在：{path}")
    return {"run_id": run_id, "format": report_format, "path": str(path)}


@serialized_run_mutation
def resume_run(data_root: str, run_id: str, *, host_quiescent: bool = False) -> dict:
    state = {"data_root": data_root, "run_id": run_id}
    progress = load_progress(state)
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    if progress.workflow_version != "source-first-v1":
        from .run_module_analysis import resume_module_analysis
        return resume_module_analysis(run_id, data_root)
    if progress.lifecycle_status not in {"running", "stopped"}:
        if progress.lifecycle_status == "complete" and reports_are_complete(Path(data_root) / "runs" / run_id):
            return run_detail(data_root, run_id)
        raise ValueError(f"当前 Run 不可续跑：{progress.lifecycle_status}；保留结果和诊断")
    if progress.stage == "reporting":
        from pangea_agent.graph.nodes.finalize_workflow import finalize_workflow
        return finalize_workflow({**state, "workflow_version": "source-first-v1"})
    if not host_quiescent and any(a.task_id and (a.status in {"dispatched", "paused"}
                                  or (a.status == "failed" and a.error == "用户停止 Run"))
                                  for a in progress.actions.values()):
        # A stop flag or repeated resume is not evidence that cancellation of
        # the prior Provider turn has actually completed.
        # The host must establish that fact before requeueing a live binding.
        return {**state, "workflow_version": "source-first-v1", "lifecycle_status": progress.lifecycle_status,
                "stage": progress.stage, "requires_host_quiescence": True, "agent_actions": []}
    import time
    from pangea_agent.graph.result_store import read_result
    now = int(time.time() * 1000)
    settle_ids = []
    for action in progress.actions.values():
        if action.status in {"dispatched", "paused"} or (action.status == "failed" and action.error == "用户停止 Run"):
            if action.execution_started_at_ms is not None and action.execution_finished_at_ms is None:
                action.execution_elapsed_ms += max(0, now - action.execution_started_at_ms)
                action.execution_finished_at_ms = now
            # A crash after work-finish but before settle must consume the
            # saved submission, not send another turn to the same worker.
            try:
                run_dir = (Path(data_root) / "runs" / run_id).resolve()
                task_path = Path(action.task_path).resolve()
                task_path.relative_to(run_dir)
                task = read_json(task_path)
                result_path = Path(task["result_path"]).resolve()
                result_path.relative_to(run_dir)
                result = read_result(result_path)
                finished = (action.task_id and result.binding.run_id == run_id
                            and Path(result.binding.data_root).resolve() == Path(data_root).resolve()
                            and result.binding.action_id == action.action_id and result.binding.task_id == action.task_id
                            and result.completion and result.completion.complete
                            and result.completion.declared_revision == result.revision)
            except (OSError, ValueError, KeyError):
                finished = False
            if finished:
                action.status = "dispatched"
                settle_ids.append(action.action_id)
                continue
            action.status = "pending"
            action.action = "continue_agent" if action.task_id else "dispatch_agent"
            action.attention_required = False
    progress.needs_user = False
    progress.lifecycle_status = "running"
    save_progress(state, progress)
    from .adapter_api import _settle_action
    for action_id in settle_ids:
        _settle_action(data_root, run_id, action_id)
    # Advance already-settled work under this same lock, even when a crash left
    # no worker to settle again. No semantic output is manufactured here.
    from .run_module_analysis import resume_module_analysis
    return resume_module_analysis(run_id, data_root)


@serialized_run_mutation
def stop_run(data_root: str, run_id: str) -> dict:
    state = {"data_root": data_root, "run_id": run_id}
    progress = load_progress(state)
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    if progress.lifecycle_status == "complete":
        raise ValueError("已经完成的 Run 不能停止")
    import time
    now = int(time.time() * 1000)
    progress.lifecycle_status = "stopped"
    for action in progress.actions.values():
        if action.status in {"pending", "dispatched"}:
            if action.execution_started_at_ms is not None and action.execution_finished_at_ms is None:
                action.execution_elapsed_ms += max(0, now - action.execution_started_at_ms)
                action.execution_finished_at_ms = now
            action.status = "paused"
            action.error = "用户停止 Run"
    save_progress(state, progress)
    return _run_summary(Path(data_root) / "runs" / run_id)


__all__ = [
    "archive_asset",
    "asset_detail",
    "import_asset",
    "import_methodology_candidates",
    "complete_methodology_derivation",
    "list_assets",
    "list_methodologies",
    "list_methodology_derivations",
    "list_runs",
    "prepare_asset_extraction",
    "prepare_methodology_derivation",
    "review_asset",
    "run_detail",
    "run_report",
    "set_methodology_status",
    "show_methodology_derivation",
    "show_methodology",
    "system_capabilities",
    "stop_run",
    "execution_event",
    "deliver_current",
    "update_asset_result",
]


@serialized_run_mutation
def execution_event(data_root: str, run_id: str, action_id: str, task_id: str,
                    event: str, reason: str = "", budget_ms: int | None = None, automatic: bool = False,
                    *, execution_id: str | None = None) -> dict:
    """Host execution facts only; never decide semantic quality."""
    import time
    state = {"data_root": data_root, "run_id": run_id}
    progress = load_progress(state)
    if progress is None or progress.workflow_version != "source-first-v1":
        raise ValueError("source-first Run 不存在")
    action = progress.actions.get(action_id)
    if action is None or not task_id or action.task_id != task_id:
        raise ValueError("执行事件与当前 action/task 绑定不一致")
    if execution_id and event == "started" and execution_id in action.execution_ids:
        return {**action.model_dump(mode="json"), "event_ignored": True, "reason": "duplicate_execution"}
    if execution_id and event != "started" and execution_id != action.execution_id:
        return {**action.model_dump(mode="json"), "event_ignored": True, "reason": "stale_execution"}
    if execution_id and execution_id == action.execution_id and action.status in {"settled", "accepted"}:
        return {**action.model_dump(mode="json"), "event_ignored": True, "reason": "already_settled"}
    if progress.lifecycle_status != "running" or action.status == "accepted":
        raise ValueError("当前 action 不再执行")
    now = int(time.time() * 1000)
    if event == "started":
        if action.status != "dispatched":
            raise ValueError("启动事件必须来自已绑定 action")
        if action.execution_started_at_ms is not None and action.execution_finished_at_ms is None:
            if execution_id and action.execution_id and execution_id != action.execution_id:
                raise ValueError("前一执行回合尚未结束，不能启动另一个回合")
            return {**action.model_dump(mode="json"), "event_ignored": True, "reason": "already_started"}
        action.execution_id = execution_id
        if execution_id:
            action.execution_ids.append(execution_id)
        action.execution_started_at_ms = now
        action.execution_finished_at_ms = None
        action.execution_budget_ms = budget_ms
        action.worker_turns += 1
        if automatic:
            action.auto_continuations += 1
    elif event in {"finished", "paused"}:
        if action.status == "settled":
            return {**action.model_dump(mode="json"), "event_ignored": True, "reason": "already_settled"}
        if action.execution_finished_at_ms is not None and (event == "finished" or action.status == "paused"):
            return {**action.model_dump(mode="json"), "event_ignored": True, "reason": "already_finished"}
        if action.execution_started_at_ms is not None and action.execution_finished_at_ms is None:
            action.execution_elapsed_ms += max(0, now - action.execution_started_at_ms)
        action.execution_finished_at_ms = now
        if event == "paused":
            action.status = "paused"
            action.attention_required = True
            action.error = reason or "宿主暂停，保留已保存结果"
            progress.needs_user = True
    else:
        raise ValueError("未知执行事件")
    save_progress(state, progress)
    return action.model_dump(mode="json")


@serialized_run_mutation
def deliver_current(data_root: str, run_id: str) -> dict:
    """Close an explicitly stopped correction run without claiming review success."""
    from pangea_agent.graph.nodes.finalize_workflow import finalize_workflow
    from pangea_agent.graph.result_store import read_result
    state = {"data_root": data_root, "run_id": run_id, "workflow_version": "source-first-v1"}
    progress = load_progress(state)
    if progress is None or progress.workflow_version != "source-first-v1":
        raise ValueError("仅允许交付 source-first 定向修正阶段的当前结果")
    if progress.partial_delivery and progress.stage in {"reporting", "complete"}:
        if progress.lifecycle_status == "complete" and reports_are_complete(Path(data_root) / "runs" / run_id):
            return {**state, **_run_summary(Path(data_root) / "runs" / run_id),
                    "report_path": progress.report_path, "html_report_path": progress.html_report_path}
        return finalize_workflow(state)
    if progress.stage != "closing":
        raise ValueError("仅允许交付 source-first 定向修正阶段的当前结果")
    if progress.lifecycle_status != "stopped":
        raise ValueError("须先停止执行并确认 worker 已结束，再交付当前结果")
    for action in progress.actions.values():
        if action.role != "closure" or action.status == "accepted":
            continue
        run_dir = (Path(data_root) / "runs" / run_id).resolve()
        task_file = Path(action.task_path).resolve()
        task_file.relative_to(run_dir)
        task = read_json(task_file)
        result_file = Path(task["result_path"]).resolve()
        result_file.relative_to(run_dir)
        result = read_result(result_file)
        if result.binding.run_id != run_id or result.binding.action_id != action.action_id:
            raise ValueError("closure 结果绑定不一致，保留结果等待修复")
        if result.binding.task_id not in {action.task_id, "pending"}:
            raise ValueError("closure worker 绑定不一致")
        # A pending seed is unchanged analysis, not a correction delivery.
        if result.binding.task_id == action.task_id and result.revision > task.get("base_revision", result.revision):
            action.delivery_revision = result.revision
        action.status = "paused"
        action.error = "用户结束定向修正；本单元未完成的修正不视为通过"
    progress.partial_delivery = True
    progress.stage = "reporting"
    progress.quality_status = "UNRESOLVED"
    progress.needs_user = False
    progress.degradations.append({"kind": "partial_delivery", "message": "用户结束返修并交付当前结果；未完成修正和未解决事项保留，未做新一轮复核"})
    save_progress(state, progress)
    return finalize_workflow(state)
