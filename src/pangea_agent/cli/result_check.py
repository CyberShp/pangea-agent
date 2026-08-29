from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from pangea_agent.agent_io import read_json
from pangea_agent.graph.result_contract import unit_submission_warnings
from pangea_agent.models.analysis import (
    AnalysisTask,
    ClosureTask,
    UnitSemanticResult,
)


def _schema_advisories(exc: ValidationError) -> list[str]:
    advisories: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        advisories.append(f"{location}: {error['msg']}")
    return advisories


def check_result_json(task_path: str) -> dict:
    """Read result JSON and report non-blocking structural advisories."""
    task_data = read_json(Path(task_path))
    result_path = task_data.get("result_path")
    if not result_path:
        raise ValueError("task 缺少 result_path")
    result_data = read_json(Path(result_path))
    response = {
        "status": "PASS",
        "check": "json_syntax_with_non_blocking_structure_advisories",
        "result_path": result_path,
        "blocking": False,
        "submission_ready": True,
        "advisory_count": 0,
        "advisories": [],
        "agent_next_step": "仅在 status=PASS 时结束当前 worker 回合",
        "state_changed": False,
    }
    task_type = task_data.get("task_type")
    if task_type not in {"analysis", "closure"}:
        return response

    try:
        result = UnitSemanticResult.model_validate(result_data)
    except ValidationError as exc:
        response["advisories"] = _schema_advisories(exc)
        response["advisory_count"] = len(response["advisories"])
        response["status"] = "WARN"
        response["submission_ready"] = False
        response["agent_next_step"] = (
            "当前 Agent 检查并修正 advisories 后重跑；"
            "这些确定性结构项会由 settle 再次校验"
        )
        return response

    if task_type == "analysis":
        task = AnalysisTask.model_validate(task_data)
        selected_inputs = read_json(Path(task.selected_inputs_path))
        review_findings = None
    else:
        closure_task = ClosureTask.model_validate(task_data)
        task = AnalysisTask.model_validate(
            read_json(Path(closure_task.original_task_path))
        )
        selected_inputs = read_json(Path(task.selected_inputs_path))
        review_findings = closure_task.review_findings

    response["advisories"] = unit_submission_warnings(
        task,
        result,
        selected_inputs,
        review_findings,
    )
    response["advisory_count"] = len(response["advisories"])
    if response["advisory_count"]:
        response["status"] = "WARN"
        response["agent_next_step"] = (
            "当前 Agent 检查 advisories；确认语义结果后可以结束当前回合，"
            "settle 会保留原结果并记录降级提示"
        )
    return response
