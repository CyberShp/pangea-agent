from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pangea_agent.models.analysis import AnalysisTask, UnitSemanticResult


def analysis_obligations(
    task: AnalysisTask,
    result: UnitSemanticResult,
    inventory: Mapping[str, Any],
    selected_inputs: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return deterministic missing/invalid semantic references for one Analysis unit."""

    issues: list[dict[str, str]] = []
    known_flows = {item.flow_key for item in result.flows}
    known_risks = {item.risk_key for item in result.risks}
    known_scenarios = {item.scenario_key for item in result.scenarios}
    expected_branches = _expected_branch_ids(task, inventory)
    expected_coverage = {
        str(item["coverage_id"])
        for item in selected_inputs.get("coverage_gaps", [])
        if isinstance(item, Mapping) and item.get("coverage_id")
    }
    known_inputs = {
        *selected_inputs.get("asset_items", {}),
        *selected_inputs.get("defect_mechanisms", {}),
        *expected_coverage,
    }

    _decision_set_issues(
        issues,
        "branch_decision",
        expected_branches,
        [item.branch_id for item in result.branch_decisions],
    )
    _decision_set_issues(
        issues,
        "coverage_decision",
        expected_coverage,
        [item.coverage_id for item in result.coverage_decisions],
    )
    _duplicate_key_issues(
        issues,
        "scenario",
        [item.scenario_key for item in result.scenarios],
    )

    for decision in result.branch_decisions:
        if decision.flow_key not in known_flows:
            _add(
                issues,
                "unknown_flow",
                decision.branch_id,
                f"BranchDecision {decision.branch_id} 引用了不存在的 flow_key={decision.flow_key}",
            )
        _scenario_reference_issues(
            issues,
            "branch_decision",
            decision.branch_id,
            decision.disposition,
            decision.scenario_keys,
            known_scenarios,
        )

    for decision in result.coverage_decisions:
        _scenario_reference_issues(
            issues,
            "coverage_decision",
            decision.coverage_id,
            decision.disposition,
            decision.scenario_keys,
            known_scenarios,
        )

    for scenario in result.scenarios:
        for flow_key in scenario.covered_flow_keys:
            if flow_key not in known_flows:
                _add(issues, "unknown_flow", scenario.scenario_key, f"Scenario {scenario.scenario_key} 引用了不存在的 flow_key={flow_key}")
        for branch_id in scenario.branch_ids:
            if branch_id not in expected_branches:
                _add(issues, "unknown_branch", scenario.scenario_key, f"Scenario {scenario.scenario_key} 引用了当前单元不存在的 branch_id={branch_id}")
        for coverage_id in scenario.coverage_ids:
            if coverage_id not in expected_coverage:
                _add(issues, "unknown_coverage", scenario.scenario_key, f"Scenario {scenario.scenario_key} 引用了当前单元不存在的 coverage_id={coverage_id}")
        for risk_key in scenario.linked_risk_keys:
            if risk_key not in known_risks:
                _add(issues, "unknown_risk", scenario.scenario_key, f"Scenario {scenario.scenario_key} 引用了不存在的 risk_key={risk_key}")
        for item_id in scenario.linked_input_ids:
            if item_id not in known_inputs:
                _add(issues, "unknown_input", scenario.scenario_key, f"Scenario {scenario.scenario_key} 引用了当前任务不存在的 input_id={item_id}")

    risks_with_scenario = {
        risk_key
        for scenario in result.scenarios
        for risk_key in scenario.linked_risk_keys
    }
    for risk in result.risks:
        if risk.test_disposition == "test_required" and risk.risk_key not in risks_with_scenario:
            _add(
                issues,
                "missing_risk_scenario",
                risk.risk_key,
                f"Risk {risk.risk_key} 标记为 test_required，但没有 Scenario 关联该 risk_key",
            )

    for case in result.test_cases:
        for scenario_key in case.scenario_keys:
            if scenario_key not in known_scenarios:
                _add(issues, "unknown_scenario", case.case_key, f"TestCase {case.case_key} 引用了不存在的 scenario_key={scenario_key}")

    return issues


def _expected_branch_ids(task: AnalysisTask, inventory: Mapping[str, Any]) -> set[str]:
    owned_paths = set(task.unit.source_scope)
    return {
        str(branch["branch_id"])
        for file_item in inventory.get("files", [])
        if isinstance(file_item, Mapping)
        and file_item.get("repo_id") == task.unit.repo_id
        and file_item.get("path") in owned_paths
        for branch in file_item.get("branches", [])
        if isinstance(branch, Mapping) and branch.get("branch_id")
    }


def _decision_set_issues(
    issues: list[dict[str, str]],
    kind: str,
    expected: set[str],
    actual: list[str],
) -> None:
    _duplicate_key_issues(issues, kind, actual)
    actual_set = set(actual)
    for item_id in sorted(expected - actual_set):
        _add(issues, f"missing_{kind}", item_id, f"{kind} 未处理当前任务项：{item_id}")
    for item_id in sorted(actual_set - expected):
        _add(issues, f"unknown_{kind}", item_id, f"{kind} 引用了当前任务不存在的编号：{item_id}")


def _duplicate_key_issues(
    issues: list[dict[str, str]],
    kind: str,
    values: list[str],
) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    for value in sorted(duplicates):
        _add(issues, f"duplicate_{kind}", value, f"{kind} 编号重复：{value}")


def _scenario_reference_issues(
    issues: list[dict[str, str]],
    kind: str,
    item_id: str,
    disposition: str,
    scenario_keys: list[str],
    known_scenarios: set[str],
) -> None:
    if disposition in {"scenario_mapped", "merged"} and not scenario_keys:
        _add(issues, "missing_scenario_link", item_id, f"{kind} {item_id} 的 disposition={disposition}，但 scenario_keys 为空")
    for scenario_key in scenario_keys:
        if scenario_key not in known_scenarios:
            _add(issues, "unknown_scenario", item_id, f"{kind} {item_id} 引用了不存在的 scenario_key={scenario_key}")


def _add(issues: list[dict[str, str]], code: str, item_id: str, message: str) -> None:
    issues.append({"code": code, "item_id": item_id, "message": message})
