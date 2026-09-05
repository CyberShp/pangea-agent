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
    append_records,
    declare_completion,
    initialize_result,
    read_records,
    read_result,
)
from pangea_agent.inventory.source_access import (
    resolve_binding,
    source_index as read_source_index,
    source_read as read_source,
    source_search as search_source,
)
from pangea_agent.graph.workflow_store import source_first_version_set_path
from pangea_agent.models.source_first import ReviewDecision, SourceBinding


def _binding_and_result(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
) -> tuple[SourceBinding, Path, dict[str, Any]]:
    binding, run_dir, _, task = resolve_binding(data_root, run_id, action_id, task_id)
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

    binding, path, _ = _binding_and_result(data_root, run_id, action_id, task_id)
    result = read_result(path)
    # ``read_records`` performs the binding check without exposing private
    # store internals; it also confirms the shell can be consumed as notes.
    view = read_records(path, binding, cursor=0, limit=1)
    records = result.records
    warnings = list(view.get("warnings", []))
    if not records:
        return {
            "status": "incomplete",
            "reason": "result_path 仍没有正文 records，空结果不能作为完成交付",
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
    return {
        "status": "valid",
        "revision": result.revision,
        "record_count": len(records),
        "warnings": warnings,
    }


def source_index(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    cursor: str | None = None,
    page_size: int = 64,
) -> dict[str, Any]:
    return read_source_index(
        data_root,
        run_id,
        action_id,
        task_id,
        cursor=cursor,
        page_size=page_size,
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
    )


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
    binding, path, _ = _binding_and_result(data_root, run_id, action_id, task_id)
    return append_records(
        path,
        binding,
        expected_revision,
        records,
        request_id=request_id,
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
) -> dict[str, Any]:
    binding, path, _ = _binding_and_result(data_root, run_id, action_id, task_id)
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
    binding, path, _ = _binding_and_result(data_root, run_id, action_id, task_id)
    return declare_completion(
        path,
        binding,
        revision,
        complete=complete,
        note=note,
        request_id=request_id,
    )


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
    binding, path, _ = _binding_and_result(data_root, run_id, action_id, task_id)
    current = read_result(path)
    unit_id = str(unit.get("unit_id") or f"unit-{len(current.records) + 1:04d}")
    response = append_records(
        path,
        binding,
        expected_revision,
        [{"kind": "unit_plan", "body": unit, "relates_to": [unit_id]}],
        request_id=request_id,
    )
    response["unit_id"] = unit_id
    return response


def review_decide(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    expected_revision: int,
    decision: dict[str, Any],
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
    binding, path, task = _binding_and_result(data_root, run_id, action_id, task_id)
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
        [{"kind": "review_decision", "body": decision}],
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
