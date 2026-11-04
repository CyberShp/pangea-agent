"""Bounded transport for literal planning inputs; no semantic selection."""

from __future__ import annotations

import json
from typing import Any

from pangea_agent.inventory.source_access import (
    SourceAccessError, input_read, resolve_binding, source_index, source_read, source_search,
)


PLANNING_IO_POLICY = "planning-io-v1"
PLANNING_CAPABILITIES = ["planning-startup-v1", "read-batch", "coverage-read", "plan-write-batch"]


def _size(value: Any) -> int:
    # Account for readable CLI indentation, not only compact JSON text.
    return len(json.dumps(value, ensure_ascii=False, indent=2))


def planning_startup(data_root: str, run_id: str, action_id: str, task_id: str,
                     task: dict, *, max_chars: int = 18_000) -> dict:
    """Supply only declared small planning inputs, with explicit unread cursors."""
    declared = {item["input_id"] for item in task.get("inputs", []) if isinstance(item, dict)
                and isinstance(item.get("input_id"), str)}
    identifiers = [name for name in ("analysis_scene", "unit_planning_rubric",
                                    "scene_planning_rubric", "planning_metadata") if name in declared]
    result = {"pages": [], "pending_inputs": [], "complete": True, "max_chars": max_chars}
    for number, input_id in enumerate(identifiers):
        # Reserve enough space to name each remaining input and its cursor.
        remaining = max_chars - _size(result) - 512 * (len(identifiers) - number)
        if remaining < 512:
            result["pending_inputs"].append({"input_id": input_id, "cursor": None})
            continue
        limit = min(12_000, remaining)
        while True:
            page = input_read(data_root, run_id, action_id, task_id, input_id=input_id, max_chars=limit)
            if _size(page) <= remaining or limit == 1:
                break
            limit = max(1, limit // 2)
        if _size(page) > remaining:
            result["pending_inputs"].append({"input_id": input_id, "cursor": None})
            continue
        result["pages"].append(page)
        if page["next_cursor"] is not None:
            result["pending_inputs"].append({"input_id": input_id, "cursor": page["next_cursor"]})
    result["complete"] = not result["pending_inputs"]
    return result


_OPERATIONS = {
    "input-read": (input_read, {"input_id", "cursor", "max_chars"}),
    "source-read": (source_read, {"repo_id", "path", "region_id", "line_start", "line_end",
                                  "page_token", "max_chars", "view"}),
    "source-index": (source_index, {"repo_id", "path", "page_token", "max_chars", "view"}),
    "source-search": (source_search, {"query", "repo_id", "path", "page_token", "max_chars", "view"}),
}


def read_batch(data_root: str, run_id: str, action_id: str, task_id: str, *,
               requests: list[dict], start_index: int = 0, max_chars: int = 24_000) -> dict:
    """Read known independent requests under one binding and one output budget.

    Each item keeps its original paging metadata. ``next_request_index`` names
    the first unexecuted request in the same array; it is NOT a source cursor.
    Partial item results must separately continue with their original query.
    """
    binding, _, _, _ = resolve_binding(data_root, run_id, action_id, task_id)
    if not isinstance(requests, list) or not requests or len(requests) > 64:
        raise SourceAccessError("read-batch requests 必须是 1 到 64 个只读请求；可分批继续")
    if type(max_chars) is not int or not 3000 <= max_chars <= 24_000:
        raise SourceAccessError("read-batch max_chars 必须在 3000 到 24000 之间")
    if type(start_index) is not int or not 0 <= start_index < len(requests):
        raise SourceAccessError("read-batch start_index 超出请求数组")
    normalized = []
    for index, request in enumerate(requests):
        if not isinstance(request, dict) or request.get("operation") not in _OPERATIONS:
            raise SourceAccessError(f"requests[{index}].operation 必须为受支持的只读操作")
        operation = request["operation"]
        function, allowed = _OPERATIONS[operation]
        extra = set(request) - allowed - {"operation"}
        if extra:
            raise SourceAccessError(f"requests[{index}] 不支持参数：{sorted(extra)}")
        arguments = {key: value for key, value in request.items() if key != "operation"}
        requested_limit = arguments.pop("max_chars", 12_000)
        if type(requested_limit) is not int or not 1000 <= requested_limit <= 24_000:
            raise SourceAccessError(f"requests[{index}].max_chars 必须在 1000 到 24000 之间")
        if operation != "input-read":
            arguments.setdefault("view", "text" if operation == "source-read" else "compact")
            valid_views = {"compact", "text"} if operation == "source-read" else {"compact"}
            if arguments["view"] not in valid_views:
                raise SourceAccessError(f"requests[{index}] 必须使用有字符预算的 compact/text 视图")
        normalized.append((operation, function, arguments, requested_limit))
    output = {"format_version": "pangea-read-batch-v1", "binding": binding.model_dump(mode="json"),
              "items": [], "next_request_index": start_index, "request_count": len(requests),
              "max_chars": max_chars, "all_succeeded": True}
    for index in range(start_index, len(normalized)):
        operation, function, arguments, requested_limit = normalized[index]
        available = max_chars - _size(output) - 256
        if available < 1600:
            if not output["items"]:
                raise SourceAccessError("read-batch 绑定元数据占满预算，请单独调用该请求或增加 max_chars")
            break
        share = max(1600, available // min(4, len(normalized) - index))
        limit = min(requested_limit, available - 128, share)
        while True:
            try:
                page = function(data_root, run_id, action_id, task_id, **arguments, max_chars=limit)
                item = {"request_index": index, "operation": operation, "ok": True, "result": page}
            except (ValueError, TypeError) as exc:
                item = {"request_index": index, "operation": operation, "ok": False,
                        "error": {"code": type(exc).__name__, "message": str(exc)}}
            candidate = {**output, "items": [*output["items"], item],
                         "next_request_index": index + 1 if index + 1 < len(normalized) else None,
                         "all_succeeded": output["all_succeeded"] and item["ok"]}
            if _size(candidate) <= max_chars or limit <= 1000:
                break
            limit = max(1000, limit // 2)
        if _size(candidate) > max_chars:
            if not output["items"]:
                raise SourceAccessError("read-batch 单项元数据超出预算，请单独调用该请求或增加 max_chars")
            break
        output = candidate
    return output
