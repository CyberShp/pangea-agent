"""Atomic transport for Planner-authored units; no semantic planning decisions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pangea_agent.contract_context import ContractContext
from pangea_agent.graph.result_store import ResultStoreError, _assert_binding, append_records, read_result
from pangea_agent.graph.workflow_store import serialized_run_mutation
from pangea_agent.inventory.on_demand import read_index
from pangea_agent.inventory.source_access import expand_owned_files


def _fingerprint(units: list[dict[str, Any]], mode: str) -> str:
    try:
        text = json.dumps({"operation": mode, "units": units}, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResultStoreError("规划载荷必须是可保存的 JSON 数据") from exc
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_unit(unit: Any, offset: int) -> None:
    """Keep the existing single-unit shape checks, without extra semantic gates."""
    label = f"units[{offset}]"
    if not isinstance(unit, dict):
        raise ResultStoreError(f"{label} 必须是 JSON object")
    for field in ("title", "purpose"):
        if not isinstance(unit.get(field), str) or not unit[field].strip():
            raise ResultStoreError(f"{label}.{field} 必须是非空字符串")
    for field in ("owned_regions", "context_regions", "context_files", "coverage_ids",
                  "asset_item_ids", "mechanism_ids"):
        value = unit.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise ResultStoreError(f"{label}.{field} 必须是字符串数组")


def _legacy_receipt_matches(receipt: dict, current, units: list[dict]) -> bool:
    """Read pre-fingerprint single-plan receipts without rewriting old results."""
    identifiers = receipt.get("record_ids")
    if not isinstance(identifiers, list) or len(identifiers) != 1 or len(units) != 1:
        return False
    record = next((item for item in current.records if item.record_id == identifiers[0]), None)
    if record is None or record.kind != "unit_plan" or not isinstance(record.body, dict):
        return False
    unit = units[0]
    if record.body != {**unit, "unit_id": unit.get("unit_id", record.body.get("unit_id"))}:
        return False
    prior_owner = any(item.kind == "unit_plan" and isinstance(item.body, dict)
                      and item.body.get("unit_id") == record.body.get("unit_id")
                      for item in current.records[:current.records.index(record)])
    return prior_owner == (unit.get("unit_id") is not None)


def _plan_response(response: dict, units: list[dict], task: dict, current, run_dir, *, replayed: bool) -> dict:
    from pangea_agent.cli.source_first_api import _plan_diagnostics

    by_id = {item.record_id: item for item in current.records}
    saved = []
    identifiers = response.get("record_ids", [])
    if len(identifiers) != len(units):
        raise ResultStoreError("规划回执的单元记录数不匹配，原结果保留")
    for unit, record_id in zip(units, identifiers):
        record = by_id.get(record_id)
        if (record is None or record.kind != "unit_plan" or not isinstance(record.body, dict)
                or not isinstance(record.body.get("unit_id"), str)):
            raise ResultStoreError("规划回执引用的原始单元不可读取，原结果保留")
        saved.append({"unit_id": record.body["unit_id"], "plan_record_id": record_id,
                      "operation": "updated" if unit.get("unit_id") is not None else "created"})
    # A retry acknowledges the original save, even after later writes. Do not
    # associate today's diagnostics with that old receipt revision.
    snapshot = current.model_copy(update={
        "revision": response["revision"],
        "records": [item for item in current.records if item.created_revision <= response["revision"]],
    })
    return {**response, "units": saved, "replayed": replayed,
            "current_revision": current.revision,
            "diagnostics": _plan_diagnostics(run_dir, task, snapshot)}


def _write_plan_units(data_root: str, run_id: str, action_id: str, task_id: str, *,
                      expected_revision: int, units: list[dict[str, Any]],
                      request_id: str | None, mode: str) -> dict[str, Any]:
    """Caller holds the Run mutation lock; append_records owns the atomic save."""
    from pangea_agent.cli.source_first_api import _binding_and_result, _effective_plan_units

    binding, path, task = _binding_and_result(data_root, run_id, action_id, task_id, writable=True)
    if mode == "plan-write-batch" and task.get("task_type") != "source_first_plan":
        raise ResultStoreError("plan-write-batch 仅用于当前绑定的 Planning task")
    if not isinstance(units, list) or not units:
        raise ResultStoreError("units 必须是非空单元对象数组；已确定的单元即可提交")
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
        raise ResultStoreError("request_id 必须是非空字符串")
    for offset, unit in enumerate(units):
        _check_unit(unit, offset)
    fingerprint = _fingerprint(units, mode)
    current = _assert_binding(read_result(path), binding)
    if request_id and request_id in current.receipts:
        receipt = dict(current.receipts[request_id])
        stored = receipt.get("request_fingerprint")
        if stored != fingerprint and not (
            stored is None and mode == "plan-write" and _legacy_receipt_matches(receipt, current, units)
        ):
            raise ResultStoreError("request_id 已用于其他载荷；请复用原载荷重试，修改内容须使用新的 request_id")
        return _plan_response(receipt, units, task, current, path.parents[2], replayed=True)

    existing = {item["unit_id"] for item in _effective_plan_units(current)}
    reserved = {item.body.get("unit_id") for item in current.records
                if item.kind == "unit_plan" and isinstance(item.body, dict)
                and isinstance(item.body.get("unit_id"), str)}
    index = read_index(path.parents[2])
    records = []
    updated_ids = set()
    number = 1
    for offset, unit in enumerate(units):
        # This checks the same path boundary as single plan-write. Advisory
        # ownership/coverage diagnostics remain advisory and preserve the body.
        expand_owned_files(unit, index, task.get("owned_scope_paths", []))
        unit_id = unit.get("unit_id")
        if unit_id is not None:
            if not isinstance(unit_id, str) or unit_id not in existing:
                raise ResultStoreError(f"units[{offset}].unit_id 更新必须使用该结果中已存在的 machine unit_id")
            if unit_id in updated_ids:
                raise ResultStoreError(f"units[{offset}].unit_id 在本批重复更新：{unit_id}")
            updated_ids.add(unit_id)
        else:
            while f"unit-{number:04d}" in reserved:
                number += 1
            unit_id = f"unit-{number:04d}"
            reserved.add(unit_id)
        records.append({"kind": "unit_plan", "body": {**unit, "unit_id": unit_id}, "relates_to": [unit_id]})
    response = append_records(path, binding, expected_revision, records, request_id=request_id,
                              request_fingerprint=fingerprint if request_id else None,
                              record_contracts=task.get("record_contracts"),
                              context=ContractContext(data_root, run_id, task))
    return _plan_response(response, units, task, read_result(path), path.parents[2], replayed=False)


@serialized_run_mutation
def plan_write_batch(data_root: str, run_id: str, action_id: str, task_id: str, *,
                     expected_revision: int, units: list[dict[str, Any]],
                     request_id: str | None = None) -> dict[str, Any]:
    """Save already-decided units atomically; do not wait for the entire plan."""
    return _write_plan_units(data_root, run_id, action_id, task_id,
                             expected_revision=expected_revision, units=units,
                             request_id=request_id, mode="plan-write-batch")
