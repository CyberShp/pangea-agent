from __future__ import annotations

from pangea_agent.models.analysis import AnalysisTask, UnitSemanticResult


def validate_unit_result(
    task: AnalysisTask,
    result: UnitSemanticResult,
    selected_inputs: dict,
) -> None:
    asset_items = selected_inputs.get("asset_items", {})
    coverage_gaps = selected_inputs.get("coverage_gaps", [])
    mechanisms = selected_inputs.get("defect_mechanisms", {})
    expected_inputs = set(asset_items)
    expected_coverage = {item["coverage_id"] for item in coverage_gaps}
    expected_mechanisms = set(mechanisms)

    _normalize_mechanical_fields(
        result,
        expected_inputs=expected_inputs,
        expected_coverage=expected_coverage,
        expected_mechanisms=expected_mechanisms,
        asset_items=asset_items,
    )

    decision_errors = [
        error
        for error in [
            _exact_id_error(
                "input_decisions", expected_inputs,
                [item.item_id for item in result.input_decisions],
            ),
            _exact_id_error(
                "coverage_decisions", expected_coverage,
                [item.coverage_id for item in result.coverage_decisions],
            ),
            _exact_id_error(
                "mechanism_decisions", expected_mechanisms,
                [item.mechanism_id for item in result.mechanism_decisions],
            ),
        ]
        if error is not None
    ]
    if decision_errors:
        raise ValueError("；".join(decision_errors))

    allowed_paths = set(task.unit.source_scope) | set(task.unit.context_scope)
    for evidence in _all_evidence(result):
        if evidence.repo_id != task.unit.repo_id or evidence.path not in allowed_paths:
            raise ValueError(
                "源码证据不属于当前分析单元："
                f"{evidence.repo_id}:{evidence.path}:{evidence.line_start}"
            )
    known_inputs = expected_inputs | expected_coverage | expected_mechanisms
    item_types = {item_id: item.get("item_type") for item_id, item in asset_items.items()}
    item_types.update({item_id: "historical_defect" for item_id in mechanisms})
    item_types.update({item_id: "coverage" for item_id in expected_coverage})
    case_errors: list[str] = []
    for case in result.test_cases:
        unknown_inputs = set(case.linked_input_ids) - known_inputs
        if unknown_inputs:
            case_errors.append(
                f"测试用例 {case.case_key} 引用了未知输入：{sorted(unknown_inputs)}"
            )
        linked_types = {
            item_types[item_id]
            for item_id in case.linked_input_ids
            if item_id in item_types
        }
        expected_basis = {
            "coverage": "coverage",
            "requirement": "requirement",
            "design": "design",
            "defect_mechanism": "historical_defect",
        }
        for basis in case.basis:
            required_type = expected_basis.get(basis)
            if required_type and required_type not in linked_types:
                case_errors.append(
                    f"测试用例 {case.case_key} basis={basis} 缺少对应输入编号"
                )
            if basis == "risk" and not case.linked_risk_keys:
                case_errors.append(f"测试用例 {case.case_key} basis=risk 缺少 risk_key")
    if case_errors:
        raise ValueError("；".join(case_errors))
    for flow in result.flows:
        step_keys = {step.step_key for step in flow.steps}
        for edge in flow.edges:
            if edge.source_step_key not in step_keys or edge.target_step_key not in step_keys:
                raise ValueError(f"流程 {flow.flow_key} 的 edge 引用了未知 step_key")


def _exact_id_error(name: str, expected: set[str], actual: list[str]) -> str | None:
    if len(actual) != len(set(actual)):
        return f"{name} 包含重复编号"
    actual_set = set(actual)
    if actual_set != expected:
        return (
            f"{name} 没有逐项处理输入："
            f"missing={sorted(expected - actual_set)} extra={sorted(actual_set - expected)}"
        )
    return None


def _normalize_mechanical_fields(
    result: UnitSemanticResult,
    *,
    expected_inputs: set[str],
    expected_coverage: set[str],
    expected_mechanisms: set[str],
    asset_items: dict,
) -> None:
    if not expected_inputs:
        result.input_decisions = []
    if not expected_coverage:
        result.coverage_decisions = []
    if not expected_mechanisms:
        result.mechanism_decisions = []

    item_types = {item_id: item.get("item_type") for item_id, item in asset_items.items()}
    item_types.update({item_id: "historical_defect" for item_id in expected_mechanisms})
    item_types.update({item_id: "coverage" for item_id in expected_coverage})
    basis_types = {
        "coverage": "coverage",
        "requirement": "requirement",
        "design": "design",
        "defect_mechanism": "historical_defect",
    }
    for case in result.test_cases:
        linked_types = {
            item_types[item_id]
            for item_id in case.linked_input_ids
            if item_id in item_types
        }
        valid_basis = [
            basis
            for basis in case.basis
            if (
                (basis == "risk" and bool(case.linked_risk_keys))
                or (basis in basis_types and basis_types[basis] in linked_types)
            )
        ]
        if valid_basis:
            case.basis = valid_basis
        elif case.linked_risk_keys:
            case.basis = ["risk"]
        elif not case.linked_input_ids:
            case.basis = ["code_flow"]


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
