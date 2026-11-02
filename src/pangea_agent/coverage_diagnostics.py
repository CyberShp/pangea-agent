"""Advisory reference facts only; workers decide applicability and triggering."""
from __future__ import annotations

from collections import Counter

COVERAGE_REFERENCE_CONTRACT = "coverage-reference-v1"


def coverage_reference_diagnostics(kind: str, body: object, gaps: object) -> list[dict]:
    """Check explicit IDs against frozen gaps without interpreting case prose.

    A valid reference proves membership, not source applicability, execution or
    whether the steps reach the zero-count outcome. Empty plans are permitted.
    """
    if kind not in {"unit_plan", "test_case", "test_case_group"} or not isinstance(body, dict):
        return []
    field = "coverage_ids" if kind == "unit_plan" else "coverage_refs"
    required = kind != "unit_plan" and body.get("purpose") == "coverage"
    values = body.get(field, [])
    warnings = []

    def warn(code, location, message):
        warnings.append({"kind": "coverage_reference_hint", "code": code,
                         "field": location, "message": message})

    if not isinstance(gaps, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("coverage_id"), str)
        or not row["coverage_id"].strip() for row in gaps
    ):
        warn("coverage_context_unavailable", field,
             "冻结 coverage-gaps 缺失、不可读取或形态错误，不能核实引用；不等于零缺口，原文保留。")
        return warnings
    if not isinstance(values, list):
        warn("invalid_reference_shape", field, f"{field} 不是数组；原文保留，请原 worker 核对冻结缺口引用。")
        return warnings
    if not values and required:
        warn("missing_coverage_reference", field,
             f"{field} 未声明冻结缺口引用；这不证明用例无效，请核对具体缺口依据，必要时修正原记录；允许零新增用例。")
    known = Counter(row["coverage_id"] for row in gaps)
    for index, value in enumerate(values):
        reference = value.get("coverage_id") if isinstance(value, dict) and field == "coverage_refs" else value
        location = f"{field}[{index}]"
        if not isinstance(reference, str) or not reference.strip():
            warn("invalid_reference_shape", location, f"{location} 缺少非空 coverage_id；原文保留。")
        elif reference not in known:
            warn("unknown_coverage_reference", location,
                 f"{location}={reference} 不在本次冻结 coverage-gaps 中；不能据此确认零覆盖目标，请原 worker 核对。")
        elif known[reference] > 1:
            warn("duplicate_reference_target", location,
                 f"{location}={reference} 在冻结 coverage-gaps 中对应多条记录，引用目标不唯一；请原 worker 核对，原文保留。")
    return warnings
