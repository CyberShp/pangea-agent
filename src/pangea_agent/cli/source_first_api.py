"""Client-facing source-first APIs.

These functions are intentionally thin wrappers around the frozen-source and
result stores.  The host supplies the exact task identity; this module never
searches another Run or guesses a replacement task.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json
from pangea_agent.graph.result_store import (
    ResultStoreError,
    active_records,
    append_records,
    compact_items_page,
    declare_completion,
    initialize_result,
    read_records,
    read_records_compact,
    read_result,
    repair_result_shell,
    supersession_map,
)
from pangea_agent.inventory.source_access import (
    input_read as read_input,
    resolve_binding,
    source_index as read_source_index,
    source_read as read_source,
    source_search as search_source,
    task_open as open_task,
)
from pangea_agent.graph.workflow_store import (
    load_progress,
    save_progress,
    serialized_run_mutation,
    source_first_version_set_path,
)
from pangea_agent.models.source_first import ReviewDecision, SourceBinding


def _binding_and_result(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    writable: bool = False,
) -> tuple[SourceBinding, Path, dict[str, Any]]:
    binding, run_dir, action, task = resolve_binding(data_root, run_id, action_id, task_id)
    if writable and action.get("status") not in {"dispatched", "settled"}:
        raise ResultStoreError(
            f"Action 已接受或当前不可修改：status={action.get('status')!r}"
        )
    result_path = task.get("result_path")
    if not isinstance(result_path, str) or not result_path:
        raise ResultStoreError("当前 task 没有 Graph 创建的 result_path")
    path = Path(result_path).resolve()
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise ResultStoreError("result_path 越出当前 Run 数据边界") from exc
    if not path.is_file():
        raise ResultStoreError(f"Graph 创建的 result_path 不存在：{path}")
    return binding, path, task


def _effective_plan_units(result) -> list[dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in active_records(result):
        if record.kind != "unit_plan" or not isinstance(record.body, dict):
            continue
        unit_id = record.body.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            continue
        if unit_id not in units:
            order.append(unit_id)
        units[unit_id] = record.body
    return [units[unit_id] for unit_id in order]


def _region_id(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and isinstance(value.get("region_id"), str):
        return value["region_id"]
    return None


def _plan_diagnostics(run_dir: Path, task: dict[str, Any], result) -> dict[str, Any]:
    index = read_json(run_dir / "inputs" / "source-index.json")
    files = index.get("files", []) if isinstance(index, dict) else []
    all_regions = {
        str(region["region_id"]): region
        for file in files
        if isinstance(file, dict)
        for region in file.get("regions", [])
        if isinstance(region, dict) and region.get("region_id")
    }
    owned_paths = {
        (str(item.get("repo_id")), str(item.get("path")).replace("\\", "/"))
        for item in task.get("owned_scope_paths", [])
        if isinstance(item, dict) and item.get("repo_id") and item.get("path")
    }
    required = {
        region_id
        for region_id, region in all_regions.items()
        if (str(region.get("repo_id")), str(region.get("path")).replace("\\", "/")) in owned_paths
        and region.get("kind") in {"function", "global"}
    }
    if not required:
        required = {
            region_id
            for region_id, region in all_regions.items()
            if (str(region.get("repo_id")), str(region.get("path")).replace("\\", "/")) in owned_paths
            and region.get("kind") != "branch"
        }
    owners: dict[str, list[str]] = {}
    unknown: list[dict[str, str]] = []
    units = _effective_plan_units(result)
    for unit in units:
        unit_id = str(unit["unit_id"])
        for key in ("owned_regions", "context_regions"):
            values = unit.get(key, [])
            for value in values if isinstance(values, list) else []:
                region_id = _region_id(value)
                if region_id is None or region_id not in all_regions:
                    unknown.append({"unit_id": unit_id, "field": key, "region_id": str(region_id)})
                elif key == "owned_regions":
                    owners.setdefault(region_id, []).append(unit_id)
    duplicate = [
        {"region_id": region_id, "unit_ids": unit_ids}
        for region_id, unit_ids in sorted(owners.items())
        if len(unit_ids) > 1
    ]
    return {
        "unit_count": len(units),
        "required_owned_region_count": len(required),
        "unknown_references": unknown,
        "duplicate_owned_regions": duplicate,
        "unassigned_owned_regions": sorted(required - set(owners)),
        "ready": bool(units) and not unknown and not duplicate and required <= set(owners),
    }


def _comparison_diagnostics(run_dir: Path, task: dict[str, Any], result) -> list[str]:
    records = active_records(result)
    decisions = [
        record for record in records
        if record.kind == "review_decision" and isinstance(record.body, dict)
    ]
    if not decisions:
        return ["comparison Reviewer 尚未提交 review_decision"]
    decision = decisions[-1].body
    version_set_id = task.get("version_set_id")
    errors: list[str] = []
    if decision.get("version_set_id") != version_set_id:
        errors.append("review_decision.version_set_id 与当前 comparison task 不一致")
    version_set = read_json(source_first_version_set_path({
        "data_root": str(run_dir.parents[1]),
        "run_id": run_dir.name,
    }))
    for entry in version_set.get("entries", []):
        path = entry.get("result_path")
        if not isinstance(path, str) or not Path(path).is_file():
            errors.append(f"comparison 审阅结果不存在：{entry.get('action_id')}")
            continue
        if read_result(path).revision != entry.get("revision"):
            errors.append(f"comparison 审阅版本已变化：{entry.get('action_id')}")
    return errors


def validate_source_first_result(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Validate only the consumability of a source-first result shell.

    A non-empty body is preserved for the Reviewer even when its prose is
    vague.  The only blocking checks here are identity, readable JSON, and an
    explicit completion declaration.
    """

    binding, path, task = _binding_and_result(data_root, run_id, action_id, task_id)
    result = read_result(path)
    # ``read_records`` performs the binding check without exposing private
    # store internals; it also confirms the shell can be consumed as notes.
    view = read_records(path, binding, cursor=0, limit=1)
    records = active_records(result)
    warnings = list(view.get("warnings", []))
    if not records:
        return {
            "status": "incomplete",
            "reason": "result_path 没有当前有效 records，空结果不能作为完成交付",
            "revision": result.revision,
            "warnings": warnings,
        }
    if result.completion is None:
        return {
            "status": "incomplete",
            "reason": "正文已保存但 Agent 尚未提交 work_finish 完成声明",
            "revision": result.revision,
            "warnings": warnings,
        }
    if not result.completion.complete:
        return {
            "status": "incomplete",
            "reason": "Agent 明确声明当前结果未完成",
            "revision": result.revision,
            "warnings": warnings,
        }
    if result.completion.declared_revision != result.revision:
        return {
            "status": "invalid",
            "reason": (
                "completion.declared_revision 与当前 result revision 不一致："
                f"declared={result.completion.declared_revision} current={result.revision}"
            ),
            "revision": result.revision,
            "warnings": warnings,
        }
    if task.get("task_type") == "source_first_plan":
        diagnostics = _plan_diagnostics(path.parents[2], task, result)
        if not diagnostics["ready"]:
            return {
                "status": "incomplete",
                "reason": "Planning 需要原 Planner 局部更正：" + json.dumps(diagnostics, ensure_ascii=False),
                "revision": result.revision,
                "warnings": warnings,
                "diagnostics": diagnostics,
            }
    if task.get("review_stage") == "comparison_review":
        errors = _comparison_diagnostics(path.parents[2], task, result)
        if errors:
            return {
                "status": "incomplete",
                "reason": "Comparison 需要同一 Reviewer 更正：" + "; ".join(errors),
                "revision": result.revision,
                "warnings": warnings,
            }
        from pangea_agent.graph.nodes.source_first import review_correction_routes

        progress = load_progress({"data_root": data_root, "run_id": run_id})
        known_units = {
            action.action_id.rsplit(":", 1)[-1]
            for action in progress.actions.values()
            if action.role == "analysis"
        } if progress else set()
        _disposition, _routes, routing_warnings = review_correction_routes(result, known_units)
        warnings.extend({
            "kind": "source_first_correction_routing",
            "message": message,
        } for message in routing_warnings)
    if task.get("task_type") == "source_first_closure":
        base_revision = task.get("base_revision")
        changed = any(
            record.created_revision > base_revision
            for record in result.records
        ) if isinstance(base_revision, int) else False
        if not changed:
            return {
                "status": "incomplete",
                "reason": "targeted closure 尚未写入本轮 correction record",
                "revision": result.revision,
                "warnings": warnings,
            }
    return {
        "status": "valid",
        "revision": result.revision,
        "record_count": len(records),
        "superseded_record_count": len(result.records) - len(records),
        "warnings": warnings,
    }


def task_open(data_root: str, run_id: str, action_id: str, task_id: str) -> dict[str, Any]:
    return open_task(data_root, run_id, action_id, task_id)


def input_read(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    input_id: str,
    cursor: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    return read_input(
        data_root,
        run_id,
        action_id,
        task_id,
        input_id=input_id,
        cursor=cursor,
        max_chars=max_chars,
    )


def source_index(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    repo_id: str | None = None,
    path: str | None = None,
    cursor: str | None = None,
    page_size: int = 50,
    view: str = "legacy",
    page_token: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    return read_source_index(
        data_root,
        run_id,
        action_id,
        task_id,
        repo_id=repo_id,
        path=path,
        cursor=cursor,
        page_size=page_size,
        view=view,
        page_token=page_token,
        max_chars=max_chars,
    )


def source_read(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    repo_id: str,
    path: str | None = None,
    region_id: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    cursor: str | None = None,
    max_lines: int = 400,
    view: str = "legacy",
    page_token: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    return read_source(
        data_root,
        run_id,
        action_id,
        task_id,
        repo_id=repo_id,
        path=path,
        region_id=region_id,
        line_start=line_start,
        line_end=line_end,
        cursor=cursor,
        max_lines=max_lines,
        view=view,
        page_token=page_token,
        max_chars=max_chars,
    )


def source_search(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    query: str,
    repo_id: str | None = None,
    path: str | None = None,
    cursor: str | None = None,
    page_size: int = 100,
    view: str = "legacy",
    page_token: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    return search_source(
        data_root,
        run_id,
        action_id,
        task_id,
        query=query,
        repo_id=repo_id,
        path=path,
        cursor=cursor,
        page_size=page_size,
        view=view,
        page_token=page_token,
        max_chars=max_chars,
    )


@serialized_run_mutation
def result_write(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    expected_revision: int,
    records: list[dict[str, Any]],
    request_id: str | None = None,
) -> dict[str, Any]:
    if any(
        isinstance(record, dict) and record.get("kind") == "review_decision"
        for record in records
    ):
        raise ResultStoreError("review_decision 只能通过 review_decide 写入")
    binding, path, task = _binding_and_result(
        data_root, run_id, action_id, task_id, writable=True
    )
    if task.get("review_stage") == "comparison_review" and any(
        isinstance(record, dict) and record.get("kind") == "finding"
        for record in records
    ):
        raise ResultStoreError(
            "comparison finding 只能通过 comparison_finding_write 写入"
        )
    return append_records(
        path,
        binding,
        expected_revision,
        records,
        request_id=request_id,
    )


@serialized_run_mutation
def comparison_finding_write(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    expected_revision: int,
    unit_ids: list[str],
    finding: dict[str, Any],
    replace_finding_record_ids: list[str] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Append a comparison finding with an unambiguous unit route."""

    if (
        not isinstance(unit_ids, list)
        or not unit_ids
        or any(not isinstance(item, str) or not item for item in unit_ids)
    ):
        raise ResultStoreError("comparison_finding_write unit_ids 必须是非空 unit_id 数组")
    if len(set(unit_ids)) != len(unit_ids):
        raise ResultStoreError("comparison_finding_write unit_ids 不得重复")
    if not isinstance(finding, dict) or not isinstance(finding.get("summary"), str):
        raise ResultStoreError("comparison_finding_write finding.summary 必须是字符串")
    if not isinstance(finding.get("body"), str) or not finding["body"].strip():
        raise ResultStoreError("comparison_finding_write finding.body 必须是非空字符串")
    forbidden = {"kind", "relates_to", "supersedes"} & set(finding)
    if forbidden:
        raise ResultStoreError(
            "comparison_finding_write finding 不接受路由字段："
            f"{sorted(forbidden)}"
        )

    binding, path, task = _binding_and_result(
        data_root, run_id, action_id, task_id, writable=True
    )
    if task.get("review_stage") != "comparison_review":
        raise ResultStoreError("comparison_finding_write 只允许 comparison_review action")
    version_set_path = task.get("version_set_path")
    if not isinstance(version_set_path, str) or not version_set_path:
        version_set_path = str(source_first_version_set_path({
            "data_root": str(Path(data_root).resolve()),
            "run_id": run_id,
        }))
    version_set = read_json(Path(version_set_path))
    valid_unit_ids = {
        str(entry["unit_id"])
        for entry in version_set.get("entries", [])
        if isinstance(entry, dict) and isinstance(entry.get("unit_id"), str)
    }
    unknown = sorted(set(unit_ids) - valid_unit_ids)
    if unknown:
        raise ResultStoreError(
            "comparison_finding_write 只能引用 version set 中的 unit_id："
            f"未知={unknown}"
        )

    targets = replace_finding_record_ids or []
    if not isinstance(targets, list) or any(
        not isinstance(item, str) or not item for item in targets
    ):
        raise ResultStoreError("replace_finding_record_ids 必须是 record_id 数组")
    if len(set(targets)) != len(targets):
        raise ResultStoreError("replace_finding_record_ids 不得重复")
    current = read_result(path)
    active_by_id = {record.record_id: record for record in active_records(current)}
    invalid_targets = sorted(
        target for target in targets
        if target not in active_by_id or active_by_id[target].kind != "finding"
    )
    if invalid_targets:
        raise ResultStoreError(
            "replace_finding_record_ids 只能引用当前 active finding："
            f"不可用={invalid_targets}"
        )

    record: dict[str, Any] = {
        "kind": "finding",
        "body": finding,
        "relates_to": list(unit_ids),
        "supersedes": targets,
    }
    if isinstance(finding.get("evidence"), list):
        record["evidence"] = finding["evidence"]
    return append_records(
        path,
        binding,
        expected_revision,
        [record],
        request_id=request_id,
    )


@serialized_run_mutation
def result_supersede(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    expected_revision: int,
    target_record_ids: list[str],
    replacement: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Append one replacement whose retirement links cannot be hidden in prose."""

    if (
        not isinstance(target_record_ids, list)
        or not target_record_ids
        or any(not isinstance(item, str) or not item for item in target_record_ids)
    ):
        raise ResultStoreError("result_supersede target_record_ids 必须是非空 record_id 数组")
    if len(set(target_record_ids)) != len(target_record_ids):
        raise ResultStoreError("result_supersede target_record_ids 不得重复")
    if not isinstance(replacement, dict) or "body" not in replacement:
        raise ResultStoreError("result_supersede replacement 必须是包含 body 的 JSON object")
    if replacement.get("kind") == "review_decision":
        raise ResultStoreError(
            "review_decision 只能通过 review_decide 的 replace_decision_record_ids 替换"
        )
    if "supersedes" in replacement:
        raise ResultStoreError("replacement 不接受 supersedes；请只使用 target_record_ids")

    binding, path, task = _binding_and_result(
        data_root, run_id, action_id, task_id, writable=True
    )
    if (
        task.get("review_stage") == "comparison_review"
        and replacement.get("kind") == "finding"
    ):
        raise ResultStoreError(
            "comparison finding 只能通过 comparison_finding_write 的 "
            "replace_finding_record_ids 替换"
        )
    current = read_result(path)
    active_ids = {record.record_id for record in active_records(current)}
    unavailable = sorted(set(target_record_ids) - active_ids)
    if unavailable:
        raise ResultStoreError(
            "result_supersede 只能退休当前 active 的精确 record_id："
            f"不可用={unavailable}"
        )
    record = {**replacement, "supersedes": list(target_record_ids)}
    return append_records(
        path,
        binding,
        expected_revision,
        [record],
        request_id=request_id,
    )


@serialized_run_mutation
def result_repair(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    records: list[dict[str, Any]],
    expected_sha256: str,
) -> dict[str, Any]:
    binding, path, _ = _binding_and_result(
        data_root, run_id, action_id, task_id, writable=True
    )
    return repair_result_shell(
        path,
        binding,
        records,
        expected_sha256=expected_sha256,
    )


def result_read(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    record_id: str | None = None,
    cursor: int = 0,
    limit: int = 100,
    view: str = "legacy",
    page_token: str | None = None,
    max_chars: int = 12_000,
    include_history: bool = False,
) -> dict[str, Any]:
    binding, path, _ = _binding_and_result(data_root, run_id, action_id, task_id)
    if view == "compact":
        return read_records_compact(
            path,
            binding,
            record_id=record_id,
            include_history=include_history,
            page_token=page_token,
            max_chars=max_chars,
        )
    if view != "legacy":
        raise ResultStoreError(f"未知 result_read view：{view}")
    return read_records(
        path,
        binding,
        record_id=record_id,
        cursor=cursor,
        limit=limit,
    )


def comparison_read(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    version_set_id: str,
    unit_id: str | None = None,
    cursor: int = 0,
    limit: int = 100,
    view: str = "legacy",
    page_token: str | None = None,
    max_chars: int = 12_000,
    include_history: bool = False,
) -> dict[str, Any]:
    """Read only the Graph-frozen versions exposed to comparison review.

    The caller supplies an opaque id from the comparison task.  It cannot
    provide arbitrary result paths or select a different Run/action.
    """

    comparison_binding, run_dir, _task_action, task = resolve_binding(
        data_root, run_id, action_id, task_id
    )
    if task.get("review_stage") != "comparison_review":
        raise ResultStoreError("comparison_read 仅允许 comparison_review task")
    if not isinstance(version_set_id, str) or not version_set_id:
        raise ResultStoreError("comparison_read 需要 version_set_id")
    if task.get("version_set_id") != version_set_id:
        raise ResultStoreError("version_set_id 与当前 comparison task 不一致")
    path = source_first_version_set_path(
        {"data_root": data_root, "run_id": run_id}
    ).resolve()
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise ResultStoreError("comparison version set 越出当前 Run 数据边界") from exc
    try:
        version_set = read_json(path)
    except (OSError, ValueError) as exc:
        raise ResultStoreError(f"comparison version set 不可读取：{path}") from exc
    if (
        not isinstance(version_set, dict)
        or version_set.get("format_version") != "pangea-version-set-v1"
        or version_set.get("version_set_id") != version_set_id
        or version_set.get("run_id") != run_id
    ):
        raise ResultStoreError("comparison version set 身份不一致")
    entries = version_set.get("entries")
    if not isinstance(entries, list):
        raise ResultStoreError("comparison version set 缺少 entries")
    if not isinstance(cursor, int) or cursor < 0:
        raise ResultStoreError("comparison_read cursor 无效")
    if not isinstance(limit, int) or limit < 1 or limit > 500:
        raise ResultStoreError("comparison_read limit 无效")

    selected = [
        item for item in entries
        if isinstance(item, dict)
        and (unit_id is None or item.get("unit_id") == unit_id)
    ]
    if view == "compact":
        items: list[dict[str, Any]] = []
        frozen_revisions: list[dict[str, Any]] = []
        total_record_count = 0
        active_record_count = 0
        warning_count = 0
        for entry in selected:
            entry_action_id = entry.get("action_id")
            entry_task_id = entry.get("task_id")
            result_path = entry.get("result_path")
            expected_revision = entry.get("revision")
            if not all(isinstance(value, str) and value for value in (entry_action_id, entry_task_id, result_path)):
                raise ResultStoreError("comparison version set 包含不完整 entry")
            candidate = Path(result_path).resolve()
            try:
                candidate.relative_to(run_dir)
            except ValueError as exc:
                raise ResultStoreError("comparison result_path 越出当前 Run 数据边界") from exc
            if not candidate.is_file():
                raise ResultStoreError(f"comparison result_path 不存在：{candidate}")
            result = read_result(candidate)
            if result.revision != expected_revision:
                raise ResultStoreError(
                    "comparison version set revision 已变化："
                    f"action={entry_action_id} expected={expected_revision} current={result.revision}"
                )
            superseded = supersession_map(result)
            records = result.records if include_history else active_records(result)
            for record in records:
                value = record.model_dump(mode="json")
                value["active"] = record.record_id not in superseded
                items.append({
                    "item_type": "record",
                    "role": entry.get("role"),
                    "unit_id": entry.get("unit_id"),
                    "action_id": entry_action_id,
                    "value": value,
                })
            if result.completion is not None:
                items.append({
                    "item_type": "completion",
                    "role": entry.get("role"),
                    "unit_id": entry.get("unit_id"),
                    "action_id": entry_action_id,
                    "value": result.completion.model_dump(mode="json"),
                })
            if include_history:
                items.extend({
                    "item_type": "warning",
                    "role": entry.get("role"),
                    "unit_id": entry.get("unit_id"),
                    "action_id": entry_action_id,
                    "value": warning,
                } for warning in result.warnings)
            total_record_count += len(result.records)
            active_record_count += len(active_records(result))
            warning_count += len(result.warnings)
            frozen_revisions.append({
                "action_id": entry_action_id,
                "revision": expected_revision,
            })
        return compact_items_page(
            metadata={
                "format_version": "pangea-comparison-read-compact-v1",
                "binding": comparison_binding.model_dump(mode="json"),
                "version_set_id": version_set_id,
                "total_record_count": total_record_count,
                "active_record_count": active_record_count,
                "warning_count": warning_count,
                "include_history": bool(include_history),
            },
            items=items,
            token_context={
                "kind": "comparison",
                "run_id": run_id,
                "action_id": action_id,
                "task_id": task_id,
                "version_set_id": version_set_id,
                "unit_id": unit_id,
                "include_history": bool(include_history),
                "frozen_revisions": frozen_revisions,
            },
            page_token=page_token,
            max_chars=max_chars,
        )
    if view != "legacy":
        raise ResultStoreError(f"未知 comparison_read view：{view}")
    output: list[dict[str, Any]] = []
    for entry in selected:
        entry_action_id = entry.get("action_id")
        entry_task_id = entry.get("task_id")
        result_path = entry.get("result_path")
        expected_revision = entry.get("revision")
        if not all(isinstance(value, str) and value for value in (entry_action_id, entry_task_id, result_path)):
            raise ResultStoreError("comparison version set 包含不完整 entry")
        candidate = Path(result_path).resolve()
        try:
            candidate.relative_to(run_dir)
        except ValueError as exc:
            raise ResultStoreError("comparison result_path 越出当前 Run 数据边界") from exc
        if not candidate.is_file():
            raise ResultStoreError(f"comparison result_path 不存在：{candidate}")
        binding = SourceBinding(
            data_root=comparison_binding.data_root,
            run_id=run_id,
            action_id=entry_action_id,
            task_id=entry_task_id,
        )
        result = read_result(candidate)
        if result.revision != expected_revision:
            raise ResultStoreError(
                "comparison version set revision 已变化："
                f"action={entry_action_id} expected={expected_revision} current={result.revision}"
            )
        view = read_records(candidate, binding, cursor=cursor, limit=limit)
        output.append({
            "role": entry.get("role"),
            "unit_id": entry.get("unit_id"),
            "action_id": entry_action_id,
            "revision": view["revision"],
            "records": view["records"],
            "completion": view["completion"],
            "warnings": view["warnings"],
            "next_cursor": view["next_cursor"],
        })
    return {
        "format_version": "pangea-comparison-read-v1",
        "binding": comparison_binding.model_dump(mode="json"),
        "version_set_id": version_set_id,
        "entries": output,
    }


@serialized_run_mutation
def work_finish(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    revision: int,
    complete: bool = True,
    note: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    binding, path, _ = _binding_and_result(data_root, run_id, action_id, task_id, writable=True)
    response = declare_completion(
        path,
        binding,
        revision,
        complete=complete,
        note=note,
        request_id=request_id,
    )
    state = {"data_root": data_root, "run_id": run_id}
    progress = load_progress(state)
    if progress is not None:
        progress.first_finish_revisions.setdefault(action_id, int(response["revision"]))
        save_progress(state, progress)
    return response


@serialized_run_mutation
def plan_write(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    expected_revision: int,
    unit: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Save one Planning Agent unit as an ordinary source-first record.

    The body is preserved verbatim.  ``unit_id`` is returned as a deterministic
    machine handle for the Graph; no title, line count, or content heuristic is
    used to decide whether the proposed unit is semantically good.
    """

    if not isinstance(unit, dict):
        raise ResultStoreError("plan_write unit 必须是 JSON object")
    for field in ("title", "purpose"):
        if not isinstance(unit.get(field), str) or not unit[field].strip():
            raise ResultStoreError(f"plan_write unit.{field} 必须是非空字符串")
    list_fields = (
        "owned_regions",
        "context_regions",
        "context_files",
        "coverage_ids",
        "asset_item_ids",
        "mechanism_ids",
    )
    for field in list_fields:
        value = unit.get(field, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise ResultStoreError(f"plan_write unit.{field} 必须是字符串数组")
    if not unit["owned_regions"]:
        raise ResultStoreError("plan_write unit.owned_regions 至少包含一个 region_id")
    binding, path, task = _binding_and_result(data_root, run_id, action_id, task_id, writable=True)
    current = read_result(path)
    existing = {item["unit_id"] for item in _effective_plan_units(current)}
    requested_id = unit.get("unit_id")
    if requested_id is not None:
        if not isinstance(requested_id, str) or requested_id not in existing:
            raise ResultStoreError("plan_write 更新必须使用该结果中已存在的 machine unit_id")
        unit_id = requested_id
        operation = "updated"
    else:
        number = 1
        while f"unit-{number:04d}" in existing:
            number += 1
        unit_id = f"unit-{number:04d}"
        operation = "created"
    saved_unit = {**unit, "unit_id": unit_id}
    response = append_records(
        path,
        binding,
        expected_revision,
        [{"kind": "unit_plan", "body": saved_unit, "relates_to": [unit_id]}],
        request_id=request_id,
    )
    response["unit_id"] = unit_id
    response["operation"] = operation
    response["diagnostics"] = _plan_diagnostics(path.parents[2], task, read_result(path))
    return response


@serialized_run_mutation
def review_decide(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    expected_revision: int,
    decision: dict[str, Any],
    replace_decision_record_ids: list[str] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(decision, dict):
        raise ResultStoreError("review_decide decision 必须是 JSON object")
    # Validate only the route vocabulary.  The conclusion and any additional
    # Reviewer fields remain in the original body and are not coerced into a
    # rich schema.
    if decision.get("disposition") not in {"pass", "unresolved", "finding"}:
        raise ResultStoreError(
            "review_decide disposition 必须是 pass、unresolved 或 finding"
        )
    binding, path, task = _binding_and_result(data_root, run_id, action_id, task_id, writable=True)
    targets = replace_decision_record_ids or []
    if not isinstance(targets, list) or any(
        not isinstance(item, str) or not item for item in targets
    ):
        raise ResultStoreError("replace_decision_record_ids 必须是 record_id 数组")
    if len(set(targets)) != len(targets):
        raise ResultStoreError("replace_decision_record_ids 不得重复")
    current = read_result(path)
    active_by_id = {record.record_id: record for record in active_records(current)}
    invalid_targets = sorted(
        target for target in targets
        if target not in active_by_id or active_by_id[target].kind != "review_decision"
    )
    if invalid_targets:
        raise ResultStoreError(
            "replace_decision_record_ids 只能引用当前 active review_decision："
            f"不可用={invalid_targets}"
        )
    if task.get("review_stage") == "comparison_review":
        expected_version_set = task.get("version_set_id")
        if (
            not isinstance(expected_version_set, str)
            or decision.get("version_set_id") != expected_version_set
        ):
            raise ResultStoreError(
                "comparison review_decide 必须回显 Graph 提供的 version_set_id"
            )
    return append_records(
        path,
        binding,
        expected_revision,
        [{"kind": "review_decision", "body": decision, "supersedes": targets}],
        request_id=request_id,
    )


def initialize_notes_result(
    path: str | Path,
    *,
    data_root: str,
    run_id: str,
    action_id: str,
) -> None:
    """Helper used by Graph when a task has no host task id yet."""

    initialize_result(
        path,
        SourceBinding(
            data_root=str(Path(data_root).resolve()),
            run_id=run_id,
            action_id=action_id,
            task_id="pending",
        ),
    )


def parse_json_argument(value: str) -> Any:
    """Parse a CLI JSON argument without accepting a path fallback."""

    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"参数不是合法 JSON：{exc}") from exc
