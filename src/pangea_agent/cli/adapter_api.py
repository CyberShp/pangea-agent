from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json
from pangea_agent.cli.run_module_analysis import apply_run_event, resume_module_analysis
from pangea_agent.graph.run_store import (
    analysis_task_path,
    load_progress,
    rework_task_path,
    review_task_path,
)


_STAGE_RANK = {
    "source_checkpoint": 0,
    "risk_analysis": 1,
    "test_generation": 2,
    "independent_review": 3,
    "comparison_review": 4,
    "rework": 5,
    "rework_verification": 6,
}
_PHASE_RANK = {
    "WAITING_SOURCE_CHECKPOINT": 0,
    "WAITING_RISK_ANALYSIS": 1,
    "WAITING_TEST_GENERATION": 2,
    "WAITING_INDEPENDENT_REVIEW": 3,
    "WAITING_COMPARISON_REVIEW": 4,
    "WAITING_REWORK": 5,
    "WAITING_REWORK_VERIFICATION": 6,
    "READY_TO_FINALIZE": 7,
    "COMPLETE": 8,
    "INCOMPLETE": 8,
}


def action_id(run_id: str, session_key: str, stage: str) -> str:
    return f"{run_id}|{session_key}|{stage}"


def expose_actions(result: dict) -> dict:
    payload = dict(result)
    actions = []
    for raw in result.get("agent_actions", []):
        action = dict(raw)
        action["action_id"] = action_id(
            result["run_id"], action["session_key"], action["stage"]
        )
        actions.append(action)
    payload["agent_actions"] = actions
    return payload


def _bound_action(data_root: str, run_id: str, current_action_id: str):
    state = {"data_root": data_root, "run_id": run_id}
    progress = load_progress(state)
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    matches = [
        (key, session)
        for key, session in progress.agent_sessions.items()
        if action_id(run_id, key, session.stage) == current_action_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Action 不属于当前 Run：{current_action_id}")
    return state, progress, matches[0][0], matches[0][1]


def _settlement_action(data_root: str, run_id: str, current_action_id: str):
    state = {"data_root": data_root, "run_id": run_id}
    progress = load_progress(state)
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    parts = current_action_id.split("|", 2)
    if len(parts) != 3 or parts[0] != run_id:
        raise ValueError(f"Action 不属于当前 Run：{current_action_id}")
    session_key, requested_stage = parts[1], parts[2]
    if action_id(run_id, session_key, requested_stage) != current_action_id:
        raise ValueError(f"Action 不属于当前 Run：{current_action_id}")
    session = progress.agent_sessions.get(session_key)
    stage_rank = _STAGE_RANK.get(requested_stage)
    phase_rank = _PHASE_RANK.get(progress.phase)
    if session is None or stage_rank is None or phase_rank is None:
        raise ValueError(f"Action 不属于当前 Run：{current_action_id}")
    if phase_rank == stage_rank and session.stage == requested_stage:
        return state, progress, session, False
    if phase_rank > stage_rank and session.task_id:
        return state, progress, session, True
    raise ValueError(f"Action 不属于当前 Run：{current_action_id}")


def _task_path(state: dict, session_key: str, session) -> Path:
    if session.role == "analysis":
        return analysis_task_path(state, session.unit_id, session.stage)
    if session.role == "rework":
        return rework_task_path(state, session.unit_id)
    phase = {
        "independent_review": "independent",
        "comparison_review": "initial",
        "rework_verification": "rework",
    }[session.stage]
    return review_task_path(state, phase)


def bind_action(
    data_root: str,
    run_id: str,
    current_action_id: str,
    task_id: str,
) -> dict:
    _, _, _, session = _bound_action(data_root, run_id, current_action_id)
    result = apply_run_event(run_id, data_root, {
        "type": "record_agent_session",
        "role": session.role,
        "unit_id": session.unit_id,
        "task_id": task_id,
        "status": "dispatched",
    })
    return {
        "action_id": current_action_id,
        "status": "dispatched",
        "event_result": result["event_result"],
    }


def validate_action(data_root: str, run_id: str, current_action_id: str) -> dict:
    state, progress, session_key, session = _bound_action(
        data_root, run_id, current_action_id
    )
    task_path = _task_path(state, session_key, session)
    task = read_json(task_path)
    if session.status == "completed":
        return {"action_id": current_action_id, "status": "valid"}
    errors = [
        {
            "loc": [],
            "type": item.get("kind", "validation_error"),
            "message": item.get("reason", "当前 Agent 尚未通过 task 提交检查"),
        }
        for item in progress.errors
    ] or [{
        "loc": [],
        "type": "task_not_completed",
        "message": "当前 Agent 尚未通过 validate-worker-result 或 check-review-artifact",
    }]
    return {
        "action_id": current_action_id,
        "status": "invalid",
        "result_path": task.get("result_path"),
        "expected_contract": task.get("result_schema_path"),
        "errors": errors,
    }


def settle_action(data_root: str, run_id: str, current_action_id: str) -> dict:
    _, _, session, already_advanced = _settlement_action(
        data_root, run_id, current_action_id
    )
    if already_advanced:
        return expose_actions(resume_module_analysis(run_id, data_root))
    if session.status != "completed" or not session.task_id:
        raise ValueError("Action 必须先绑定真实 Agent 会话并通过 task 提交检查")
    return expose_actions(resume_module_analysis(
        run_id,
        data_root,
        settled_task_id=session.task_id,
    ))
