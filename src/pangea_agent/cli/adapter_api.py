from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.assets import (
    complete_asset_extraction,
    load_asset,
    load_asset_action,
    save_asset_action,
)
from pangea_agent.cli.run_module_analysis import resume_module_analysis
from pangea_agent.graph.nodes.advance_workflow import (
    assert_comparison_review_scope,
    assert_review_scope,
    _validate_comparison_review,
    _validate_review,
)
from pangea_agent.graph.planning import accept_plan
from pangea_agent.graph.result_contract import (
    assert_unit_submission,
    validate_unit_result,
)
from pangea_agent.graph.workflow_store import (
    load_progress,
    pending_actions,
    run_directory,
    save_progress,
    validated_result_path,
)
from pangea_agent.models.analysis import (
    ActionState,
    AnalysisTask,
    ClosureTask,
    ComparisonReviewResult,
    IndependentReviewResult,
    PlanningResult,
    PlanningTask,
    UnitSemanticResult,
)
from pangea_agent.models.asset import AssetExtractionResult


REPEATED_REPAIR_ATTENTION_AFTER = 3
TOTAL_REPAIR_ATTENTION_AFTER = 6
MAX_VALIDATION_ERROR_DETAILS = 24


def _state(data_root: str, run_id: str) -> dict:
    return {"data_root": data_root, "run_id": run_id}


def _repair_action(action: ActionState) -> dict:
    if not action.task_id:
        raise ValueError(
            f"Action 校验失败但没有可恢复的 Agent 会话：{action.action_id}"
        )
    payload = action.model_dump(mode="json")
    payload["action"] = "continue_agent"
    return payload


def _invalid_result(
    state: dict,
    progress,
    action: ActionState,
    exc: Exception,
) -> dict:
    validation_details: list[dict] = []
    if isinstance(exc, ValidationError):
        all_errors = exc.errors(include_url=False)
        message = f"{len(all_errors)} schema validation errors for {exc.title}"
        validation_details = [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "type": item["type"],
                "message": item["msg"],
            }
            for item in all_errors[:MAX_VALIDATION_ERROR_DETAILS]
        ]
    else:
        message = str(exc)
    action.validation_failures += 1
    if action.error == message:
        action.repeated_validation_failures += 1
    else:
        action.repeated_validation_failures = 1
    action.error = message
    error = {
        "code": exc.__class__.__name__,
        "message": message,
    }
    if validation_details:
        error["details"] = validation_details
        error["detail_count"] = len(all_errors)
        error["details_truncated"] = len(all_errors) > len(validation_details)
    save_progress(state, progress)
    repair_action = _repair_action(action)
    repair_action["validation_error"] = error
    payload = {
        "action_id": action.action_id,
        "status": "invalid",
        "recoverable": True,
        "error": error,
        "validation_failures": action.validation_failures,
        "repeated_validation_failures": action.repeated_validation_failures,
        "attention_required": (
            action.repeated_validation_failures
            >= REPEATED_REPAIR_ATTENTION_AFTER
            or action.validation_failures >= TOTAL_REPAIR_ATTENTION_AFTER
        ),
        "repair_action": repair_action,
    }
    return payload


def _record_degradations(progress, action_id: str, warnings: list[str]) -> None:
    progress.degradations = [
        item
        for item in progress.degradations
        if item.get("action_id") != action_id
    ]
    progress.degradations.extend(
        {
            "action_id": action_id,
            "kind": "agent_result_warning",
            "message": warning,
        }
        for warning in warnings
    )


def bind_asset_action(
    data_root: str,
    asset_id: str,
    action_id: str,
    task_id: str,
) -> dict:
    if not task_id:
        raise ValueError("task_id 不能为空")
    action = load_asset_action(data_root, asset_id)
    if action.action_id != action_id:
        raise ValueError(f"Action 不属于资产 {asset_id}：{action_id}")
    if action.status == "dispatched" and action.task_id == task_id:
        return action.model_dump(mode="json")
    if action.status != "pending":
        raise ValueError(f"Action 当前不能绑定：status={action.status}")
    action.task_id = task_id
    action.status = "dispatched"
    save_asset_action(data_root, asset_id, action)
    return action.model_dump(mode="json")


def validate_asset_action(data_root: str, asset_id: str, action_id: str) -> dict:
    action = load_asset_action(data_root, asset_id)
    if action.action_id != action_id:
        raise ValueError(f"Action 不属于资产 {asset_id}：{action_id}")
    record = load_asset(data_root, asset_id)
    task = read_json(Path(action.task_path))
    result = AssetExtractionResult.model_validate(read_json(Path(task["result_path"])))
    if result.asset_id != record.asset_id:
        raise ValueError("提取结果 asset_id 与任务不一致")
    invalid_types = {
        item.item_type for item in result.items if item.item_type != record.asset_type
    }
    if invalid_types:
        raise ValueError(f"提取结果类型与资产类型不一致：{sorted(invalid_types)}")
    return {"action_id": action_id, "status": "valid"}


def settle_asset_action(data_root: str, asset_id: str, action_id: str) -> dict:
    action = load_asset_action(data_root, asset_id)
    if action.action_id != action_id:
        raise ValueError(f"Action 不属于资产 {asset_id}：{action_id}")
    if action.status == "accepted":
        return {"asset": load_asset(data_root, asset_id).model_dump(mode="json")}
    if action.status != "dispatched" or not action.task_id:
        raise ValueError("Action 必须先绑定真实 Agent 会话")
    validate_asset_action(data_root, asset_id, action_id)
    record = complete_asset_extraction(data_root, asset_id)
    action.status = "accepted"
    save_asset_action(data_root, asset_id, action)
    return {"asset": record.model_dump(mode="json")}


def next_actions(data_root: str, run_id: str, limit: int = 8) -> dict:
    if limit < 1 or limit > 8:
        raise ValueError("limit 必须在 1 到 8 之间")
    progress = load_progress(_state(data_root, run_id))
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    actions = sorted(
        pending_actions(progress, limit),
        key=lambda item: item["action_id"],
    )
    return {
        "run_id": run_id,
        "lifecycle_status": progress.lifecycle_status,
        "stage": progress.stage,
        "actions": actions,
    }


def bind_action(data_root: str, run_id: str, action_id: str, task_id: str) -> dict:
    if not task_id:
        raise ValueError("task_id 不能为空")
    state = _state(data_root, run_id)
    progress = load_progress(state)
    if progress is None or action_id not in progress.actions:
        raise ValueError(f"Action 不存在：{action_id}")
    action = progress.actions[action_id]
    if action.task_id == task_id and action.status in {
        "dispatched",
        "settled",
        "accepted",
    }:
        return action.model_dump(mode="json")
    if progress.lifecycle_status != "running":
        raise ValueError("Run 当前不接受新的 Agent 绑定")
    if action.status != "pending":
        raise ValueError(f"Action 当前不能绑定：status={action.status}")
    if action.action == "continue_agent":
        if not action.task_id:
            raise ValueError(f"continue_agent 缺少 originating task_id：{action_id}")
        if action.task_id != task_id:
            raise ValueError(
                "continue_agent 禁止替换 Agent 会话："
                f"expected_task_id={action.task_id} actual_task_id={task_id}"
            )
    else:
        action.task_id = task_id
    action.status = "dispatched"
    save_progress(state, progress)
    return action.model_dump(mode="json")


def _validate_planning(
    state: dict,
    task: PlanningTask,
    result: PlanningResult,
) -> list[str]:
    run_dir = run_directory(state)
    warnings: list[str] = []
    accept_plan(
        task,
        result,
        read_json(Path(task.compact_metadata_path)),
        read_json(run_dir / "inputs" / "asset-items.json"),
        read_json(run_dir / "inputs" / "coverage-gaps.json"),
        warnings,
    )
    return warnings


def _validate_action(data_root: str, run_id: str, action_id: str) -> dict:
    state = _state(data_root, run_id)
    progress = load_progress(state)
    if progress is None or action_id not in progress.actions:
        raise ValueError(f"Action 不存在：{action_id}")
    action = progress.actions[action_id]
    if action.status == "accepted":
        return {"action_id": action_id, "status": "valid", "already_accepted": True}
    if action.status not in {"dispatched", "settled"} or not action.task_id:
        raise ValueError(
            "Action 尚未绑定可恢复的 Agent 会话，不能校验结果："
            f"status={action.status}"
        )
    task_path = Path(action.task_path)
    if not task_path.is_file():
        raise ValueError(f"Action task 不存在：{task_path}")

    warnings: list[str] = []
    if action.role == "planning":
        task = PlanningTask.model_validate(read_json(task_path))
        try:
            result = PlanningResult.model_validate(read_json(Path(task.result_path)))
            warnings = _validate_planning(state, task, result)
        except (FileNotFoundError, ValueError) as exc:
            return _invalid_result(state, progress, action, exc)
    elif action.role == "analysis":
        task = AnalysisTask.model_validate(read_json(task_path))
        selected_inputs = read_json(Path(task.selected_inputs_path))
        try:
            result = UnitSemanticResult.model_validate(read_json(Path(task.result_path)))
            assert_unit_submission(task, result, selected_inputs)
            warnings = validate_unit_result(task, result, selected_inputs)
        except (FileNotFoundError, ValueError) as exc:
            return _invalid_result(state, progress, action, exc)
    elif action.role == "review":
        task = read_json(task_path)
        selected_inputs = read_json(Path(task["selected_inputs_path"]))
        try:
            if task["task_type"] == "comparison_review":
                result = ComparisonReviewResult.model_validate(
                    read_json(Path(task["result_path"]))
                )
                independent = IndependentReviewResult.model_validate(
                    read_json(Path(task["independent_review_result_path"]))
                )
                assert_comparison_review_scope(progress, independent, result)
                warnings = _validate_comparison_review(
                    progress,
                    independent,
                    result,
                    selected_inputs,
                )
            else:
                result = IndependentReviewResult.model_validate(
                    read_json(Path(task["result_path"]))
                )
                assert_review_scope(progress, result)
                warnings = _validate_review(progress, result)
        except (FileNotFoundError, ValueError) as exc:
            return _invalid_result(state, progress, action, exc)
    elif action.role == "closure":
        task = ClosureTask.model_validate(read_json(task_path))
        original = AnalysisTask.model_validate(read_json(Path(task.original_task_path)))
        selected_inputs = read_json(Path(original.selected_inputs_path))
        try:
            result = UnitSemanticResult.model_validate(read_json(Path(task.result_path)))
            assert_unit_submission(original, result, selected_inputs)
            warnings = validate_unit_result(original, result, selected_inputs)
            expected = {finding.finding_key for finding in task.review_findings}
            actual = [item.finding_key for item in result.review_finding_decisions]
            if len(actual) != len(set(actual)) or set(actual) != expected:
                raise ValueError(
                    "定向补齐没有逐项处理复核发现："
                    f"missing={sorted(expected - set(actual))} "
                    f"extra={sorted(set(actual) - expected)}"
                )
        except (FileNotFoundError, ValueError) as exc:
            return _invalid_result(state, progress, action, exc)
    else:
        raise ValueError(f"Run adapter 不处理 role={action.role}")

    write_json(
        validated_result_path(state, action_id),
        result.model_dump(mode="json"),
    )
    _record_degradations(progress, action_id, warnings)
    if action.error is not None:
        action.error = None
        action.repeated_validation_failures = 0
    save_progress(state, progress)
    payload = {"action_id": action_id, "status": "valid"}
    if warnings:
        payload["warnings"] = warnings
    return payload


def validate_action(data_root: str, run_id: str, action_id: str) -> dict:
    return {
        "run_id": run_id,
        "action_id": action_id,
        "status": "settle_required",
        "message": (
            "独立 validate 入口已停用；请直接调用 pangea_action_settle。"
            "settle 会在同一次提交中校验结果并推进 Workflow。"
        ),
    }


def settle_action(data_root: str, run_id: str, action_id: str) -> dict:
    state = _state(data_root, run_id)
    progress = load_progress(state)
    if progress is None or action_id not in progress.actions:
        raise ValueError(f"Action 不存在：{action_id}")
    action = progress.actions[action_id]
    if action.status == "accepted":
        return next_actions(data_root, run_id)
    if action.status == "settled":
        return resume_module_analysis(run_id, data_root)
    if progress.lifecycle_status != "running":
        raise ValueError("Run 当前不接受 Agent 结果")
    if action.status != "dispatched" or not action.task_id:
        raise ValueError("Action 必须先绑定真实 Agent 会话")

    validation = _validate_action(data_root, run_id, action_id)
    if validation["status"] != "valid":
        current = load_progress(state)
        if current is None:
            raise ValueError(f"Run 不存在：{run_id}")
        payload = {
            "run_id": run_id,
            "lifecycle_status": current.lifecycle_status,
            "stage": current.stage,
            "validation": validation,
        }
        payload["agent_actions"] = (
            [validation["repair_action"]]
            if validation.get("repair_action")
            else []
        )
        return payload

    progress = load_progress(state)
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    progress.actions[action_id].status = "settled"
    save_progress(state, progress)
    return resume_module_analysis(run_id, data_root)
