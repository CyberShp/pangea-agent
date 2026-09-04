from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from pangea_agent.models.analysis import AnalysisTask, UnitSemanticResult


READY_SCENARIO_STATES = {"blackbox_ready", "graybox_ready"}
SCENARIO_MAPPED_DISPOSITIONS = {"scenario_mapped", "merged"}


def build_analysis_obligation_manifest(
    task: AnalysisTask,
    inventory: Mapping[str, Any],
    selected_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze deterministic object identities for one Analysis unit."""

    branches = []
    owned_paths = set(task.unit.source_scope)
    for file_item in inventory.get("files", []):
        if not isinstance(file_item, Mapping):
            continue
        if file_item.get("repo_id") != task.unit.repo_id:
            continue
        if file_item.get("path") not in owned_paths:
            continue
        for branch in file_item.get("branches", []):
            if not isinstance(branch, Mapping) or not branch.get("branch_id"):
                continue
            branches.append({
                "branch_id": str(branch["branch_id"]),
                "path": str(file_item["path"]),
                "line": branch.get("line"),
                "kind": branch.get("kind"),
            })
    coverage = [
        {"coverage_id": str(item["coverage_id"])}
        for item in selected_inputs.get("coverage_gaps", [])
        if isinstance(item, Mapping) and item.get("coverage_id")
    ]
    inputs = sorted(
        str(item_id)
        for item_id in selected_inputs.get("asset_items", {})
        if item_id
    )
    mechanisms = sorted(
        str(item_id)
        for item_id in selected_inputs.get("defect_mechanisms", {})
        if item_id
    )
    manifest = {
        "schema_version": 1,
        "run_id": task.run_id,
        "action_id": task.action_id,
        "unit_id": task.unit.unit_id,
        "source_scope": list(task.unit.source_scope),
        "counts": {
            "branch_decisions": len(branches),
            "input_decisions": len(inputs),
            "coverage_decisions": len(coverage),
            "mechanism_decisions": len(mechanisms),
        },
        "branch_obligations": sorted(branches, key=lambda item: item["branch_id"]),
        "input_obligations": [{"item_id": item_id} for item_id in inputs],
        "coverage_obligations": sorted(coverage, key=lambda item: item["coverage_id"]),
        "mechanism_obligations": [{"mechanism_id": item_id} for item_id in mechanisms],
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return manifest


def analysis_obligations(
    task: AnalysisTask,
    result: UnitSemanticResult,
    inventory: Mapping[str, Any],
    selected_inputs: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return deterministic missing/invalid references and declared-state conflicts."""

    issues: list[dict[str, str]] = []
    known_flows = {item.flow_key for item in result.flows}
    known_risks = {item.risk_key for item in result.risks}
    scenarios_by_key = {item.scenario_key: item for item in result.scenarios}
    known_scenarios = set(scenarios_by_key)
    branch_decisions_by_id = {
        item.branch_id: item for item in result.branch_decisions
    }
    coverage_decisions_by_id = {
        item.coverage_id: item for item in result.coverage_decisions
    }
    expected_branches = _expected_branch_ids(task, inventory)
    expected_coverage = {
        str(item["coverage_id"])
        for item in selected_inputs.get("coverage_gaps", [])
        if isinstance(item, Mapping) and item.get("coverage_id")
    }
    coverage_records = {
        str(item["coverage_id"]): item
        for item in selected_inputs.get("coverage_gaps", [])
        if isinstance(item, Mapping) and item.get("coverage_id")
    }
    zero_targets_by_coverage = {
        coverage_id: _zero_coverage_targets(record)
        for coverage_id, record in coverage_records.items()
    }
    known_inputs = {
        *selected_inputs.get("asset_items", {}),
        *selected_inputs.get("defect_mechanisms", {}),
        *expected_coverage,
    }
    if (
        not known_inputs
        and result.unresolved
        and not result.review_finding_decisions
    ):
        _add(
            issues,
            "unresolved_without_allowed_input",
            task.unit.unit_id,
            "当前单元没有可供顶层 unresolved 引用的 selected input 或 Coverage ID；"
            "证据缺口应保留在对应 Branch/Risk/Scenario 的 developer_confirm disposition",
        )
    claims_by_coverage: dict[str, list[tuple[Any, str]]] = {
        coverage_id: [] for coverage_id in expected_coverage
    }
    all_claims_by_coverage: dict[str, list[Any]] = {
        coverage_id: [] for coverage_id in expected_coverage
    }
    cases_by_scenario = {
        scenario_key: [
            case
            for case in result.test_cases
            if scenario_key in case.scenario_keys
        ]
        for scenario_key in known_scenarios
    }

    for case in result.test_cases:
        seen_claims: set[tuple[str, str]] = set()
        known_claim_coverage_ids: set[str] = set()
        for claim in case.direct_coverage_claims:
            claim_key = (claim.coverage_id, claim.target)
            if claim_key in seen_claims:
                _add(
                    issues,
                    "duplicate_coverage_claim",
                    case.case_key,
                    "TestCase "
                    f"{case.case_key} 重复声明 Coverage target="
                    f"{claim.coverage_id}:{claim.target}",
                )
                continue
            seen_claims.add(claim_key)

            record = coverage_records.get(claim.coverage_id)
            if record is None:
                _add(
                    issues,
                    "unknown_coverage_claim",
                    case.case_key,
                    f"TestCase {case.case_key} 声明了当前任务不存在的 "
                    f"coverage_id={claim.coverage_id}",
                )
                continue

            known_claim_coverage_ids.add(claim.coverage_id)
            all_claims_by_coverage[claim.coverage_id].append(case)
            target_counts = _coverage_target_counts(record)
            if claim.target not in target_counts:
                _add(
                    issues,
                    "unknown_coverage_claim_target",
                    case.case_key,
                    f"TestCase {case.case_key} 对 coverage_id="
                    f"{claim.coverage_id} 声明了不适用的 target={claim.target}",
                )
                continue
            if target_counts[claim.target] != 0:
                _add(
                    issues,
                    "nonzero_coverage_claim_target",
                    case.case_key,
                    f"TestCase {case.case_key} 声明 coverage_id="
                    f"{claim.coverage_id} 的 target={claim.target}，"
                    f"但冻结 count={target_counts[claim.target]} 并非 0",
                )
                continue
            claims_by_coverage[claim.coverage_id].append((case, claim.target))

        linked_coverage_ids = set(case.linked_input_ids) & expected_coverage
        if linked_coverage_ids != known_claim_coverage_ids:
            _add(
                issues,
                "coverage_claim_link_mismatch",
                case.case_key,
                f"TestCase {case.case_key} 的 Coverage linked_input_ids "
                "必须与 direct_coverage_claims.coverage_id 集合一致："
                f"linked={sorted(linked_coverage_ids)} "
                f"claims={sorted(known_claim_coverage_ids)}",
            )
        if case.direct_coverage_claims and "coverage" not in case.basis:
            _add(
                issues,
                "coverage_claim_missing_basis",
                case.case_key,
                f"TestCase {case.case_key} 声明了 direct_coverage_claims，"
                "但 basis 未包含 coverage",
            )

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
        if decision.disposition in SCENARIO_MAPPED_DISPOSITIONS:
            ready_scenario_keys = {
                scenario_key
                for scenario_key in decision.scenario_keys
                if scenarios_by_key.get(scenario_key)
                and scenarios_by_key[scenario_key].readiness in READY_SCENARIO_STATES
            }
            if not ready_scenario_keys:
                _add(
                    issues,
                    "missing_ready_branch_scenario",
                    decision.branch_id,
                    f"BranchDecision {decision.branch_id} 的 disposition={decision.disposition}，但没有 ready Scenario",
                )
            for scenario_key in decision.scenario_keys:
                scenario = scenarios_by_key.get(scenario_key)
                if scenario is None:
                    continue
                if scenario.readiness not in READY_SCENARIO_STATES:
                    _add(
                        issues,
                        "branch_scenario_readiness_conflict",
                        decision.branch_id,
                        f"BranchDecision {decision.branch_id} 的 disposition={decision.disposition}，但引用 Scenario {scenario_key} 的 readiness={scenario.readiness}",
                    )
                if decision.branch_id not in scenario.branch_ids:
                    _add(
                        issues,
                        "branch_scenario_mismatch",
                        decision.branch_id,
                        f"BranchDecision {decision.branch_id} 指向 Scenario {scenario_key}，但该 Scenario.branch_ids 未反向包含此 branch_id",
                    )
        elif decision.disposition == "developer_confirm":
            for scenario_key in decision.scenario_keys:
                scenario = scenarios_by_key.get(scenario_key)
                if scenario is None:
                    continue
                if scenario.readiness != "developer_confirm":
                    _add(
                        issues,
                        "branch_scenario_readiness_conflict",
                        decision.branch_id,
                        f"BranchDecision {decision.branch_id} 声明 developer_confirm，但引用 Scenario {scenario_key} 的 readiness={scenario.readiness}",
                    )
                if decision.branch_id not in scenario.branch_ids:
                    _add(
                        issues,
                        "branch_scenario_mismatch",
                        decision.branch_id,
                        f"BranchDecision {decision.branch_id} 指向 Scenario {scenario_key}，但该 Scenario.branch_ids 未反向包含此 branch_id",
                    )
        elif decision.scenario_keys:
            _add(
                issues,
                "branch_scenario_disposition_conflict",
                decision.branch_id,
                f"BranchDecision {decision.branch_id} 的 disposition={decision.disposition}，但仍引用 Scenario={decision.scenario_keys}",
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
        if decision.disposition in SCENARIO_MAPPED_DISPOSITIONS:
            ready_scenario_keys = {
                scenario_key
                for scenario_key in decision.scenario_keys
                if scenarios_by_key.get(scenario_key)
                and scenarios_by_key[scenario_key].readiness in READY_SCENARIO_STATES
            }
            if not ready_scenario_keys:
                _add(
                    issues,
                    "missing_ready_coverage_scenario",
                    decision.coverage_id,
                    f"CoverageDecision {decision.coverage_id} 的 disposition={decision.disposition}，但没有 ready Scenario",
                )
            ready_claims = [
                (case, target)
                for case, target in claims_by_coverage.get(
                    decision.coverage_id, []
                )
                if set(case.scenario_keys) & ready_scenario_keys
            ]
            if not ready_claims:
                _add(
                    issues,
                    "missing_coverage_case",
                    decision.coverage_id,
                    f"CoverageDecision {decision.coverage_id} 的 disposition={decision.disposition}，但没有 TestCase 通过 direct_coverage_claims 声明零覆盖目标并引用其 ready Scenario",
                )
            claimed_ready_targets = {target for _, target in ready_claims}
            for target in sorted(
                zero_targets_by_coverage.get(decision.coverage_id, set())
                - claimed_ready_targets
            ):
                _add(
                    issues,
                    "missing_coverage_target_claim",
                    decision.coverage_id,
                    f"CoverageDecision {decision.coverage_id} 的零覆盖目标 "
                    f"target={target} 没有 ready Scenario 下的 TestCase claim",
                )
            for scenario_key in decision.scenario_keys:
                scenario = scenarios_by_key.get(scenario_key)
                if scenario is not None and decision.coverage_id not in scenario.coverage_ids:
                    _add(
                        issues,
                        "coverage_scenario_mismatch",
                        decision.coverage_id,
                        f"CoverageDecision {decision.coverage_id} 指向 Scenario {scenario_key}，但该 Scenario.coverage_ids 未反向包含此 coverage_id",
                    )
        elif all_claims_by_coverage.get(decision.coverage_id):
            _add(
                issues,
                "coverage_claim_disposition_conflict",
                decision.coverage_id,
                f"CoverageDecision {decision.coverage_id} 的 disposition="
                f"{decision.disposition}，但仍有 TestCase 声明 direct Coverage claims",
            )

    for scenario in result.scenarios:
        if scenario.readiness in READY_SCENARIO_STATES:
            missing_fields = [
                name
                for name, value in (
                    ("business_entry", scenario.business_entry),
                    ("actions", scenario.actions),
                    ("external_oracles", scenario.external_oracles),
                )
                if not value
            ]
            if missing_fields:
                _add(
                    issues,
                    "incomplete_ready_scenario",
                    scenario.scenario_key,
                    "Scenario 声明为可执行，但缺少必需业务字段："
                    f"{','.join(missing_fields)}",
                )
            if not cases_by_scenario.get(scenario.scenario_key):
                _add(
                    issues,
                    "missing_ready_scenario_case",
                    scenario.scenario_key,
                    f"Scenario {scenario.scenario_key} 的 readiness={scenario.readiness}，但没有正式 TestCase 直接引用该 Scenario",
                )
        for flow_key in scenario.covered_flow_keys:
            if flow_key not in known_flows:
                _add(
                    issues,
                    "unknown_flow",
                    scenario.scenario_key,
                    f"Scenario {scenario.scenario_key} 引用了不存在的 flow_key={flow_key}",
                )
        for branch_id in scenario.branch_ids:
            if branch_id not in expected_branches:
                _add(
                    issues,
                    "unknown_branch",
                    scenario.scenario_key,
                    f"Scenario {scenario.scenario_key} 引用了当前单元不存在的 branch_id={branch_id}",
                )
                continue
            decision = branch_decisions_by_id.get(branch_id)
            if (
                decision is not None
                and scenario.scenario_key not in decision.scenario_keys
            ):
                _add(
                    issues,
                    "scenario_branch_mismatch",
                    scenario.scenario_key,
                    f"Scenario {scenario.scenario_key} 声明覆盖 branch_id={branch_id}，但对应 BranchDecision 未反向映射到该 Scenario",
                )
        for coverage_id in scenario.coverage_ids:
            if coverage_id not in expected_coverage:
                _add(
                    issues,
                    "unknown_coverage",
                    scenario.scenario_key,
                    f"Scenario {scenario.scenario_key} 引用了当前单元不存在的 coverage_id={coverage_id}",
                )
                continue
            decision = coverage_decisions_by_id.get(coverage_id)
            if (
                decision is not None
                and (
                    decision.disposition not in SCENARIO_MAPPED_DISPOSITIONS
                    or scenario.scenario_key not in decision.scenario_keys
                )
            ):
                _add(
                    issues,
                    "scenario_coverage_mismatch",
                    scenario.scenario_key,
                    f"Scenario {scenario.scenario_key} 声明覆盖 coverage_id={coverage_id}，但对应 CoverageDecision 未反向映射到该 Scenario",
                )
        for risk_key in scenario.linked_risk_keys:
            if risk_key not in known_risks:
                _add(
                    issues,
                    "unknown_risk",
                    scenario.scenario_key,
                    f"Scenario {scenario.scenario_key} 引用了不存在的 risk_key={risk_key}",
                )
        for item_id in scenario.linked_input_ids:
            if item_id not in known_inputs:
                _add(
                    issues,
                    "unknown_input",
                    scenario.scenario_key,
                    f"Scenario {scenario.scenario_key} 引用了当前任务不存在的 input_id={item_id}",
                )

    scenarios_by_risk: dict[str, set[str]] = {}
    for scenario in result.scenarios:
        for risk_key in scenario.linked_risk_keys:
            scenarios_by_risk.setdefault(risk_key, set()).add(scenario.scenario_key)

    ready_scenarios_by_risk = {
        risk_key: {
            scenario_key
            for scenario_key in scenario_keys
            if scenarios_by_key.get(scenario_key)
            and scenarios_by_key[scenario_key].readiness in READY_SCENARIO_STATES
        }
        for risk_key, scenario_keys in scenarios_by_risk.items()
    }

    cases_by_risk: dict[str, list] = {}
    for case in result.test_cases:
        for scenario_key in case.scenario_keys:
            if scenario_key not in known_scenarios:
                _add(
                    issues,
                    "unknown_scenario",
                    case.case_key,
                    f"TestCase {case.case_key} 引用了不存在的 scenario_key={scenario_key}",
                )
            elif scenarios_by_key[scenario_key].readiness == "developer_confirm":
                _add(
                    issues,
                    "case_uses_unready_scenario",
                    case.case_key,
                    f"TestCase {case.case_key} 引用了 readiness=developer_confirm 的 Scenario {scenario_key}",
                )
        for claim in case.direct_coverage_claims:
            item_id = claim.coverage_id
            if item_id not in expected_coverage:
                continue
            decision = coverage_decisions_by_id.get(item_id)
            if decision is None:
                continue
            if decision.disposition not in SCENARIO_MAPPED_DISPOSITIONS:
                _add(
                    issues,
                    "coverage_case_disposition_conflict",
                    case.case_key,
                    f"TestCase {case.case_key} 直接关联 coverage_id={item_id}，但对应 CoverageDecision.disposition={decision.disposition}",
                )
                continue
            if not set(case.scenario_keys) & set(decision.scenario_keys):
                _add(
                    issues,
                    "coverage_case_scenario_mismatch",
                    case.case_key,
                    f"TestCase {case.case_key} 直接关联 coverage_id={item_id}，但它与对应 CoverageDecision 没有共享 Scenario",
                )
        for risk_key in case.linked_risk_keys:
            cases_by_risk.setdefault(risk_key, []).append(case)

    for risk in result.risks:
        scenario_keys = scenarios_by_risk.get(risk.risk_key, set())
        ready_scenario_keys = ready_scenarios_by_risk.get(risk.risk_key, set())
        linked_cases = cases_by_risk.get(risk.risk_key, [])
        ready_cases = [
            case
            for case in linked_cases
            if set(case.scenario_keys) & ready_scenario_keys
        ]

        if risk.test_disposition == "test_required":
            if not scenario_keys:
                _add(
                    issues,
                    "missing_risk_scenario",
                    risk.risk_key,
                    f"Risk {risk.risk_key} 标记为 test_required，但没有 Scenario 关联该 risk_key",
                )
            elif not ready_scenario_keys:
                _add(
                    issues,
                    "missing_ready_risk_scenario",
                    risk.risk_key,
                    f"Risk {risk.risk_key} 标记为 test_required，但关联 Scenario 均未达到 blackbox_ready/graybox_ready",
                )
            elif not ready_cases:
                _add(
                    issues,
                    "missing_risk_case",
                    risk.risk_key,
                    f"Risk {risk.risk_key} 标记为 test_required，但没有 TestCase 同时关联该 Risk 和 ready Scenario",
                )
            continue

        if risk.test_disposition == "developer_confirm":
            if ready_scenario_keys:
                _add(
                    issues,
                    "developer_confirm_has_ready_scenario",
                    risk.risk_key,
                    f"Risk {risk.risk_key} 声明 developer_confirm，但已关联 ready Scenario={sorted(ready_scenario_keys)}",
                )
            if linked_cases:
                _add(
                    issues,
                    "developer_confirm_has_case",
                    risk.risk_key,
                    f"Risk {risk.risk_key} 声明 developer_confirm，但已有正式 TestCase 关联",
                )
            continue

        if scenario_keys:
            _add(
                issues,
                "unreachable_has_scenario",
                risk.risk_key,
                f"Risk {risk.risk_key} 声明不可达，但仍关联 Scenario={sorted(scenario_keys)}",
            )
        if linked_cases:
            _add(
                issues,
                "unreachable_has_case",
                risk.risk_key,
                f"Risk {risk.risk_key} 声明不可达，但仍关联正式 TestCase",
            )
        if not risk.unreachable_reason or not risk.unreachable_evidence:
            _add(
                issues,
                "incomplete_unreachable_risk",
                risk.risk_key,
                f"Risk {risk.risk_key} 的不可达决定必须包含原因和源码证据",
            )

    return issues


def _coverage_target_counts(record: Mapping[str, Any]) -> dict[str, Any]:
    coverage_type = record.get("coverage_type")
    if coverage_type == "function":
        return {"function_execution": record.get("count")}
    if coverage_type == "branch":
        return {
            "branch_true_outcome": record.get("true_count"),
            "branch_false_outcome": record.get("false_count"),
        }
    return {}


def _zero_coverage_targets(record: Mapping[str, Any]) -> set[str]:
    return {
        target
        for target, count in _coverage_target_counts(record).items()
        if count == 0
    }


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
    if disposition in SCENARIO_MAPPED_DISPOSITIONS and not scenario_keys:
        _add(
            issues,
            "missing_scenario_link",
            item_id,
            f"{kind} {item_id} 的 disposition={disposition}，但 scenario_keys 为空",
        )
    for scenario_key in scenario_keys:
        if scenario_key not in known_scenarios:
            _add(
                issues,
                "unknown_scenario",
                item_id,
                f"{kind} {item_id} 引用了不存在的 scenario_key={scenario_key}",
            )


def _add(issues: list[dict[str, str]], code: str, item_id: str, message: str) -> None:
    issues.append({"code": code, "item_id": item_id, "message": message})
