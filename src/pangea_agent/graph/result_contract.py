from __future__ import annotations

from pangea_agent.models.analysis import AnalysisTask, UnitSemanticResult


def validate_unit_result(
    task: AnalysisTask,
    result: UnitSemanticResult,
    selected_inputs: dict,
) -> list[str]:
    """Validate deterministic references without judging Agent semantics."""
    asset_items = selected_inputs.get("asset_items", {})
    coverage_gaps = selected_inputs.get("coverage_gaps", [])
    mechanisms = selected_inputs.get("defect_mechanisms", {})
    expected_inputs = set(asset_items)
    expected_coverage = {item["coverage_id"] for item in coverage_gaps}
    expected_mechanisms = set(mechanisms)

    warnings = []
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
    for evidence in _all_evidence(result):
        if evidence.repo_id != task.unit.repo_id or evidence.path not in allowed_paths:
            raise ValueError(
                "源码证据不属于当前分析单元："
                f"{evidence.repo_id}:{evidence.path}:{evidence.line_start}"
            )

    known_inputs = expected_inputs | expected_coverage | expected_mechanisms
    item_types = {
        item_id: item.get("item_type") for item_id, item in asset_items.items()
    }
    item_types.update({item_id: "historical_defect" for item_id in mechanisms})
    item_types.update({item_id: "coverage" for item_id in expected_coverage})
    for case in result.test_cases:
        unknown_inputs = set(case.linked_input_ids) - known_inputs
        if unknown_inputs:
            raise ValueError(
                f"测试用例 {case.case_key} 引用了未知输入：{sorted(unknown_inputs)}"
            )
        derived_basis = _derived_basis(case, item_types)
        if case.basis != derived_basis:
            warnings.append(
                f"测试用例 {case.case_key} basis 已由真实关联确定："
                f"{case.basis} -> {derived_basis}"
            )
            case.basis = derived_basis

    return warnings


def _check_decisions(name: str, expected: set[str], actual: list[str]) -> list[str]:
    if len(actual) != len(set(actual)):
        raise ValueError(f"{name} 包含重复编号")
    unknown = set(actual) - expected
    if unknown:
        raise ValueError(f"{name} 引用了当前任务不存在的编号：{sorted(unknown)}")
    missing = expected - set(actual)
    if not missing:
        return []
    return [f"{name} 未记录全部可选处理项：missing={sorted(missing)}"]


def _derived_basis(case, item_types: dict[str, str | None]) -> list[str]:
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
    basis = [
        name for item_type, name in type_to_basis.items()
        if item_type in linked_types
    ]
    if case.linked_risk_keys:
        basis.append("risk")
    return basis or ["code_flow"]


def _all_evidence(result: UnitSemanticResult):
    for flow in result.flows:
        for step in flow.steps:
            yield from step.evidence
    for decision in result.input_decisions:
        yield from decision.evidence
    for decision in result.mechanism_decisions:
        yield from decision.evidence
    for risk in result.risks:
        yield from risk.evidence
    for decision in result.review_finding_decisions:
        yield from decision.evidence
