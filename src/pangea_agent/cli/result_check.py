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
        "advisory_count": 0,
        "advisories": [],
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
    return response
