from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pangea_agent.models.analysis import (
    AnalysisTask,
    CorrectionAssertion,
    ClosureTask,
    ReviewFinding,
    UnitSemanticResult,
    ValueSnapshot,
)


_CORRECTION_COLLECTION_KEYS = {
    "flows": "flow_key",
    "input_decisions": "item_id",
    "branch_decisions": "branch_id",
    "coverage_decisions": "coverage_id",
    "mechanism_decisions": "mechanism_id",
    "risks": "risk_key",
    "scenarios": "scenario_key",
    "test_cases": "case_key",
}


@dataclass(frozen=True)
class ResultContractIssue:
    """One deterministic contract issue that prevents safe downstream routing."""

    family: str
    path: str
    message: str
    context: dict[str, Any]


class ResultContractValidationError(ValueError):
    """Aggregated structural issues that require same-session Agent repair."""

    def __init__(self, title: str, issues: list[ResultContractIssue]):
        self.title = title
        self.issues = tuple(issues)
        super().__init__(title)


def source_evidence_excerpt_errors(
    evidence_items,
    source_roots: Mapping[str, str],
    label: str,
) -> list[str]:
    """Reject observations that are not contiguous excerpts of cited lines."""
    errors: list[str] = []
    for evidence in evidence_items:
        source_root = source_roots.get(evidence.repo_id)
        if source_root is None:
            continue
        root = Path(source_root).resolve()
        source_path = (root / evidence.path.replace("\\", "/").strip("/")).resolve()
        try:
            source_path.relative_to(root)
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError, ValueError):
            # Existing scope/input checks own unreadable or out-of-root paths.
            continue
        line_end = evidence.line_end or evidence.line_start
        if evidence.line_start > len(lines) or line_end > len(lines):
            errors.append(
                f"{label}证据行号超出冻结源码："
                f"{evidence.repo_id}:{evidence.path}:"
                f"{evidence.line_start}-{line_end}；source_lines={len(lines)}"
            )
            continue
        cited_excerpt = "\n".join(lines[evidence.line_start - 1:line_end])
        observation = evidence.observation.replace("\r\n", "\n").replace("\r", "\n")
        if observation not in cited_excerpt:
            errors.append(
                f"{label} observation 不是声明行范围内的连续逐字源码片段："
                f"{evidence.repo_id}:{evidence.path}:"
                f"{evidence.line_start}-{line_end}"
            )
    return errors


def risk_test_obligations(result: UnitSemanticResult) -> list[str]:
    """Return risks whose test disposition is structurally incomplete."""
    linked_risks = {
        risk_key
        for case in result.test_cases
        for risk_key in case.linked_risk_keys
    }
    obligations: list[str] = []
    for risk in result.risks:
        linked = risk.risk_key in linked_risks
        if risk.test_disposition == "test_required":
            if not linked:
                obligations.append(
                    f"{risk.risk_key}: 关联至少一个测试用例，或由 Agent 改判为 developer_confirm / 从受支持业务入口不可达"
                )
            continue
        if risk.test_disposition == "developer_confirm":
            if linked:
                obligations.append(
                    f"{risk.risk_key}: 已关联正式测试用例，不能同时声明 developer_confirm"
                )
            continue
        if linked:
            obligations.append(
                f"{risk.risk_key}: 已关联测试用例，不能同时声明从受支持业务入口不可达"
            )
        elif not risk.unreachable_reason or not risk.unreachable_evidence:
            obligations.append(
                f"{risk.risk_key}: 不可达决定必须包含原因和源码证据"
            )
    return obligations


def validate_unit_result(
    task: AnalysisTask,
    result: UnitSemanticResult,
    selected_inputs: dict,
    review_findings: list[ReviewFinding] | None = None,
) -> list[str]:
    """Validate deterministic references without judging Agent semantics."""
    asset_items = selected_inputs.get("asset_items", {})
    coverage_gaps = selected_inputs.get("coverage_gaps", [])
    mechanisms = selected_inputs.get("defect_mechanisms", {})
    expected_inputs = set(asset_items)
    expected_coverage = {item["coverage_id"] for item in coverage_gaps}
    expected_mechanisms = set(mechanisms)

    warnings = _reference_warnings(result)
    warnings.extend(_check_decisions(
        "input_decisions",
        expected_inputs,
        [item.item_id for item in result.input_decisions],
    ))
    warnings.extend(_check_decisions(
        "coverage_decisions",
        expected_coverage,
        [item.coverage_id for item in result.coverage_decisions],
    ))
    warnings.extend(_check_decisions(
        "mechanism_decisions",
        expected_mechanisms,
        [item.mechanism_id for item in result.mechanism_decisions],
    ))

    allowed_paths = set(task.unit.source_scope) | set(task.unit.context_scope)
    for evidence in _all_evidence(result, include_review_decisions=False):
        if evidence.repo_id != task.unit.repo_id or evidence.path not in allowed_paths:
            warnings.append(
                "源码证据待确认，不属于当前分析单元："
                f"{evidence.repo_id}:{evidence.path}:{evidence.line_start}"
            )
        if evidence.line_end is not None and evidence.line_end < evidence.line_start:
            warnings.append(
                "源码证据行号范围待确认："
                f"{evidence.repo_id}:{evidence.path}:"
                f"{evidence.line_start}-{evidence.line_end}"
            )
    warnings.extend(source_evidence_excerpt_errors(
        _all_evidence(result, include_review_decisions=True),
        {task.repository.repo_id: task.repository.source_root},
        "Analysis",
    ))
    warnings.extend(_review_decision_evidence_warnings(
        task,
        result,
        review_findings,
    ))
    has_targeted_decisions = any(
        getattr(item, "correction_id", None) is not None
        for item in result.review_finding_decisions
    )
    if review_findings is not None:
        expected_finding_keys = {item.finding_key for item in review_findings}
        actual_finding_keys = [
            item.finding_key for item in result.review_finding_decisions
        ]
        if has_targeted_decisions:
            warnings.extend(_check_decision_membership(
                "review_finding_decisions",
                expected_finding_keys,
                actual_finding_keys,
            ))
        else:
            warnings.extend(_check_decisions(
                "review_finding_decisions",
                expected_finding_keys,
                actual_finding_keys,
            ))

    known_inputs = expected_inputs | expected_coverage | expected_mechanisms
    item_types = {
        item_id: item.get("item_type") for item_id, item in asset_items.items()
    }
    item_types.update({item_id: "historical_defect" for item_id in mechanisms})
    item_types.update({item_id: "coverage" for item_id in expected_coverage})
    for case in result.test_cases:
        unknown_inputs = set(case.linked_input_ids) - known_inputs
        if unknown_inputs:
            warnings.append(
                f"测试用例 {case.case_key} 引用了未知输入：{sorted(unknown_inputs)}"
            )
        unsupported_basis = _unsupported_basis(case, item_types)
        if unsupported_basis:
            warnings.append(
                f"测试用例 {case.case_key} basis 缺少真实关联，"
                f"保留 Agent 原值：actual={case.basis} "
                f"unsupported={unsupported_basis}"
            )

    return warnings


def resolve_correction_target(
    result: UnitSemanticResult | Mapping[str, Any],
    target: Any,
) -> dict[str, Any]:
    """Resolve one structured Closure target without interpreting text."""

    payload = _as_mapping(result, "UnitSemanticResult")
    target_data = _as_mapping(target, "correction target")
    collection = target_data.get("collection")
    object_key = target_data.get("object_key")
    field_path = target_data.get("field_path")
    if field_path is not None:
        if (
            not isinstance(field_path, str)
            or not field_path
            or not field_path.startswith("/")
        ):
            raise ValueError(
                "correction target field_path 必须是 RFC 6901 JSON Pointer 或 null"
            )

    if collection == "result":
        if object_key is not None:
            raise ValueError("collection=result 时 object_key 必须为 null")
        if field_path not in {"/summary", "/unresolved"}:
            raise ValueError(
                "collection=result 时 field_path 只允许 /summary 或 /unresolved"
            )
        return _snapshot_value(payload, field_path)

    key_field = _CORRECTION_COLLECTION_KEYS.get(collection)
    if key_field is None:
        raise ValueError(f"correction target collection 不受支持：{collection!r}")
    if object_key is None and field_path is None:
        return {"exists": False, "value": None}
    if not isinstance(object_key, str) or not object_key:
        raise ValueError(
            f"collection={collection} 时 object_key 必须是非空字符串；"
            "仅新增整个对象的 target 可同时省略 object_key 和 field_path"
        )
    items = payload.get(collection)
    if not isinstance(items, list):
        raise ValueError(f"UnitSemanticResult.{collection} 必须是数组")
    matches = [
        item
        for item in items
        if isinstance(item, Mapping) and item.get(key_field) == object_key
    ]
    if len(matches) > 1:
        raise ValueError(
            f"correction target 定位到重复对象：{collection}:{object_key}"
        )
    if not matches:
        return {"exists": False, "value": None}
    return _snapshot_value(matches[0], field_path)


def correction_target_identity_errors(
    result: UnitSemanticResult | Mapping[str, Any],
    target: Any,
) -> list[str]:
    """Validate one correction coordinate without judging its semantic content."""

    try:
        snapshot = resolve_correction_target(result, target)
    except ValueError as exc:
        return [str(exc)]

    target_data = _as_mapping(target, "correction target")
    collection = target_data.get("collection")
    object_key = target_data.get("object_key")
    field_path = target_data.get("field_path")
    missing_object_target = (
        collection != "result"
        and object_key is None
        and field_path is None
    )
    if missing_object_target:
        return []

    errors: list[str] = []
    if collection != "result" and object_key is not None:
        whole_object = resolve_correction_target(
            result,
            {**target_data, "field_path": None},
        )
        if not whole_object["exists"]:
            errors.append(
                "对象在 validated Analysis 中不存在；新增整个对象必须使用 "
                "object_key=null、field_path=null"
            )
        elif field_path is not None and not snapshot["exists"]:
            errors.append(
                "field_path 在 validated Analysis 对象中不存在；"
                "不得把缺失字段伪装成已有对象修正"
            )

    if field_path is None:
        return errors

    try:
        tokens = [
            _decode_json_pointer_token(token)
            for token in field_path[1:].split("/")
        ]
    except ValueError as exc:
        return [*errors, str(exc)]
    key_field = _CORRECTION_COLLECTION_KEYS.get(collection)
    if key_field is not None and tokens and tokens[0] == key_field:
        errors.append("correction target 指向 Workflow-owned 身份字段")
    if (
        collection in {"coverage_decisions", "mechanism_decisions"}
        and tokens
        and tokens[0] == "test_case_keys"
    ):
        errors.append("correction target 指向 Workflow-owned 派生字段")
    if tokens and tokens[-1] == "repo_id":
        errors.append("correction target 指向 Workflow-owned repo_id 字段")
    return errors


def validate_closure_correction_contract(
    closure_task: ClosureTask,
    original_result: UnitSemanticResult,
    result: UnitSemanticResult,
) -> list[str]:
    """Validate v2 Closure decisions against exact before/after snapshots."""

    if getattr(closure_task, "review_contract_version", "1.0") != "2.0":
        return []

    errors: list[str] = []
    targets = list(getattr(closure_task, "correction_targets", []))
    target_by_id: dict[tuple[str, str], Any] = {}
    for target in targets:
        finding_key = getattr(target, "finding_key", None)
        correction_id = getattr(target, "correction_id", None)
        identity = (finding_key, correction_id)
        if not all(isinstance(value, str) and value for value in identity):
            errors.append(
                "Closure correction target 的 finding_key/correction_id 必须为非空字符串"
            )
            continue
        if identity in target_by_id:
            errors.append(
                "Closure correction_targets 包含重复编号："
                f"{finding_key}/{correction_id}"
            )
            continue
        target_by_id[identity] = target

        ref = getattr(target, "target", None)
        if getattr(ref, "unit_id", None) != closure_task.unit.unit_id:
            errors.append(
                f"Closure correction target {finding_key}/{correction_id} 的 "
                f"unit_id={getattr(ref, 'unit_id', None)!r} 与 task unit "
                f"{closure_task.unit.unit_id!r} 不一致"
            )
        try:
            actual_before = resolve_correction_target(original_result, ref)
        except ValueError as exc:
            errors.append(
                f"Closure correction target {finding_key}/{correction_id} 无法解析：{exc}"
            )
            continue
        identity_errors = correction_target_identity_errors(original_result, ref)
        errors.extend(
            f"Closure correction target {finding_key}/{correction_id} 无效：{message}"
            for message in identity_errors
        )
        declared_before = _snapshot_data(getattr(target, "before", None))
        if declared_before != actual_before:
            errors.append(
                f"Closure correction target {finding_key}/{correction_id} 的 "
                f"before 与 original_result 不一致："
                f"declared={declared_before!r} actual={actual_before!r}"
            )

    decision_by_id: dict[tuple[str, str | None], Any] = {}
    for decision in result.review_finding_decisions:
        identity = (decision.finding_key, getattr(decision, "correction_id", None))
        if identity in decision_by_id:
            errors.append(
                "Closure review_finding_decisions 包含重复编号："
                f"{identity[0]}/{identity[1]}"
            )
            continue
        decision_by_id[identity] = decision

    expected_ids = set(target_by_id)
    actual_ids = set(decision_by_id)
    missing = expected_ids - actual_ids
    extra = actual_ids - expected_ids
    if missing or extra:
        errors.append(
            "Closure v2 decision 集合必须与 correction_targets 完全一致："
            f"missing={_format_correction_ids(missing)} "
            f"extra={_format_correction_ids(extra)}"
        )

    resolved_new_keys: set[str] = set()
    for identity in sorted(expected_ids & actual_ids):
        target = target_by_id[identity]
        decision = decision_by_id[identity]
        ref = getattr(target, "target", None)
        try:
            before = resolve_correction_target(original_result, ref)
        except ValueError:
            continue
        label = f"{identity[0]}/{identity[1]}"
        ref_data = _as_mapping(ref, "correction target")
        missing_object_target = (
            ref_data.get("collection") != "result"
            and ref_data.get("object_key") is None
            and ref_data.get("field_path") is None
        )
        resolved_object_key = getattr(decision, "resolved_object_key", None)
        if missing_object_target and decision.disposition == "incorporated":
            if not isinstance(resolved_object_key, str) or not resolved_object_key:
                errors.append(
                    f"Closure correction {label} 新增整个对象时，"
                    "incorporated decision 必须填写 resolved_object_key"
                )
                continue
            resolved_ref = {
                **ref_data,
                "object_key": resolved_object_key,
            }
            resolved_before = resolve_correction_target(
                original_result,
                resolved_ref,
            )
            after = resolve_correction_target(result, resolved_ref)
            if resolved_before["exists"]:
                errors.append(
                    f"Closure correction {label} 的 resolved_object_key="
                    f"{resolved_object_key!r} 在 original_result 中已经存在"
                )
            if not after["exists"]:
                errors.append(
                    f"Closure correction {label} 的 resolved_object_key="
                    f"{resolved_object_key!r} 未出现在 Closure result 中"
                )
            if resolved_object_key in resolved_new_keys:
                errors.append(
                    f"Closure correction {label} 复用了另一个新增对象的 resolved_object_key="
                    f"{resolved_object_key!r}"
                )
            resolved_new_keys.add(resolved_object_key)
            changed = not resolved_before["exists"] and after["exists"]
        else:
            after = resolve_correction_target(result, ref)
            changed = before != after
            if resolved_object_key is not None:
                errors.append(
                    f"Closure correction {label} 只有新增整个对象并标记 "
                    "incorporated 时才允许 resolved_object_key"
                )
        if decision.disposition == "incorporated" and not changed:
            errors.append(
                f"Closure correction {label} 标记 incorporated，"
                "但目标 before 与 after 完全相同"
            )
        if decision.disposition == "incorporated":
            assertions = getattr(target, "assertions", [])
            if assertions:
                if missing_object_target:
                    assertion_root = after.get("value") if after.get("exists") else None
                elif ref_data.get("collection") == "result":
                    assertion_root = _as_mapping(result, "Closure result")
                else:
                    assertion_root = after.get("value") if ref_data.get("field_path") is None else resolve_correction_target(
                        result,
                        {**ref_data, "field_path": None},
                    ).get("value")
                for assertion in assertions:
                    assertion_error = _correction_assertion_error(
                        assertion,
                        assertion_root,
                    )
                    if assertion_error:
                        errors.append(f"Closure correction {label} assertion 未满足：{assertion_error}")
        elif decision.disposition in {"dismissed", "unresolved"} and changed:
            errors.append(
                f"Closure correction {label} 标记 {decision.disposition}，"
                "但目标 before 与 after 已发生变化"
            )
        if decision.disposition == "dismissed" and not decision.evidence:
            errors.append(
                f"Closure correction {label} 标记 dismissed，必须提供反证 evidence"
            )
    return errors


def snapshot_correction_target(
    result: UnitSemanticResult | Mapping[str, Any],
    ref: Any,
) -> ValueSnapshot:
    """Public resolver used by Closure task construction and finalization."""

    return ValueSnapshot.model_validate(resolve_correction_target(result, ref))


def validate_closure_corrections(
    task: ClosureTask,
    original_result: UnitSemanticResult,
    closure_result: UnitSemanticResult,
) -> list[str]:
    """Public v2 Closure contract validator."""

    return validate_closure_correction_contract(
        task,
        original_result,
        closure_result,
    )


def _as_mapping(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    raise ValueError(f"{label} 必须是 JSON 对象")


def _snapshot_value(value: Any, field_path: str | None) -> dict[str, Any]:
    if field_path is None:
        return {"exists": True, "value": deepcopy(value)}
    current = value
    for encoded_token in field_path[1:].split("/"):
        token = _decode_json_pointer_token(encoded_token)
        if isinstance(current, Mapping):
            if token not in current:
                return {"exists": False, "value": None}
            current = current[token]
            continue
        if isinstance(current, list):
            if token == "-":
                raise ValueError("correction target field_path 不支持 '-' append token")
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise ValueError(
                    "correction target field_path 的数组 token 必须是规范非负索引"
                )
            index = int(token)
            if index >= len(current):
                return {"exists": False, "value": None}
            current = current[index]
            continue
        return {"exists": False, "value": None}
    return {"exists": True, "value": deepcopy(current)}


def _correction_assertion_error(
    assertion: CorrectionAssertion,
    value: Any,
) -> str | None:
    snapshot = _snapshot_value(value, assertion.json_pointer)
    operator = assertion.operator
    expected = assertion.expected
    if operator == "exists":
        return None if snapshot["exists"] else f"{assertion.json_pointer} 不存在"
    if operator == "absent":
        return f"{assertion.json_pointer} 仍然存在" if snapshot["exists"] else None
    if not snapshot["exists"]:
        return f"{assertion.json_pointer} 不存在"
    actual = snapshot["value"]
    if operator == "equals" and actual == expected:
        return None
    if operator == "contains":
        if isinstance(actual, (list, str, dict)) and expected in actual:
            return None
    if operator == "not_contains":
        if isinstance(actual, (list, str, dict)) and expected not in actual:
            return None
    return f"{assertion.json_pointer}: actual={actual!r}, expected={expected!r}, operator={operator}"


def _decode_json_pointer_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            decoded.append(char)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise ValueError(
                "correction target field_path 包含无效 RFC 6901 '~' 转义"
            )
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _snapshot_data(value: Any) -> dict[str, Any]:
    data = _as_mapping(value, "before snapshot")
    return {
        "exists": data.get("exists"),
        "value": deepcopy(data.get("value")),
    }


def _format_correction_ids(values: set[tuple[str, str | None]]) -> list[str]:
    return sorted(
        f"{finding_key}/{correction_id}"
        for finding_key, correction_id in values
    )


def unit_submission_warnings(
    task: AnalysisTask,
    result: UnitSemanticResult,
    selected_inputs: dict,
    review_findings: list[ReviewFinding] | None = None,
) -> list[str]:
    """Return deterministic submission warnings without changing workflow state."""
    errors = _reference_warnings(result)
    errors.extend(_evidence_scope_warnings(task, result))
    errors.extend(_review_decision_evidence_warnings(
        task,
        result,
        review_findings,
    ))

    asset_items = selected_inputs.get("asset_items", {})
    coverage_gaps = selected_inputs.get("coverage_gaps", [])
    mechanisms = selected_inputs.get("defect_mechanisms", {})
    errors.extend(_check_decisions(
        "input_decisions",
        set(asset_items),
        [item.item_id for item in result.input_decisions],
    ))
    errors.extend(_check_decisions(
        "coverage_decisions",
        {item["coverage_id"] for item in coverage_gaps},
        [item.coverage_id for item in result.coverage_decisions],
    ))
    errors.extend(_check_decisions(
        "mechanism_decisions",
        set(mechanisms),
        [item.mechanism_id for item in result.mechanism_decisions],
    ))
    item_types = {
        item_id: item.get("item_type") for item_id, item in asset_items.items()
    }
    item_types.update({item_id: "historical_defect" for item_id in mechanisms})
    item_types.update({
        item["coverage_id"]: "coverage" for item in coverage_gaps
    })
    for case in result.test_cases:
        unsupported_basis = _unsupported_basis(case, item_types)
        if unsupported_basis:
            errors.append(
                f"测试用例 {case.case_key} 声明的 basis 没有对应链接："
                f"unsupported={unsupported_basis}"
            )
    return errors


def _evidence_scope_warnings(
    task: AnalysisTask,
    result: UnitSemanticResult,
) -> list[str]:
    warnings: list[str] = []
    allowed_paths = {
        path.replace("\\", "/").strip("/")
        for path in [*task.unit.source_scope, *task.unit.context_scope]
    }
    out_of_scope: dict[tuple[str, str], list[int]] = {}
    for evidence in _all_evidence(result, include_review_decisions=False):
        normalized_path = evidence.path.replace("\\", "/").strip("/")
        if evidence.repo_id != task.unit.repo_id or normalized_path not in allowed_paths:
            out_of_scope.setdefault(
                (evidence.repo_id, evidence.path), []
            ).append(evidence.line_start)
        if evidence.line_end is not None and evidence.line_end < evidence.line_start:
            warnings.append(
                "源码证据行号范围无效："
                f"{evidence.repo_id}:{evidence.path}:"
                f"{evidence.line_start}-{evidence.line_end}"
            )
    for (repo_id, path), lines in out_of_scope.items():
        warnings.append(
            "源码证据不属于当前分析单元："
            f"{repo_id}:{path}；lines={sorted(set(lines))[:12]} "
            f"occurrences={len(lines)}；allowed_repo={task.unit.repo_id} "
            f"allowed_paths={sorted(allowed_paths)}"
        )
    return warnings


def _review_decision_evidence_warnings(
    task: AnalysisTask,
    result: UnitSemanticResult,
    review_findings: list[ReviewFinding] | None,
) -> list[str]:
    warnings: list[str] = []
    unit_paths = {
        path.replace("\\", "/").strip("/")
        for path in [*task.unit.source_scope, *task.unit.context_scope]
    }
    finding_paths: dict[str, set[tuple[str, str]]] = {}
    for finding in review_findings or []:
        finding_paths[finding.finding_key] = {
            (
                evidence.repo_id,
                evidence.path.replace("\\", "/").strip("/"),
            )
            for evidence in finding.evidence
        }
    for decision in result.review_finding_decisions:
        allowed_finding_paths = finding_paths.get(decision.finding_key, set())
        invalid: dict[tuple[str, str], list[int]] = {}
        for evidence in decision.evidence:
            normalized_path = evidence.path.replace("\\", "/").strip("/")
            belongs_to_unit = (
                evidence.repo_id == task.unit.repo_id
                and normalized_path in unit_paths
            )
            belongs_to_finding = (
                evidence.repo_id,
                normalized_path,
            ) in allowed_finding_paths
            if not belongs_to_unit and not belongs_to_finding:
                invalid.setdefault(
                    (evidence.repo_id, evidence.path), []
                ).append(evidence.line_start)
            if (
                evidence.line_end is not None
                and evidence.line_end < evidence.line_start
            ):
                warnings.append(
                    "复核裁决证据行号范围无效："
                    f"{evidence.repo_id}:{evidence.path}:"
                    f"{evidence.line_start}-{evidence.line_end}"
                )
        for (repo_id, path), lines in invalid.items():
            warnings.append(
                f"复核裁决 {decision.finding_key} 使用了未授权证据："
                f"{repo_id}:{path}；lines={sorted(set(lines))[:12]}；"
                "只允许当前单元路径或对应 review finding 已冻结的证据路径"
            )
    return warnings


def _check_decisions(name: str, expected: set[str], actual: list[str]) -> list[str]:
    warnings = []
    if len(actual) != len(set(actual)):
        warnings.append(f"{name} 包含重复编号")
    unknown = set(actual) - expected
    if unknown:
        warnings.append(f"{name} 引用了当前任务不存在的编号：{sorted(unknown)}")
    missing = expected - set(actual)
    if missing:
        warnings.append(f"{name} 未记录全部可选处理项：missing={sorted(missing)}")
    return warnings


def _check_decision_membership(
    name: str,
    expected: set[str],
    actual: list[str],
) -> list[str]:
    warnings = []
    unknown = set(actual) - expected
    if unknown:
        warnings.append(f"{name} 引用了当前任务不存在的编号：{sorted(unknown)}")
    missing = expected - set(actual)
    if missing:
        warnings.append(f"{name} 未记录全部可选处理项：missing={sorted(missing)}")
    return warnings


def _reference_warnings(result: UnitSemanticResult) -> list[str]:
    warnings: list[str] = []
    keyed = {
        "flow_key": [item.flow_key for item in result.flows],
        "risk_key": [item.risk_key for item in result.risks],
        "case_key": [item.case_key for item in result.test_cases],
        "review_decision_key": [
            (item.finding_key, getattr(item, "correction_id", None))
            for item in result.review_finding_decisions
        ],
    }
    for name, values in keyed.items():
        if len(values) != len(set(values)):
            warnings.append(f"{name} 包含重复编号")

    known_flows = set(keyed["flow_key"])
    known_risks = set(keyed["risk_key"])
    for flow in result.flows:
        step_keys = [step.step_key for step in flow.steps]
        if len(step_keys) != len(set(step_keys)):
            warnings.append(f"流程 {flow.flow_key} 的 step_key 包含重复编号")
        known_steps = set(step_keys)
        missing_step_keys: set[str] = set()
        for edge in flow.edges:
            missing_step_keys.update({
                key
                for key in (edge.source_step_key, edge.target_step_key)
                if key not in known_steps
            })
        if missing_step_keys:
            warnings.append(
                f"流程 {flow.flow_key} 的 edge 引用了未知 step_key："
                f"{sorted(missing_step_keys)}"
            )
    for case in result.test_cases:
        unknown_flows = set(case.covered_flow_keys) - known_flows
        if unknown_flows:
            warnings.append(
                f"测试用例 {case.case_key} 引用了未知 flow_key："
                f"{sorted(unknown_flows)}"
            )
        unknown_risks = set(case.linked_risk_keys) - known_risks
        if unknown_risks:
            warnings.append(
                f"测试用例 {case.case_key} 引用了未知 risk_key："
                f"{sorted(unknown_risks)}"
            )
    return warnings


def _unsupported_basis(case, item_types: dict[str, str | None]) -> list[str]:
    type_to_basis = {
        "coverage": "coverage",
        "requirement": "requirement",
        "design": "design",
        "historical_defect": "defect_mechanism",
    }
    linked_types = {
        item_types[item_id]
        for item_id in case.linked_input_ids
        if item_id in item_types
    }
    supported = {
        name for item_type, name in type_to_basis.items()
        if item_type in linked_types
    }
    if case.covered_flow_keys:
        supported.add("code_flow")
    if case.linked_risk_keys:
        supported.add("risk")
    return [basis for basis in case.basis if basis not in supported]


def _all_evidence(
    result: UnitSemanticResult,
    *,
    include_review_decisions: bool = True,
):
    for flow in result.flows:
        for step in flow.steps:
            yield from step.evidence
    for decision in result.input_decisions:
        yield from decision.evidence
    for decision in result.mechanism_decisions:
        yield from decision.evidence
    for risk in result.risks:
        yield from risk.evidence
        yield from risk.unreachable_evidence
    for scenario in result.scenarios:
        yield from scenario.evidence
    if include_review_decisions:
        for decision in result.review_finding_decisions:
            yield from decision.evidence
