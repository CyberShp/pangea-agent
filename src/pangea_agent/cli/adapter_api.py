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
    if progress.lifecycle_status != "running":
        raise ValueError("Run 当前不接受新的 Agent 绑定")
    action = progress.actions[action_id]
    if action.status == "dispatched" and action.task_id == task_id:
        return action.model_dump(mode="json")
    if action.status != "pending":
        raise ValueError(f"Action 当前不能绑定：status={action.status}")
    action.task_id = task_id
    action.status = "dispatched"
    save_progress(state, progress)
    return action.model_dump(mode="json")


def _validate_planning(state: dict, task: PlanningTask, result: PlanningResult) -> None:
    run_dir = run_directory(state)
    accept_plan(
        task,
        result,
        read_json(Path(task.compact_metadata_path)),
        read_json(run_dir / "inputs" / "asset-items.json"),
        read_json(run_dir / "inputs" / "coverage-gaps.json"),
    )


def validate_action(data_root: str, run_id: str, action_id: str) -> dict:
    state = _state(data_root, run_id)
    progress = load_progress(state)
    if progress is None or action_id not in progress.actions:
        raise ValueError(f"Action 不存在：{action_id}")
    action = progress.actions[action_id]
    task_path = Path(action.task_path)
    if action.role == "planning":
        task = PlanningTask.model_validate(read_json(task_path))
        result = PlanningResult.model_validate(read_json(Path(task.result_path)))
        _validate_planning(state, task, result)
    elif action.role == "analysis":
        task = AnalysisTask.model_validate(read_json(task_path))
        result = UnitSemanticResult.model_validate(read_json(Path(task.result_path)))
        validate_unit_result(task, result, read_json(Path(task.selected_inputs_path)))
    elif action.role == "review":
        task = read_json(task_path)
        if task["task_type"] == "comparison_review":
            result = ComparisonReviewResult.model_validate(
                read_json(Path(task["result_path"]))
            )
            independent = IndependentReviewResult.model_validate(
                read_json(Path(task["independent_review_result_path"]))
            )
            _validate_comparison_review(
                progress,
                independent,
                result,
                read_json(Path(task["selected_inputs_path"])),
                {
                    unit_id: UnitSemanticResult.model_validate(read_json(Path(path)))
                    for unit_id, path in task["analysis_result_paths"].items()
                },
            )
        else:
            result = IndependentReviewResult.model_validate(
                read_json(Path(task["result_path"]))
            )
            _validate_review(progress, result)
    elif action.role == "closure":
        task = ClosureTask.model_validate(read_json(task_path))
        original = AnalysisTask.model_validate(read_json(Path(task.original_task_path)))
        result = UnitSemanticResult.model_validate(read_json(Path(task.result_path)))
        validate_unit_result(original, result, read_json(Path(original.selected_inputs_path)))
        expected = {finding.finding_key for finding in task.review_findings}
        actual = [item.finding_key for item in result.review_finding_decisions]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError(
                "定向补齐没有逐项处理复核发现："
                f"missing={sorted(expected - set(actual))} extra={sorted(set(actual) - expected)}"
            )
    else:
        raise ValueError(f"Run adapter 不处理 role={action.role}")
    return {"action_id": action_id, "status": "valid"}


def settle_action(data_root: str, run_id: str, action_id: str) -> dict:
    state = _state(data_root, run_id)
    progress = load_progress(state)
    if progress is None or action_id not in progress.actions:
        raise ValueError(f"Action 不存在：{action_id}")
    action = progress.actions[action_id]
    if action.status == "accepted":
        return next_actions(data_root, run_id)
    if progress.lifecycle_status != "running":
        raise ValueError("Run 当前不接受 Agent 结果")
    if action.status != "dispatched" or not action.task_id:
        raise ValueError("Action 必须先绑定真实 Agent 会话")
    validate_action(data_root, run_id, action_id)
    action.status = "settled"
    save_progress(state, progress)
    return resume_module_analysis(run_id, data_root)
