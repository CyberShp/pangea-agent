from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json
from pangea_agent.assets import (
    complete_asset_extraction,
    load_asset,
    load_asset_action,
    save_asset_action,
)
from pangea_agent.cli.run_module_analysis import resume_module_analysis
from pangea_agent.graph.nodes.advance_workflow import (
    _validate_comparison_review,
    _validate_review,
)
from pangea_agent.graph.planning import accept_plan
from pangea_agent.graph.result_contract import validate_unit_result
from pangea_agent.graph.workflow_store import (
    load_progress,
    pending_actions,
    run_directory,
    save_progress,
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


def _state(data_root: str, run_id: str) -> dict:
    return {"data_root": data_root, "run_id": run_id}


def _originating_analysis_action(progress, run_id: str, action: ActionState) -> ActionState | None:
    if action.role != "closure" or action.stage != "targeted_closure":
        return None
    unit_id = action.action_id.rsplit(":", 1)[-1]
    origin_id = f"{run_id}:analysis:{unit_id}"
    origin = progress.actions.get(origin_id)
    if (
        origin is None
        or origin.role != "analysis"
        or origin.stage != "unit_analysis"
        or origin.status != "accepted"
        or not origin.task_id
    ):
        raise ValueError(
            "定向补齐必须续接该单元首轮 analysis worker，"
            f"但 originating action 不可恢复：{origin_id}"
        )
    return origin


def _external_action(progress, run_id: str, action: ActionState) -> dict:
    payload = action.model_dump(mode="json")
    origin = _originating_analysis_action(progress, run_id, action)
    if origin is not None:
        payload["action"] = "continue_agent"
        payload["task_id"] = origin.task_id
    return payload


def _repair_action(progress, run_id: str, action: ActionState) -> dict:
    payload = _external_action(progress, run_id, action)
    task_id = payload.get("task_id") or action.task_id
    if not task_id:
        raise ValueError(
            f"Action 校验失败但没有可恢复的 Agent 会话：{action.action_id}"
        )
    payload["action"] = "continue_agent"
    payload["task_id"] = task_id
    return payload


def _invalid_result(
    state: dict,
    progress,
    action: ActionState,
    exc: Exception,
) -> dict:
    action.error = str(exc)
    save_progress(state, progress)
    return {
        "action_id": action.action_id,
        "status": "invalid",
        "recoverable": True,
        "error": {
            "code": exc.__class__.__name__,
            "message": str(exc),
        },
        "repair_action": _repair_action(progress, state["run_id"], action),
    }


def _external_pending_actions(progress, run_id: str, limit: int = 8) -> list[dict]:
    return [
        _external_action(progress, run_id, progress.actions[item["action_id"]])
        for item in pending_actions(progress, limit)
    ]


def _normalize_result_actions(data_root: str, run_id: str, result: dict) -> dict:
    if not result.get("agent_actions"):
        return result
    progress = load_progress(_state(data_root, run_id))
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    normalized = []
    for item in result["agent_actions"]:
        action_id = item.get("action_id")
        if not action_id or action_id not in progress.actions:
            raise ValueError(f"Workflow 返回了未持久化的 action：{action_id}")
        normalized.append(_external_action(progress, run_id, progress.actions[action_id]))
    payload = dict(result)
    payload["agent_actions"] = normalized
    return payload


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
        _external_pending_actions(progress, run_id, limit),
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
    origin = _originating_analysis_action(progress, run_id, action)
    if origin is not None and task_id != origin.task_id:
        raise ValueError(
            "定向补齐禁止新建或替换 worker："
            f"expected_task_id={origin.task_id} actual_task_id={task_id}"
        )
    if action.task_id == task_id and action.status in {"dispatched", "settled", "accepted"}:
        return _external_action(progress, run_id, action)
    if progress.lifecycle_status != "running":
        raise ValueError("Run 当前不接受新的 Agent 绑定")
    if action.status != "pending":
        raise ValueError(f"Action 当前不能绑定：status={action.status}")
    if origin is not None:
        action.action = "continue_agent"
        action.task_id = origin.task_id
    else:
        action.task_id = task_id
    action.status = "dispatched"
    save_progress(state, progress)
    return _external_action(progress, run_id, action)


def validate_action(data_root: str, run_id: str, action_id: str) -> dict:
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

    if action.role == "planning":
        task = PlanningTask.model_validate(read_json(task_path))
        compact_metadata = read_json(Path(task.compact_metadata_path))
        asset_inputs = read_json(run_directory(state) / "inputs" / "asset-items.json")
        coverage_gaps = read_json(run_directory(state) / "inputs" / "coverage-gaps.json")
        try:
            result = PlanningResult.model_validate(read_json(Path(task.result_path)))
            accept_plan(task, result, compact_metadata, asset_inputs, coverage_gaps)
        except (ValueError, FileNotFoundError) as exc:
            return _invalid_result(state, progress, action, exc)
    elif action.role == "analysis":
        task = AnalysisTask.model_validate(read_json(task_path))
        selected_inputs = read_json(Path(task.selected_inputs_path))
        try:
            result = UnitSemanticResult.model_validate(read_json(Path(task.result_path)))
            validate_unit_result(task, result, selected_inputs)
        except (ValueError, FileNotFoundError) as exc:
            return _invalid_result(state, progress, action, exc)
    elif action.role == "review":
        task = read_json(task_path)
        if task["task_type"] == "comparison_review":
            independent = IndependentReviewResult.model_validate(
                read_json(Path(task["independent_review_result_path"]))
            )
            selected_inputs = read_json(Path(task["selected_inputs_path"]))
            analysis_results = {
                unit_id: UnitSemanticResult.model_validate(read_json(Path(path)))
                for unit_id, path in task["analysis_result_paths"].items()
            }
            try:
                result = ComparisonReviewResult.model_validate(
                    read_json(Path(task["result_path"]))
                )
                _validate_comparison_review(
                    progress,
                    independent,
                    result,
                    selected_inputs,
                    analysis_results,
                )
            except (ValueError, FileNotFoundError) as exc:
                return _invalid_result(state, progress, action, exc)
        else:
            try:
                result = IndependentReviewResult.model_validate(
                    read_json(Path(task["result_path"]))
                )
                _validate_review(progress, result)
            except (ValueError, FileNotFoundError) as exc:
                return _invalid_result(state, progress, action, exc)
    elif action.role == "closure":
        task = ClosureTask.model_validate(read_json(task_path))
        original = AnalysisTask.model_validate(read_json(Path(task.original_task_path)))
        selected_inputs = read_json(Path(original.selected_inputs_path))
        try:
            result = UnitSemanticResult.model_validate(read_json(Path(task.result_path)))
            validate_unit_result(original, result, selected_inputs)
            expected = {finding.finding_key for finding in task.review_findings}
            actual = [item.finding_key for item in result.review_finding_decisions]
            if len(actual) != len(set(actual)) or set(actual) != expected:
                raise ValueError(
                    "定向补齐没有逐项处理复核发现："
                    f"missing={sorted(expected - set(actual))} "
                    f"extra={sorted(set(actual) - expected)}"
                )
        except (ValueError, FileNotFoundError) as exc:
            return _invalid_result(state, progress, action, exc)
    else:
        raise ValueError(f"Run adapter 不处理 role={action.role}")

    if action.error is not None:
        action.error = None
        save_progress(state, progress)
    return {"action_id": action_id, "status": "valid"}


def settle_action(data_root: str, run_id: str, action_id: str) -> dict:
    state = _state(data_root, run_id)
    progress = load_progress(state)
    if progress is None or action_id not in progress.actions:
        raise ValueError(f"Action 不存在：{action_id}")
    action = progress.actions[action_id]
    if action.status == "accepted":
        return next_actions(data_root, run_id)
    if action.status == "settled":
        result = resume_module_analysis(run_id, data_root)
        return _normalize_result_actions(data_root, run_id, result)
    if progress.lifecycle_status != "running":
        raise ValueError("Run 当前不接受 Agent 结果")
    if action.status != "dispatched" or not action.task_id:
        raise ValueError("Action 必须先绑定真实 Agent 会话")

    validation = validate_action(data_root, run_id, action_id)
    if validation["status"] != "valid":
        return {
            "run_id": run_id,
            "lifecycle_status": progress.lifecycle_status,
            "stage": progress.stage,
            "validation": validation,
            "agent_actions": [validation["repair_action"]],
        }

    progress = load_progress(state)
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    action = progress.actions[action_id]
    action.status = "settled"
    save_progress(state, progress)
    result = resume_module_analysis(run_id, data_root)
    return _normalize_result_actions(data_root, run_id, result)
