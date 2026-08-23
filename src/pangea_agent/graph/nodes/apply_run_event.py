from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pangea_agent.agent_io import write_json
from pangea_agent.graph.run_store import (
    edit_progress,
    load_progress,
    load_review_task,
    load_worker_task,
    reviewer_unavailable_path,
)
from pangea_agent.graph.state import PangeaState
from pangea_agent.models.worker import ReviewerUnavailable


def _is_real_subagent_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    try:
        return str(UUID(candidate)) == candidate.lower()
    except ValueError:
        return False


def _record_session(state: PangeaState, event: dict) -> str:
    role = event.get("role")
    unit_id = event.get("unit_id")
    key = "review" if role == "review" else f"{role}:{unit_id or ''}"
    with edit_progress(state) as progress:
        record = progress.agent_sessions.get(key)
        if record is None:
            raise ValueError(f"当前 Run 没有待记录的 Agent 会话：{key}")
        expected_phase = {
            "source_checkpoint": "WAITING_SOURCE_CHECKPOINT",
            "risk_analysis": "WAITING_RISK_ANALYSIS",
            "test_generation": "WAITING_TEST_GENERATION",
            "independent_review": "WAITING_INDEPENDENT_REVIEW",
            "comparison_review": "WAITING_COMPARISON_REVIEW",
            "rework": "WAITING_REWORK",
            "rework_verification": "WAITING_REWORK_VERIFICATION",
        }.get(record.stage)
        if expected_phase != progress.phase:
            raise ValueError(
                f"Agent 会话不是当前 Graph 阶段：session={record.stage} progress={progress.phase}"
            )
        task_id = event.get("task_id")
        status = event.get("status", "dispatched")
        if status != "dispatched":
            raise ValueError("Agent 完成状态只能由当前 task 的提交校验记录")
        if not _is_real_subagent_id(task_id):
            raise ValueError("记录 dispatched 状态时必须提供 DSH 返回的真实 subagent UUID")
        if record.task_id and record.task_id != task_id:
            run_dir = Path(state["data_root"]) / "runs" / state["run_id"]
            task_path = run_dir / "agent-tasks" / "rework" / f"{unit_id}.json"
            analysis_record = progress.agent_sessions.get(f"analysis:{unit_id or ''}")
            replacement_allowed = (
                role == "rework"
                and progress.phase == "WAITING_REWORK"
                and analysis_record is not None
                and record.task_id == analysis_record.task_id
                and task_path.is_file()
                and load_worker_task(task_path).replacement_allowed
            )
            if not replacement_allowed:
                raise ValueError("同一 Agent 会话不能替换 task_id")
        already_completed = record.status == "completed"
        record.task_id = task_id
        if not already_completed:
            record.status = "dispatched"
        return f"{key}={record.status}"


def _mark_reviewer_unavailable(state: PangeaState, event: dict) -> str:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("指定 Run 不存在")
    if progress.phase != "WAITING_REWORK_VERIFICATION":
        raise ValueError("仅返工复核阶段可以标记原 reviewer 无法恢复")
    run_dir = Path(state["data_root"]) / "runs" / state["run_id"]
    task_path = run_dir / "agent-tasks" / "rework-review.json"
    if not task_path.is_file():
        raise ValueError("当前 Run 缺少返工复核 task")
    task = load_review_task(task_path)
    reviewer_id = event.get("reviewer_id")
    if task.same_reviewer_id != reviewer_id:
        raise ValueError("reviewer-id 不是当前 Run 绑定的原 reviewer")
    signal = ReviewerUnavailable(
        run_id=state["run_id"],
        reviewer_id=reviewer_id,
        reason=event.get("reason", ""),
    )
    write_json(reviewer_unavailable_path(state), signal.model_dump(mode="json"))
    return "UNRESOLVED"


def apply_run_event(state: PangeaState) -> PangeaState:
    event = state.get("event")
    if not isinstance(event, dict):
        raise ValueError("Graph event 必须是对象")
    event_type = event.get("type")
    if event_type == "record_agent_session":
        result = _record_session(state, event)
    elif event_type == "reviewer_unavailable":
        result = _mark_reviewer_unavailable(state, event)
    else:
        raise ValueError(f"未知 Graph event：{event_type}")
    progress = load_progress(state)
    return {**state, "phase": progress.phase if progress else "INCOMPLETE", "event_result": result}
