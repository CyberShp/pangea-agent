"""Client-facing source-first APIs.

These functions are intentionally thin wrappers around the frozen-source and
result stores.  The host supplies the exact task identity; this module never
searches another Run or guesses a replacement task.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    # Validate only the transport vocabulary.  The conclusion itself remains
    # the reviewer's original body and is never inferred from record contents.
    ReviewDecision.model_validate(decision)
    binding, path, _ = _binding_and_result(data_root, run_id, action_id, task_id)
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

