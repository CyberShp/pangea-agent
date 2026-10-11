"""Read-only execution facts for one explicitly selected Run.

Saved records and completed worker submissions are not semantic approval. Live
Provider/tool activity is supplied by the host, never inferred from file mtimes.
"""
from __future__ import annotations

import time
from pathlib import Path

from pangea_agent.agent_io import read_json
from pangea_agent.graph.result_store import active_records, read_result


def _inside(root: Path, path: str | Path) -> Path:
    candidate = Path(path).resolve()
    candidate.relative_to(root.resolve())
    return candidate


def plan_units(run_dir: Path, progress: dict) -> list[dict] | None:
    if progress.get("workflow_version") != "source-first-v1":
        return progress.get("analysis_units", [])
    path = run_dir / "inputs/source-first-plan.json"
    try:
        value = read_json(_inside(run_dir, path))
        return value["units"] if isinstance(value.get("units"), list) else None
    except (OSError, ValueError, TypeError):
        return None


def execution_view(run_dir: Path, progress: dict, *, now_ms: int | None = None) -> dict:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    lifecycle, stage = progress.get("lifecycle_status"), progress.get("stage")
    actions, preserved, unresolved, diagnostics = [], [], [], []
    last_progress = None
    partial_reporting = progress.get("partial_delivery") and stage in {"reporting", "complete"}
    for action_id, action in progress.get("actions", {}).items():
        task, result = {}, None
        try:
            task = read_json(_inside(run_dir, action["task_path"]))
            result = read_result(_inside(run_dir, task["result_path"]))
            if (result.binding.run_id != run_dir.name or result.binding.action_id != action_id
                    or Path(result.binding.data_root).resolve() != run_dir.resolve().parents[1]):
                raise ValueError("结果与当前 Run/action 身份不一致")
            pending_seed = (result.binding.task_id == "pending" and (not result.completion or not result.completion.complete) and not result.receipts
                            and ((not result.records and result.revision == 0)
                                 or (action.get("role") == "closure"
                                     and result.revision == task.get("base_revision"))))
            if result.binding.task_id != action.get("task_id") and not pending_seed:
                raise ValueError("结果与当前 worker 身份不一致")
            frozen_revision = action.get("delivery_revision")
            if frozen_revision is None:
                frozen_revision = progress.get("accepted_revisions", {}).get(action_id)
            if frozen_revision is not None and result.revision != frozen_revision:
                raise ValueError("结果与冻结修订不一致")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result = None
            diagnostics.append({"action_id": action_id, "message": str(exc)})
        status, task_id = action.get("status"), action.get("task_id")
        unit_id = task.get("unit_id")
        records = active_records(result) if result else None
        declared = bool(result and result.completion and result.completion.complete
                        and result.completion.declared_revision == result.revision)
        if lifecycle == "complete" or status == "accepted" or partial_reporting:
            operation = "none"
        elif status == "settled":
            operation = "advance_workflow"
        elif declared and task_id:
            operation = "settle_action"
        elif status in {"pending", "dispatched", "paused"} or (status == "failed" and action.get("error") == "用户停止 Run"):
            operation = "continue_agent" if task_id else "dispatch_agent"
        else:
            operation = "none"
        start, finish = action.get("execution_started_at_ms"), action.get("execution_finished_at_ms")
        current_elapsed = max(0, (finish if finish is not None else now) - start) if start is not None else None
        timed = start is not None or finish is not None or action.get("execution_elapsed_ms", 0) > 0
        saved_at = result.last_record_write_at_ms if result else None
        seed = bool(result and action.get("role") == "closure" and result.revision == task.get("base_revision")
                    and (not result.completion or not result.completion.complete) and not result.receipts)
        if type(saved_at) is not int or saved_at <= 0 or seed or (result and result.binding.task_id == "pending"):
            saved_at = None
        row = {"action_id": action_id, "task_id": task_id, "unit_id": unit_id,
               "title": task.get("title") or task.get("target"), "role": action.get("role"),
               "stage": action.get("stage"), "status": status,
               "saved_revision": result.revision if result else None,
               "saved_record_count": len(records) if records is not None else None,
               "last_saved_at_ms": saved_at, "completion_declared": declared,
               "accepted_revision": progress.get("accepted_revisions", {}).get(action_id),
               "delivery_revision": action.get("delivery_revision"), "execution_id": action.get("execution_id"),
               "elapsed_ms": action.get("execution_elapsed_ms", 0) if timed else None,
               "current_turn_elapsed_ms": current_elapsed, "budget_ms": action.get("execution_budget_ms") if action.get("execution_budget_ms") is not None else task.get("execution_budget_ms"),
               "resume_action": operation, "error": action.get("error")}
        actions.append(row)
        if type(saved_at) is int and saved_at > 0 and (last_progress is None or saved_at > last_progress["at_ms"]):
            last_progress = {"kind": "records_saved", "at_ms": saved_at, "action_id": action_id,
                             "unit_id": unit_id, "revision": result.revision, "record_count": len(records)}
        if records:
            acceptance = ("accepted" if status == "accepted" else "delivered" if action.get("delivery_revision") is not None
                          else "seed" if seed else "draft")
            preserved.append({"action_id": action_id, "unit_id": unit_id, "revision": result.revision,
                              "record_count": len(records), "acceptance": acceptance})
        if status in {"paused", "failed"} or action.get("attention_required"):
            unresolved.append({"action_id": action_id, "unit_id": unit_id, "status": status,
                               "reason": action.get("error") or "执行尚未完成，已保存结果保留"})
    units = plan_units(run_dir, progress)
    unit_ids = list(dict.fromkeys(u.get("unit_id") for u in units or [] if isinstance(u, dict) and u.get("unit_id")))
    counts = {"total": len(unit_ids) if units is not None else None,
              "completed": 0, "pending": 0, "active": 0, "paused": 0, "failed": 0}
    for unit_id in unit_ids:
        unit_actions = [a for a in actions if a["unit_id"] == unit_id and a["role"] in {"analysis", "closure"}]
        closures = [a for a in unit_actions if a["role"] == "closure"]
        action = (closures or unit_actions or [None])[-1]
        status = action["status"] if action else "pending"
        key = ("completed" if status in {"settled", "accepted"} else "active" if status == "dispatched"
               else "paused" if status == "paused" or (action and action["error"] == "用户停止 Run")
               else "failed" if status == "failed" else "pending")
        counts[key] += 1
    requires_quiescence = (lifecycle in {"running", "stopped"} and stage != "reporting"
                           and any(a["task_id"] and (a["status"] in {"dispatched", "paused"}
                                   or (a["status"] == "failed" and a["error"] == "用户停止 Run")) for a in actions))
    can_resume = lifecycle in {"running", "stopped"}
    resume_from = [{"action_id": a["action_id"], "task_id": a["task_id"], "operation": a["resume_action"]}
                   for a in actions if a["resume_action"] != "none"]
    if stage == "reporting" and lifecycle != "complete":
        resume_from = [{"action_id": None, "task_id": None, "operation": "finalize_report"}]
    return {"format_version": "pangea-execution-view-v1", "stage": stage,
            "lifecycle_status": lifecycle, "quality_status": progress.get("quality_status"),
            "unit_counts": counts, "last_effective_progress": last_progress, "actions": actions,
            "preserved": preserved, "unresolved": unresolved,
            "recovery": {"can_resume": can_resume, "requires_host_quiescence": requires_quiescence,
                         "resume_from": resume_from, "blocked_reason": None if can_resume else "Run 已结束或冻结输入异常，不能续跑"},
            "diagnostics": diagnostics}
