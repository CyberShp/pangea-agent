from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from pangea_agent.agent_io import read_json
from pangea_agent.cli.validation_diagnostics import (
    compact_diagnostic,
    diagnostics_from_validation_error,
)
from pangea_agent.graph.analysis_normalizer import normalize_analysis_result
from pangea_agent.graph.planning import (
    accept_planning_result,
    normalize_planning_result,
)
from pangea_agent.graph.result_contract import (
    unit_submission_warnings,
    validate_closure_corrections,
)
from pangea_agent.models.analysis import (
    AnalysisTask,
    ClosureTask,
    PlanningTask,
    UnitSemanticResult,
)


def _schema_advisories(exc: ValidationError) -> list[str]:
    advisories: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        advisories.append(f"{location}: {error['msg']}")
    return advisories


def _set_schema_diagnostic(response: dict, exc: ValidationError) -> None:
    diagnostic, _ = diagnostics_from_validation_error(exc)
    response["validation_error"] = compact_diagnostic(diagnostic)
    response["advisories"] = [
        f"{group.group_key}: {group.count} errors"
        for group in diagnostic.groups
    ]


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
    if task_type == "unit_planning":
        try:
            task = PlanningTask.model_validate(task_data)
            warnings: list[str] = []
            result = normalize_planning_result(task, result_data, warnings)
            inputs_root = Path(task.compact_metadata_path).parent
            accept_planning_result(
                task,
                result,
                read_json(Path(task.compact_metadata_path)),
                read_json(inputs_root / "asset-items.json"),
                read_json(inputs_root / "coverage-gaps.json"),
                warnings,
            )
        except ValidationError as exc:
            _set_schema_diagnostic(response, exc)
            response["submission_ready"] = False
        except (OSError, ValueError) as exc:
            response["advisories"] = [str(exc)]
            response["submission_ready"] = False
        else:
            response["advisories"] = warnings
        response["advisory_count"] = len(response["advisories"])
        if response["advisory_count"]:
            response["status"] = "WARN"
            response["agent_next_step"] = (
                "当前 Agent 只需修正 submission_ready=false 的源码归属问题；"
                "可归一化提示会由 settle 记录并继续流程"
            )
        return response
    if task_type not in {"analysis", "closure"}:
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

    try:
        try:
            inventory = read_json(Path(task.inventory_path))
        except (OSError, ValueError):
            inventory = {}
        normalization_warnings: list[str] = []
        result = normalize_analysis_result(
            task,
            result_data,
            inventory,
            selected_inputs,
            normalization_warnings,
        )
    except ValidationError as exc:
        _set_schema_diagnostic(response, exc)
        response["advisory_count"] = len(response["advisories"])
        response["status"] = "WARN"
        response["submission_ready"] = False
        response["agent_next_step"] = (
            "当前 Agent 检查并修正 advisories 后重跑；"
            "这些确定性结构项会由 settle 再次校验"
        )
        return response
    except (OSError, ValueError) as exc:
        response["advisories"] = [str(exc)]
        response["advisory_count"] = 1
        response["status"] = "WARN"
        response["submission_ready"] = False
        response["agent_next_step"] = "补充可消费的分析摘要、流程和源码证据后重跑"
        return response

    response["advisories"] = normalization_warnings + unit_submission_warnings(
        task,
        result,
        selected_inputs,
        review_findings,
    )
    correction_errors: list[str] = []
    if task_type == "closure":
        try:
            original_result = UnitSemanticResult.model_validate(
                read_json(Path(closure_task.original_result_path))
            )
            correction_errors = validate_closure_corrections(
                closure_task,
                original_result,
                result,
            )
        except ValidationError as exc:
            diagnostic, _ = diagnostics_from_validation_error(exc)
            correction_errors = [
                f"{group.group_key}: {group.count} errors"
                for group in diagnostic.groups
            ]
        except (OSError, ValueError) as exc:
            correction_errors = [str(exc)]
        response["advisories"].extend(correction_errors)
    response["advisory_count"] = len(response["advisories"])
    if correction_errors:
        response["status"] = "WARN"
        response["submission_ready"] = False
        response["agent_next_step"] = (
            "按 correction target 修正同一 Closure result_path 后重跑；"
            "before/after 与 decision 集合属于确定性结构合同"
        )
        return response
    if response["advisory_count"]:
        response["status"] = "WARN"
        response["agent_next_step"] = (
            "当前 Agent 检查 advisories；确认语义结果后可以结束当前回合，"
            "settle 会保留原结果并记录降级提示"
        )
    return response
