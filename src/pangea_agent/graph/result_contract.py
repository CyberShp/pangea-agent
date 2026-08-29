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
    for evidence in _all_evidence(result):
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


def assert_unit_references(result: UnitSemanticResult) -> None:
    """Reject only broken internal identifiers, never semantic conclusions."""
    errors = _reference_warnings(result)
    if errors:
        raise ValueError("结果内部编号引用不完整：" + " | ".join(errors[:24]))


def assert_unit_submission(
    task: AnalysisTask,
    result: UnitSemanticResult,
    selected_inputs: dict,
) -> None:
    """Reject mechanically inconsistent ownership and declared links."""
    errors = _reference_warnings(result)
    errors.extend(_evidence_scope_warnings(task, result))

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

    if errors:
        raise ValueError("结果结构关联不完整：" + " | ".join(errors[:24]))


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
    for evidence in _all_evidence(result):
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


def _reference_warnings(result: UnitSemanticResult) -> list[str]:
    warnings: list[str] = []
    keyed = {
        "flow_key": [item.flow_key for item in result.flows],
        "risk_key": [item.risk_key for item in result.risks],
        "case_key": [item.case_key for item in result.test_cases],
        "finding_key": [
            item.finding_key for item in result.review_finding_decisions
        ],
    }
    for name, values in keyed.items():
        if len(values) != len(set(values)):
            warnings.append(f"{name} 包含重复编号")

    known_flows = set(keyed["flow_key"])
    known_risks = set(keyed["risk_key"])
    known_cases = set(keyed["case_key"])
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
    for decision in [*result.coverage_decisions, *result.mechanism_decisions]:
        unknown_cases = set(decision.test_case_keys) - known_cases
        if unknown_cases:
            warnings.append(
                "处理决定引用了未知 case_key："
                f"{sorted(unknown_cases)}"
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
