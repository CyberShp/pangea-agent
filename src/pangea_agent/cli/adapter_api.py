from __future__ import annotations

import json
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
from pangea_agent.cli.source_first_api import validate_source_first_result
from pangea_agent.cli.validation_diagnostics import (
    compact_diagnostic,
    diagnostics_from_contract_issues,
    diagnostics_from_validation_error,
    validation_report,
    write_validation_report,
)
from pangea_agent.graph.nodes.advance_workflow import (
    _validate_comparison_review,
    _validate_review,
)
from pangea_agent.graph.analysis_normalizer import normalize_analysis_result
from pangea_agent.graph.planning import (
    accept_planning_result,
    normalize_planning_result,
)
from pangea_agent.graph.result_contract import (
    ResultContractIssue,
    ResultContractValidationError,
    validate_closure_corrections,
    validate_unit_result,
)
from pangea_agent.graph.result_store import ResultStoreError
from pangea_agent.graph.workflow_store import (
    load_progress,
    pending_actions,
    run_directory,
    save_progress,
    serialized_run_mutation,
    validation_report_path,
    validated_result_path,
)
from pangea_agent.models.analysis import (
    ActionState,
    AnalysisTask,
    ClosureTask,
    ComparisonReviewResult,
    ComparisonReviewTask,
    IndependentReviewResult,
    IndependentReviewTask,
    PlanningResult,
    PlanningResultV2,
    PlanningTask,
    RepairRequest,
    UnitSemanticResult,
    ValidationFailureRecord,
)
from pangea_agent.graph.schema_contract import sha256_file
from pangea_agent.models.asset import AssetExtractionResult


REPEATED_REPAIR_ATTENTION_AFTER = 3
TOTAL_REPAIR_ATTENTION_AFTER = 6


def _repair_attempts(action: ActionState) -> int:
    return action.validation_failures + action.incomplete_attempts


def _last_failure_record(action: ActionState) -> ValidationFailureRecord | None:
    records = [*action.validation_history, *action.incomplete_history]
    if not records:
        return None
    return max(records, key=lambda record: record.attempt)


def _state(data_root: str, run_id: str) -> dict:
    return {"data_root": data_root, "run_id": run_id}


def _repair_action(action: ActionState) -> dict:
    if not action.task_id:
        raise ValueError(
            f"Action 校验失败但没有可恢复的 Agent 会话：{action.action_id}"
        )
    payload = action.model_dump(mode="json")
    payload["action"] = "continue_agent"
    # This is a next-action descriptor, not evidence that the continuation
    # has already been sent to the bound Agent session.
    payload["status"] = "pending"
    payload["dispatch_required"] = True
    payload["repair_dispatched"] = False
    return payload


def _task_data(action: ActionState) -> dict:
    try:
        value = read_json(Path(action.task_path))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _result_sha256(task: dict) -> str | None:
    result_path = task.get("result_path")
    if not result_path:
        return None
    try:
        return sha256_file(result_path)
    except OSError:
        return None


def _contract_card_sha256(task: dict) -> str | None:
    contract_path = task.get("result_contract_path")
    if not contract_path:
        return None
    try:
        return sha256_file(contract_path)
    except OSError:
        return None


def _quality_diagnostics_path(state: dict, action_id: str, attempt: int) -> Path:
    safe_action_id = action_id.replace(":", "__")
    return (
        run_directory(state)
        / "validation"
        / safe_action_id
        / f"quality-diagnostics-attempt-{attempt:04d}.json"
    )


def _optional_path(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _update_no_progress(
    action: ActionState,
    *,
    error_count: int,
    family_fingerprint: str | None,
    result_sha256: str | None,
    group_keys: set[str] | None = None,
) -> bool:
    previous_count = action.last_validation_detail_count
    previous_family = action.last_validation_family_fingerprint
    previous_record = _last_failure_record(action)
    previous_sha = previous_record.result_sha256 if previous_record else None
    if previous_record is None and previous_count == 0 and previous_family is None:
        progress_made = True
    else:
        previous_groups = {
            group.group_key for group in previous_record.groups
        } if previous_record else set()
        current_groups = group_keys or set()
        if family_fingerprint == previous_family and result_sha256 == previous_sha:
            progress_made = False
        else:
            progress_made = error_count < previous_count or current_groups < previous_groups
    if progress_made:
        action.consecutive_no_progress_failures = 0
    else:
        action.consecutive_no_progress_failures += 1
    action.last_validation_family_fingerprint = family_fingerprint
    action.last_validation_detail_count = error_count
    action.attention_required = (
        action.consecutive_no_progress_failures >= REPEATED_REPAIR_ATTENTION_AFTER
        or _repair_attempts(action) >= TOTAL_REPAIR_ATTENTION_AFTER
    )
    return progress_made


def _invalid_result(
    state: dict,
    progress,
    action: ActionState,
    exc: Exception,
) -> dict:
    task = _task_data(action)
    attempt = _repair_attempts(action) + 1
    result_sha256 = _result_sha256(task)
    report_path = validation_report_path(
        state,
        action.action_id,
        attempt,
    )
    validation_kind = "result_contract_validation"
    error_code = exc.__class__.__name__
    if isinstance(exc, ValidationError):
        validation_kind = "schema_validation"
        diagnostic, all_details = diagnostics_from_validation_error(
            exc,
            full_report_path=str(report_path),
        )
        message = (
            f"{diagnostic.total_error_count} schema errors in "
            f"{diagnostic.group_count} error groups for {exc.title}"
        )
    elif isinstance(exc, ResultContractValidationError):
        diagnostic, all_details = diagnostics_from_contract_issues(
            list(exc.issues),
            full_report_path=str(report_path),
        )
        message = str(exc)
    elif isinstance(exc, json.JSONDecodeError):
        validation_kind = "schema_validation"
        error_code = "InvalidJSON"
        diagnostic, all_details = diagnostics_from_contract_issues(
            [ResultContractIssue(
                family="json.invalid",
                path="$",
                message=str(exc),
                context={"line": exc.lineno, "column": exc.colno},
            )],
            full_report_path=str(report_path),
        )
        message = "InvalidJSON: 结果文件不是合法 JSON"
    else:
        diagnostic, all_details = diagnostics_from_contract_issues(
            [ResultContractIssue(
                family="result_contract.error",
                path="$",
                message=str(exc),
                context={},
            )],
            full_report_path=str(report_path),
        )
        message = str(exc)
    validation_details = [
        {
            "path": detail.path,
            "type": detail.error_type,
            "message": detail.message,
        }
        for detail in all_details
    ]
    write_validation_report(
        report_path,
        validation_report(
            diagnostic,
            all_details,
            action_id=action.action_id,
            task_id=action.task_id or "",
            attempt=attempt,
            task_path=action.task_path,
            result_path=str(task.get("result_path", "")),
            result_sha256=result_sha256,
            contract_manifest_path=task.get("result_contract_manifest_path"),
            contract_card_sha256=_contract_card_sha256(task),
            validation_kind=validation_kind,
        ),
    )
    action.validation_failures += 1
    if diagnostic and action.last_validation_family_fingerprint == diagnostic.family_fingerprint:
        action.repeated_validation_failures += 1
    else:
        action.repeated_validation_failures = 1
    action.error = message
    error = {
        "code": error_code,
        "message": message,
        "validation_kind": validation_kind,
    }
    error.update(compact_diagnostic(diagnostic))
    error["code"] = error_code
    error["message"] = message
    detail_count = diagnostic.total_error_count
    group_count = diagnostic.group_count
    family_fingerprint = diagnostic.family_fingerprint
    groups = diagnostic.groups
    report_path_value = str(report_path)
    _update_no_progress(
        action,
        error_count=detail_count,
        family_fingerprint=family_fingerprint,
        result_sha256=result_sha256,
        group_keys={group.group_key for group in groups},
    )
    action.validation_history.append(ValidationFailureRecord(
        attempt=attempt,
        code=error["code"],
        message=message,
        detail_count=detail_count,
        group_count=group_count,
        family_fingerprint=family_fingerprint,
        result_sha256=result_sha256,
        report_path=report_path_value,
        groups=groups,
        details=validation_details,
        details_truncated=False,
    ))
    action.action = "continue_agent"
    action.status = "pending"
    action.repair_status = "required"
    action.pending_repair = RepairRequest.model_validate({
        "attempt": attempt,
        "kind": validation_kind,
        "validation_report_path": report_path_value,
        "result_contract_path": _optional_path(task.get("result_contract_path")),
        "result_sha256": result_sha256,
        "error": error,
    })
    save_progress(state, progress)
    repair_action = _repair_action(action)
    repair_action["validation_error"] = error
    return {
        "action_id": action.action_id,
        "status": "invalid",
        "recoverable": True,
        "next_required_tool": "pangea_action_dispatch",
        "next_required_action_id": action.action_id,
        "repair_dispatched": False,
        "error": error,
        "validation_failures": action.validation_failures,
        "repeated_validation_failures": action.repeated_validation_failures,
        "attention_required": action.attention_required,
        "repair_action": repair_action,
    }


def _workflow_input_error(
    state: dict,
    progress,
    action: ActionState,
    exc: Exception,
) -> dict:
    """Fail a run when an immutable, Workflow-owned input is unreadable."""

    message = str(exc)
    action.status = "failed"
    action.error = message
    progress.lifecycle_status = "failed"
    progress.errors.append(
        {
            "kind": "workflow_input_invalid",
            "action_id": action.action_id,
            "reason": message,
        }
    )
    save_progress(state, progress)
    return {
        "action_id": action.action_id,
        "status": "failed",
        "recoverable": False,
        "error": {
            "code": exc.__class__.__name__,
            "message": message,
        },
    }


def _is_untouched_result_skeleton(task: dict) -> bool:
    result_path = task.get("result_path")
    skeleton_path = task.get("result_skeleton_path")
    if not result_path or not skeleton_path:
        return False
    try:
        return read_json(Path(result_path)) == read_json(Path(skeleton_path))
    except (OSError, ValueError):
        return False


def _incomplete_result(
    state: dict,
    progress,
    action: ActionState,
) -> dict:
    message = "Agent 未写入结果：result_path 仍是 Graph 创建的原始骨架"
    task = _task_data(action)
    attempt = _repair_attempts(action) + 1
    result_sha256 = _result_sha256(task)
    action.incomplete_attempts += 1
    action.error = message
    _update_no_progress(
        action,
        error_count=0,
        family_fingerprint=None,
        result_sha256=result_sha256,
    )
    record = ValidationFailureRecord(
        attempt=attempt,
        code="IncompleteAgentResult",
        message=message,
        result_sha256=result_sha256,
    )
    action.incomplete_history.append(record)
    action.action = "continue_agent"
    action.status = "pending"
    action.repair_status = "required"
    action.pending_repair = RepairRequest.model_validate({
        "attempt": attempt,
        "kind": "incomplete_result",
        "validation_report_path": None,
        "result_contract_path": _optional_path(task.get("result_contract_path")),
        "result_sha256": result_sha256,
        "error": {
            "code": record.code,
            "message": record.message,
            "result_sha256": result_sha256,
        },
    })
    save_progress(state, progress)
    repair_action = _repair_action(action)
    error = {
        "code": record.code,
        "message": record.message,
    }
    repair_action["validation_error"] = error
    return {
        "action_id": action.action_id,
        "status": "incomplete",
        "recoverable": True,
        "next_required_tool": "pangea_action_dispatch",
        "next_required_action_id": action.action_id,
        "repair_dispatched": False,
        "error": error,
        "validation_failures": action.validation_failures,
        "incomplete_attempts": action.incomplete_attempts,
        "attention_required": action.attention_required,
        "repair_action": repair_action,
    }


def _incomplete_source_first_result(
    state: dict,
    progress,
    action: ActionState,
    reason: str,
) -> dict:
    """Keep a source-first partial body while asking the same Agent to continue."""

    task = _task_data(action)
    attempt = _repair_attempts(action) + 1
    result_sha256 = _result_sha256(task)
    action.incomplete_attempts += 1
    action.error = reason
    _update_no_progress(
        action,
        error_count=0,
        family_fingerprint=None,
        result_sha256=result_sha256,
    )
    record = ValidationFailureRecord(
        attempt=attempt,
        code="IncompleteSourceFirstResult",
        message=reason,
        result_sha256=result_sha256,
    )
    action.incomplete_history.append(record)
    action.action = "continue_agent"
    action.status = "pending"
    action.repair_status = "required"
    action.pending_repair = RepairRequest.model_validate({
        "attempt": attempt,
        "kind": "incomplete_result",
        "validation_report_path": None,
        "result_contract_path": None,
        "result_sha256": result_sha256,
        "error": {
            "code": record.code,
            "message": record.message,
            "result_sha256": result_sha256,
        },
    })
    save_progress(state, progress)
    repair_action = _repair_action(action)
    repair_action["validation_error"] = {
        "code": record.code,
        "message": record.message,
    }
    return {
        "action_id": action.action_id,
        "status": "incomplete",
        "recoverable": True,
        "next_required_tool": "pangea_action_dispatch",
        "next_required_action_id": action.action_id,
        "repair_dispatched": False,
        "error": repair_action["validation_error"],
        "validation_failures": action.validation_failures,
        "incomplete_attempts": action.incomplete_attempts,
        "attention_required": action.attention_required,
        "repair_action": repair_action,
    }


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
        "data_root": str(Path(data_root).resolve()),
        "run_id": run_id,
        "workflow_version": progress.workflow_version,
        "lifecycle_status": progress.lifecycle_status,
        "stage": progress.stage,
        "actions": actions,
    }


@serialized_run_mutation
def defer_action(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    reason_code: str,
    reason: str,
    no_progress: bool = False,
    finalization_base_record_count: int | None = None,
) -> dict:
    """Keep an unfinished host turn attached to its original worker."""
    state = _state(data_root, run_id)
    progress = load_progress(state)
    if progress is None or action_id not in progress.actions:
        raise ValueError(f"Action 不存在：{action_id}")
    action = progress.actions[action_id]
    if progress.workflow_version != "source-first-v1" or progress.lifecycle_status != "running":
        raise ValueError("只有运行中的 source-first Run 可以保存宿主续接")
    if action.status != "dispatched" or not task_id or action.task_id != task_id:
        raise ValueError("宿主续接必须匹配当前 dispatched action 的原 task_id")
    if reason_code not in {"worker_error", "result_incomplete", "finalization_incomplete"}:
        raise ValueError("未知的宿主续接原因")
    if not reason.strip():
        raise ValueError("宿主续接必须提供具体原因")
    if finalization_base_record_count is not None and finalization_base_record_count < 0:
        raise ValueError("finalization_base_record_count 必须是非负整数")
    error = {
        "code": "HostWorkerIncomplete",
        "origin": "opencode_host",
        "reason_code": reason_code,
        "message": reason,
    }
    if finalization_base_record_count is not None:
        error["finalization_base_record_count"] = finalization_base_record_count
    action.action = "continue_agent"
    action.status = "pending"
    action.error = reason
    action.repair_status = "required"
    action.pending_repair = RepairRequest(
        attempt=_repair_attempts(action) + 1,
        kind="incomplete_result",
        error=error,
    )
    action.consecutive_no_progress_failures = (
        action.consecutive_no_progress_failures + 1 if no_progress else 0
    )
    action.attention_required = no_progress
    save_progress(state, progress)
    return {
        "action_id": action_id,
        "status": "incomplete",
        "recoverable": True,
        "attention_required": action.attention_required,
        "error": error,
        "repair_action": _repair_action(action),
    }


@serialized_run_mutation
def retry_attention_action(data_root: str, run_id: str, action_id: str) -> dict:
    """Requeue one exact interrupted/attention action without changing identity."""

    state = _state(data_root, run_id)
    progress = load_progress(state)
    if progress is None or action_id not in progress.actions:
        raise ValueError(f"Action 不存在：{action_id}")
    if progress.lifecycle_status != "running":
        raise ValueError("Run 当前不接受 action 续接")
    action = progress.actions[action_id]
    if action.action != "continue_agent" or not action.task_id:
        raise ValueError("只能续接已绑定原 Agent 的 continue_agent action")
    if action.status not in {"pending", "dispatched"}:
        raise ValueError(f"Action 当前不能续接：status={action.status}")
    if not action.attention_required and action.status != "dispatched":
        raise ValueError("Action 未处于 attention/interrupted 状态，无需人工续接")
    action.status = "pending"
    action.attention_required = False
    action.consecutive_no_progress_failures = 0
    save_progress(state, progress)
    payload = _repair_action(action)
    payload["validation_error"] = (
        action.pending_repair.error if action.pending_repair is not None else None
    )
    return payload


@serialized_run_mutation
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
    if action.action == "continue_agent" and action.pending_repair is not None:
        action.repair_status = "dispatched"
        action.repair_dispatches += 1
    save_progress(state, progress)
    return action.model_dump(mode="json")


def _validate_planning(
    state: dict,
    task: PlanningTask,
    result: PlanningResult | PlanningResultV2,
) -> list[str]:
    run_dir = run_directory(state)
    warnings: list[str] = []
    accept_planning_result(
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

    raw_task = read_json(task_path)
    if isinstance(raw_task, dict) and raw_task.get("workflow_version") == "source-first-v1":
        try:
            source_first = validate_source_first_result(
                data_root,
                run_id,
                action_id,
                action.task_id,
            )
        except ResultStoreError as exc:
            return _invalid_result(state, progress, action, exc)
        except (OSError, ValueError) as exc:
            return _workflow_input_error(state, progress, action, exc)
        if source_first["status"] == "incomplete":
            return _incomplete_source_first_result(
                state,
                progress,
                action,
                str(source_first.get("reason", "source-first 结果尚未完成")),
            )
        if source_first["status"] != "valid":
            return _invalid_result(
                state,
                progress,
                action,
                ValueError(str(source_first.get("reason", "source-first 结果不可消费"))),
            )
        result_warnings = [
            str(item.get("message") or item.get("kind") or item)
            for item in source_first.get("warnings", [])
            if isinstance(item, dict)
        ]
        _record_degradations(progress, action_id, result_warnings)
        progress.first_finish_revisions.setdefault(
            action_id,
            int(source_first.get("revision", 0)),
        )
        quality_diagnostics_path = _quality_diagnostics_path(
            state,
            action_id,
            _repair_attempts(action) + 1,
        )
        write_json(quality_diagnostics_path, {
            "schema_version": 1,
            "kind": "quality_diagnostics",
            "workflow_version": "source-first-v1",
            "run_id": run_id,
            "action_id": action_id,
            "task_id": action.task_id,
            "task_path": action.task_path,
            "result_path": raw_task.get("result_path"),
            "warnings": result_warnings,
            "warning_count": len(result_warnings),
        })
        action.error = None
        action.repair_status = "none"
        action.pending_repair = None
        action.consecutive_no_progress_failures = 0
        action.attention_required = False
        save_progress(state, progress)
        payload = {
            "action_id": action_id,
            "status": "valid",
            "quality_diagnostics_path": str(quality_diagnostics_path),
            "revision": source_first.get("revision", 0),
        }
        if result_warnings:
            payload["warnings"] = result_warnings
        return payload
    comparison_task = None
    independent = None
    analysis_results = None
    if (
        action.role == "review"
        and raw_task.get("task_type") == "comparison_review"
    ):
        try:
            comparison_task = ComparisonReviewTask.model_validate(raw_task)
            independent = IndependentReviewResult.model_validate(
                read_json(Path(comparison_task.independent_review_result_path))
            )
            analysis_results = {
                unit_id: UnitSemanticResult.model_validate(
                    read_json(Path(result_path))
                )
                for unit_id, result_path in comparison_task.analysis_result_paths.items()
            }
        except (OSError, ValueError) as exc:
            return _workflow_input_error(state, progress, action, exc)
    if _is_untouched_result_skeleton(raw_task):
        return _incomplete_result(state, progress, action)

    warnings: list[str] = []
    if action.role == "planning":
        task = PlanningTask.model_validate(raw_task)
        try:
            result = normalize_planning_result(
                task,
                read_json(Path(task.result_path)),
                warnings,
            )
            warnings.extend(_validate_planning(state, task, result))
        except (FileNotFoundError, ValueError) as exc:
            return _invalid_result(state, progress, action, exc)
    elif action.role == "analysis":
        task = AnalysisTask.model_validate(raw_task)
        selected_inputs = read_json(Path(task.selected_inputs_path))
        try:
            try:
                inventory = read_json(Path(task.inventory_path))
            except (OSError, ValueError):
                inventory = {}
            result = normalize_analysis_result(
                task,
                read_json(Path(task.result_path)),
                inventory,
                selected_inputs,
                warnings,
            )
        except (FileNotFoundError, ValueError) as exc:
            return _invalid_result(state, progress, action, exc)
    elif action.role == "review":
        task = raw_task
        selected_inputs = read_json(Path(task["selected_inputs_path"]))
        try:
            if task["task_type"] == "comparison_review":
                if comparison_task is None or independent is None or analysis_results is None:
                    raise ValueError("Comparison Review 冻结输入未完成预检")
                result = ComparisonReviewResult.model_validate(
                    read_json(Path(task["result_path"]))
                )
                warnings = _validate_comparison_review(
                    progress,
                    independent,
                    result,
                    selected_inputs,
                    comparison_task,
                    analysis_results,
                )
            else:
                independent_task = IndependentReviewTask.model_validate(task)
                result = IndependentReviewResult.model_validate(
                    read_json(Path(task["result_path"]))
                )
                warnings = _validate_review(
                    progress,
                    result,
                    selected_inputs,
                    {
                        item.repo_id: item.source_root
                        for item in independent_task.repositories
                    },
                )
        except (FileNotFoundError, ValueError) as exc:
            return _invalid_result(state, progress, action, exc)
    elif action.role == "closure":
        task = ClosureTask.model_validate(raw_task)
        original = AnalysisTask.model_validate(read_json(Path(task.original_task_path)))
        original_result = UnitSemanticResult.model_validate(
            read_json(Path(task.original_result_path))
        )
        selected_inputs = read_json(Path(original.selected_inputs_path))
        try:
            try:
                inventory = read_json(Path(original.inventory_path))
            except (OSError, ValueError):
                inventory = {}
            result = normalize_analysis_result(
                original,
                read_json(Path(task.result_path)),
                inventory,
                selected_inputs,
                warnings,
            )
            correction_errors = validate_closure_corrections(
                task,
                original_result,
                result,
            )
            if correction_errors:
                raise ResultContractValidationError(
                    "Closure v2 结构合同不完整",
                    [ResultContractIssue(
                        family="closure.correction",
                        path="$.correction_targets",
                        message=message,
                        context={},
                    ) for message in correction_errors],
                )
        except (FileNotFoundError, ValueError) as exc:
            return _invalid_result(state, progress, action, exc)
    else:
        raise ValueError(f"Run adapter 不处理 role={action.role}")

    quality_diagnostics_path = _quality_diagnostics_path(
        state,
        action_id,
        _repair_attempts(action) + 1,
    )
    write_json(quality_diagnostics_path, {
        "schema_version": 1,
        "kind": "quality_diagnostics",
        "run_id": run_id,
        "action_id": action_id,
        "task_id": action.task_id,
        "task_path": action.task_path,
        "result_path": str(task.get("result_path", "")),
        "warnings": warnings,
        "warning_count": len(warnings),
    })
    write_json(
        validated_result_path(state, action_id),
        result.model_dump(mode="json"),
    )
    _record_degradations(progress, action_id, warnings)
    if (
        action.error is not None
        or action.repair_status != "none"
        or action.pending_repair is not None
    ):
        action.error = None
        action.repeated_validation_failures = 0
        action.repair_status = "none"
        action.pending_repair = None
        action.consecutive_no_progress_failures = 0
        action.attention_required = False
    save_progress(state, progress)
    payload = {
        "action_id": action_id,
        "status": "valid",
        "quality_diagnostics_path": str(quality_diagnostics_path),
    }
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


@serialized_run_mutation
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
        if validation.get("recoverable") is False:
            return {
                "run_id": run_id,
                "lifecycle_status": current.lifecycle_status,
                "stage": current.stage,
                "validation": validation,
                "agent_actions": [],
            }
        payload = {
            "run_id": run_id,
            "lifecycle_status": current.lifecycle_status,
            "stage": current.stage,
            "validation": validation,
            "repair_dispatched": False,
            "attention_required": bool(validation.get("attention_required")),
        }
        payload["next_required_tool"] = "pangea_action_dispatch"
        payload["next_required_action_id"] = action_id
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
