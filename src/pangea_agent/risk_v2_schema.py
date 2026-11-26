"""Deterministic contract validation for risk-method-v2.

Validates JSON shape, required fields, literal source quotes, and fault-injection
recipe structure for needs_runtime_verification risks. Never judges whether a
risk is real, never forces non-zero risk counts, and never auto-mutates content.
"""
from __future__ import annotations

from typing import Any

from pangea_agent.risk_schema import _SOURCE_REFERENCE

RISK_METHOD_V2 = "risk-method-v2"
RISK_BLUEPRINT_FORMAT = "risk-blueprint-v1"
CHECK_FORMAT_V2 = "behavior-check-v2"
RISK_FORMAT_V3 = "behavior-risk-v3"

TRACKS = ("error_rollback", "concurrency_timing", "resource_lifecycle", "memory_corruption")
VERIFICATION_STATUSES = ("static_proven", "needs_runtime_verification")
SEVERITIES = ("Low", "Medium", "High", "Critical", "Unknown")
CHECK_CONCLUSIONS_V2 = (
    "confirmed",
    "needs_runtime_verification",
    "excluded",
    "unresolved",
    "not_applicable",
)
INSTRUMENTATION_KINDS = (
    "fault_return",
    "race_window_delay",
    "leak_counter_probe",
    "memory_poison_or_bound_probe",
    "custom",
)
_FORBIDDEN_PATCH_FIELDS = ("remediation_hint", "fix", "patch", "suggested_patch")


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_blueprint_structure(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return ["blueprint.body 必须是 JSON 对象"]
    errors: list[str] = []
    if body.get("format_version") != RISK_BLUEPRINT_FORMAT:
        errors.append(f"format_version 必须是 {RISK_BLUEPRINT_FORMAT}")
    for key in ("resource_entities", "concurrency_contexts", "transaction_chains", "memory_hazard_zones"):
        if key == "memory_hazard_zones" and key not in body:
            continue
        items = body.get(key)
        if not isinstance(items, list):
            errors.append(f"{key} 必须是数组（无对应实体时可写 []）")
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{key}[{idx}] 必须是对象")
                continue
            if not _non_empty_str(item.get("id")):
                errors.append(f"{key}[{idx}].id 必须是非空字符串")
            if not _non_empty_str(item.get("name")):
                errors.append(f"{key}[{idx}].name 必须是非空字符串")
    return errors


def check_check_v2_structure(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return ["check.body 必须是 JSON 对象"]
    errors: list[str] = []
    if body.get("format_version") != CHECK_FORMAT_V2:
        errors.append(f"format_version 必须是 {CHECK_FORMAT_V2}")
    for key in ("check_id", "track", "object", "hypothesis", "conclusion", "reasoning"):
        if not _non_empty_str(body.get(key)):
            errors.append(f"{key} 必须是非空字符串")
    if body.get("track") not in TRACKS:
        errors.append(f"track 必须是 {'/'.join(TRACKS)}")
    if body.get("conclusion") not in CHECK_CONCLUSIONS_V2:
        errors.append(f"conclusion 必须是 {'/'.join(CHECK_CONCLUSIONS_V2)}")
    sites = body.get("sites")
    if not isinstance(sites, list) or not sites or any(
        not isinstance(ref, str) or not _SOURCE_REFERENCE.fullmatch(ref) for ref in sites
    ):
        errors.append("sites 必须是非空源码引用数组（repo_id:path:line 或 line-line）")
    if body.get("conclusion") in ("confirmed", "needs_runtime_verification", "excluded"):
        quotes = body.get("evidence_quotes")
        if not isinstance(quotes, list) or not quotes:
            errors.append("confirmed/needs_runtime_verification/excluded 的 evidence_quotes 必填")
    for list_key in ("risk_ids", "matched_dts_pattern_ids"):
        if list_key in body and (
            not isinstance(body[list_key], list)
            or any(not _non_empty_str(x) for x in body[list_key])
        ):
            errors.append(f"{list_key} 必须是非空字符串数组")
    return errors


def check_risk_v3_structure(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return ["risk.body 必须是 JSON 对象"]
    errors: list[str] = []
    if body.get("format_version") != RISK_FORMAT_V3:
        errors.append(f"format_version 必须是 {RISK_FORMAT_V3}")
    for key in (
        "risk_id",
        "track",
        "verification_status",
        "severity",
        "title",
        "trigger",
        "system_result",
        "external_observation",
        "exclusion_condition",
    ):
        if not _non_empty_str(body.get(key)):
            errors.append(f"{key} 必须是非空字符串")
    if body.get("track") not in TRACKS:
        errors.append(f"track 必须是 {'/'.join(TRACKS)}")
    if body.get("verification_status") not in VERIFICATION_STATUSES:
        errors.append(f"verification_status 必须是 {'/'.join(VERIFICATION_STATUSES)}")
    if body.get("severity") not in SEVERITIES:
        errors.append(f"severity 必须是 {'/'.join(SEVERITIES)}")
    path_steps = body.get("interleaving_or_path")
    if not isinstance(path_steps, list) or not path_steps or any(not _non_empty_str(s) for s in path_steps):
        errors.append("interleaving_or_path 必须是非空字符串数组（逐行交错序列或副作用退出路径）")
    evidence = body.get("source_evidence")
    if not isinstance(evidence, list) or not evidence or any(
        not isinstance(ref, str) or not _SOURCE_REFERENCE.fullmatch(ref) for ref in evidence
    ):
        errors.append("source_evidence 必须是非空源码引用数组（repo_id:path:line 或 line-line）")
    quotes = body.get("evidence_quotes")
    if not isinstance(quotes, list) or not quotes:
        errors.append("evidence_quotes 必须是非空数组")
    if "linked_test_case_ids" in body and not isinstance(body["linked_test_case_ids"], list):
        errors.append("linked_test_case_ids 必须是数组")
    if "matched_dts_pattern_ids" in body and (
        not isinstance(body["matched_dts_pattern_ids"], list)
        or any(not _non_empty_str(x) for x in body["matched_dts_pattern_ids"])
    ):
        errors.append("matched_dts_pattern_ids 必须是非空字符串数组")
    for forbidden in _FORBIDDEN_PATCH_FIELDS:
        if forbidden in body:
            errors.append(f"检测到顶层键 {forbidden}：正式风险只交付证据，不交付源码修改建议")

    # Mandatory structured instrumentation recipe for needs_runtime_verification risks.
    if body.get("verification_status") == "needs_runtime_verification":
        if not _non_empty_str(body.get("runtime_uncertainty")):
            errors.append("verification_status=needs_runtime_verification 时 runtime_uncertainty 必填")
        inst = body.get("instrumentation")
        points = inst.get("points") if isinstance(inst, dict) else None
        if not isinstance(points, list) or not points:
            errors.append("verification_status=needs_runtime_verification 时 instrumentation.points 必须是非空桩点数组")
        else:
            for idx, point in enumerate(points):
                if not isinstance(point, dict):
                    errors.append(f"instrumentation.points[{idx}] 必须是对象")
                    continue
                for req in ("id", "repo_id", "path", "symbol", "action", "observe"):
                    if not _non_empty_str(point.get(req)):
                        errors.append(f"instrumentation.points[{idx}].{req} 必须是非空字符串")
                if "kind" in point and point["kind"] not in INSTRUMENTATION_KINDS:
                    errors.append(f"instrumentation.points[{idx}].kind 必须是 {'/'.join(INSTRUMENTATION_KINDS)}")
    return errors


def format_v2_gate_errors(kind: str, offset: int, errors: list[str]) -> str:
    lines = [f"record[{offset}] {kind} (risk-method-v2) 确定性格式/引文门禁未通过，共 {len(errors)} 处，请原 Worker 本轮修正后重写："]
    lines.extend(f"- {err}" for err in errors)
    return "\n".join(lines)


def blueprint_coverage_diagnostics(
    track: str | None,
    blueprint: dict[str, Any] | None,
    records: list[Any],
    dts_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Non-blocking work-finish advisory: summarize entity and homologous DTS dispositions."""
    rows = [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in records]
    checks = [r["body"] for r in rows if r.get("kind") == "check" and isinstance(r.get("body"), dict)]
    risks = [r["body"] for r in rows if r.get("kind") == "risk" and isinstance(r.get("body"), dict)]
    covered_entities = {
        str(b.get("entity_ref"))
        for b in (*checks, *risks)
        if isinstance(b.get("entity_ref"), str) and b.get("entity_ref")
    }
    covered_dts = {
        str(pid)
        for b in (*checks, *risks)
        for pid in (b.get("matched_dts_pattern_ids") or [])
        if isinstance(pid, str) and pid
    }
    track_entity_key = {
        "resource_lifecycle": "resource_entities",
        "concurrency_timing": "concurrency_contexts",
        "error_rollback": "transaction_chains",
        "memory_corruption": "memory_hazard_zones",
    }.get(track or "")
    expected_entities = []
    if isinstance(blueprint, dict) and track_entity_key:
        expected_entities = [
            str(item["id"])
            for item in blueprint.get(track_entity_key, [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
    expected_homologous = [
        str(item["dts_id"])
        for item in (dts_context or {}).get("homologous_dts", [])
        if isinstance(item, dict)
        and isinstance(item.get("dts_id"), str)
        and (not track or item.get("track") in (None, track))
    ]
    return {
        "track": track,
        "missing_blueprint_entities": sorted(set(expected_entities) - covered_entities),
        "unaddressed_homologous_dts": sorted(set(expected_homologous) - covered_dts),
        "static_proven_count": sum(1 for r in risks if r.get("verification_status") == "static_proven"),
        "needs_runtime_verification_count": sum(1 for r in risks if r.get("verification_status") == "needs_runtime_verification"),
        "check_count": len(checks),
    }
