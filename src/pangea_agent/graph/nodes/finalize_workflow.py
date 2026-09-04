from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.analysis_obligations import analysis_obligations
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.result_contract import (
    resolve_correction_target,
    risk_test_obligations,
    validate_closure_corrections,
    validate_unit_result,
)
from pangea_agent.graph.workflow_store import (
    analysis_task_path,
    comparison_review_aggregate_path,
    closure_task_path,
    comparison_review_task_path,
    load_progress,
    run_directory,
    save_progress,
    validated_result_path,
)
from pangea_agent.models.analysis import (
    AnalysisTask,
    ClosureTask,
    ComparisonReviewResult,
    IndependentReviewResult,
    UnitSemanticResult,
)
from pangea_agent.report import write_reports


READY_SCENARIO_STATES = {"blackbox_ready", "graybox_ready"}


def _evidence(item) -> dict:
    end = item.line_end or item.line_start
    location = f"{item.repo_id}:{item.path}:{item.line_start}"
    if end != item.line_start:
        location += f"-{end}"
    return {"location": location, "observation": item.observation}


def _mermaid(flow) -> str:
    lines = ["flowchart TD"]
    for step in flow.steps:
        label = step.label.replace('"', "'").replace("[", "(").replace("]", ")")
        lines.append(f'    {step.step_key}["{label}"]')
    for edge in flow.edges:
        condition = edge.condition.replace('"', "'") if edge.condition else None
        arrow = f' -->|"{condition}"| ' if condition else " --> "
        lines.append(f"    {edge.source_step_key}{arrow}{edge.target_step_key}")
    return "\n".join(lines)


def _zero_coverage_targets(record: dict) -> list[str]:
    if record.get("coverage_type") == "function":
        return ["function_execution"] if record.get("count") == 0 else []
    if record.get("coverage_type") != "branch":
        return []
    targets: list[str] = []
    if record.get("true_count") == 0:
        targets.append("branch_true_outcome")
    if record.get("false_count") == 0:
        targets.append("branch_false_outcome")
    return targets


def _coverage_claim_case_keys(
    result: UnitSemanticResult,
) -> dict[tuple[str, str], list[str]]:
    claims: dict[tuple[str, str], list[str]] = defaultdict(list)
    for case in result.test_cases:
        for claim in case.direct_coverage_claims:
            key = (claim.coverage_id, claim.target)
            if case.case_key not in claims[key]:
                claims[key].append(case.case_key)
    return dict(claims)


def _load_final_unit_result(state: PangeaState, progress, unit_id: str) -> UnitSemanticResult:
    closure_action_id = f"{state['run_id']}:closure:{unit_id}"
    action_id = (
        closure_action_id
        if unit_id in progress.completed_closure_units
        else f"{state['run_id']}:analysis:{unit_id}"
    )
    return UnitSemanticResult.model_validate(
        read_json(validated_result_path(state, action_id))
    )


def _deduplicate_degradations(items: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for item in items:
        kind = str(item.get("kind", "agent_result_warning"))
        message = str(item.get("message", "结果存在待确认项"))
        key = (kind, message)
        action_ids = item.get("action_ids") or [item.get("action_id")]
        entry = grouped.setdefault(
            key,
            {
                "kind": kind,
                "message": message,
                "action_ids": [],
                "occurrence_count": 0,
            },
        )
        for action_id in action_ids:
            if action_id and action_id not in entry["action_ids"]:
                entry["action_ids"].append(action_id)
        entry["occurrence_count"] += int(item.get("occurrence_count", 1))
    return list(grouped.values())


def _analysis_worker_id(progress, unit_id: str) -> str:
    action = progress.actions.get(f"{progress.run_id}:analysis:{unit_id}")
    return action.task_id if action and action.task_id else "未绑定"


def _developer_confirm_items(unit_id: str, result: UnitSemanticResult) -> list[dict]:
    items: list[dict] = []
    items.extend(
        {
            "stage": "developer_confirm",
            "unit_id": unit_id,
            "item_type": "branch",
            "item_id": decision.branch_id,
            "reason": decision.reason,
        }
        for decision in result.branch_decisions
        if decision.disposition == "developer_confirm"
    )
    items.extend(
        {
            "stage": "developer_confirm",
            "unit_id": unit_id,
            "item_type": "coverage",
            "item_id": decision.coverage_id,
            "reason": decision.reason,
        }
        for decision in result.coverage_decisions
        if decision.disposition == "developer_confirm"
    )
    items.extend(
        {
            "stage": "developer_confirm",
            "unit_id": unit_id,
            "item_type": "risk",
            "item_id": risk.risk_key,
            "reason": "风险需要开发确认业务入口、构造方式或外部 Oracle",
        }
        for risk in result.risks
        if risk.test_disposition == "developer_confirm"
    )
    items.extend(
        {
            "stage": "developer_confirm",
            "unit_id": unit_id,
            "item_type": "scenario",
            "item_id": scenario.scenario_key,
            "reason": "场景尚未达到可执行 readiness",
        }
        for scenario in result.scenarios
        if scenario.readiness == "developer_confirm"
    )
    return items


def _closure_correction_projection(
    state: PangeaState,
    progress,
    results: dict[str, UnitSemanticResult],
) -> tuple[dict[tuple[str, str], list[dict]], set[str], list[dict]]:
    projected: dict[tuple[str, str], list[dict]] = defaultdict(list)
    v2_units: set[str] = set()
    diagnostics: list[dict] = []
    for unit_id in progress.completed_closure_units:
        task_payload = read_json(closure_task_path(state, unit_id))
        if task_payload.get("review_contract_version") != "2.0":
            continue
        v2_units.add(unit_id)
        closure_task = ClosureTask.model_validate(task_payload)
        original_result = UnitSemanticResult.model_validate(
            read_json(Path(closure_task.original_result_path))
        )
        contract_errors = validate_closure_corrections(
            closure_task,
            original_result,
            results[unit_id],
        )
        contract_invalid = bool(contract_errors)
        diagnostics.extend(
            {
                "stage": "closure_finding",
                "unit_id": unit_id,
                "reason": f"Targeted Closure v2 contract 无效：{message}",
            }
            for message in contract_errors[:24]
        )
        targets = task_payload.get("correction_targets", [])
        decisions = [
            item.model_dump(mode="json")
            for item in results[unit_id].review_finding_decisions
        ]
        decisions_by_id: dict[
            tuple[str | None, str | None], list[dict]
        ] = defaultdict(list)
        for decision in decisions:
            decisions_by_id[
                (decision.get("finding_key"), decision.get("correction_id"))
            ].append(decision)

        target_id_counts: dict[tuple[str | None, str | None], int] = defaultdict(int)
        for item in targets:
            target_id_counts[
                (item.get("finding_key"), item.get("correction_id"))
            ] += 1

        known_ids = {
            (item.get("finding_key"), item.get("correction_id"))
            for item in targets
            if item.get("finding_key") and item.get("correction_id")
        }
        for identity in decisions_by_id:
            if identity in known_ids:
                continue
            finding_key, correction_id = identity
            diagnostics.append(
                {
                    "stage": "closure_finding",
                    "unit_id": unit_id,
                    "finding_key": finding_key,
                    "correction_id": correction_id,
                    "reason": (
                        "Targeted Closure decision 引用了未知 correction_id"
                        if correction_id
                        else "Targeted Closure v2 decision 缺少 correction_id"
                    ),
                }
            )

        for item in targets:
            finding_key = item.get("finding_key")
            correction_id = item.get("correction_id")
            identity = (finding_key, correction_id)
            linked_decisions = decisions_by_id.get(identity, [])
            duplicate_target = target_id_counts.get(identity, 0) > 1
            if contract_invalid:
                status = "invalid"
            elif duplicate_target or len(linked_decisions) > 1:
                status = "duplicate"
            elif not linked_decisions:
                status = "missing"
            else:
                status = linked_decisions[0]["disposition"]
            target = item.get("target", {})
            before = item.get("before", {"exists": False, "value": None})
            resolved_object_key = (
                linked_decisions[0].get("resolved_object_key")
                if len(linked_decisions) == 1
                else None
            )
            resolved_target = target
            if (
                target.get("collection") != "result"
                and target.get("object_key") is None
                and target.get("field_path") is None
                and resolved_object_key
            ):
                resolved_target = {
                    **target,
                    "object_key": resolved_object_key,
                }
            try:
                after = resolve_correction_target(results[unit_id], resolved_target)
            except ValueError as exc:
                after = {"exists": False, "value": None}
                status = "invalid"
                diagnostics.append(
                    {
                        "stage": "closure_finding",
                        "unit_id": unit_id,
                        "finding_key": finding_key,
                        "correction_id": correction_id,
                        "reason": f"Targeted Closure correction target 无法解析：{exc}",
                    }
                )
            entry = {
                "unit_id": unit_id,
                "finding_key": finding_key,
                "correction_id": correction_id,
                "target": target,
                "resolved_object_key": resolved_object_key,
                "required_state": item.get("required_state"),
                "before": before,
                "after": after,
                "changed": before != after,
                "disposition": (
                    linked_decisions[0]["disposition"]
                    if len(linked_decisions) == 1 and not duplicate_target
                    else None
                ),
                "status": status,
                "decisions": linked_decisions,
            }
            projected[(finding_key, unit_id)].append(entry)
            if status in {"missing", "duplicate"}:
                diagnostics.append(
                    {
                        "stage": "closure_finding",
                        "unit_id": unit_id,
                        "finding_key": finding_key,
                        "correction_id": correction_id,
                        "reason": (
                            "Targeted Closure 缺少 correction target decision"
                            if status == "missing"
                            else "Targeted Closure correction target 或 decision 重复"
                        ),
                    }
                )
    return dict(projected), v2_units, diagnostics


def _comparison_audit_projection(
    state: PangeaState,
    comparison_review: ComparisonReviewResult | None,
) -> tuple[dict | None, list[dict]]:
    if comparison_review is None:
        return None, []
    task_payload = read_json(comparison_review_task_path(state))
    if task_payload.get("review_contract_version") != "2.0":
        return None, []

    targets = task_payload.get("required_analysis_audits", [])
    decisions = [
        item.model_dump(mode="json")
        for item in getattr(comparison_review, "analysis_audit_decisions", [])
    ]
    decisions_by_id: dict[str | None, list[dict]] = defaultdict(list)
    for decision in decisions:
        decisions_by_id[decision.get("audit_id")].append(decision)
    target_id_counts: dict[str, int] = defaultdict(int)
    for target in targets:
        target_id_counts[target.get("audit_id", "")] += 1

    diagnostics: list[dict] = []
    known_ids = {
        target.get("audit_id")
        for target in targets
        if target.get("audit_id")
    }
    unmatched_decisions = []
    for audit_id in decisions_by_id:
        if audit_id in known_ids:
            continue
        unmatched_decisions.extend(decisions_by_id[audit_id])
        diagnostics.append(
            {
                "stage": "review_contract",
                "audit_id": audit_id,
                "reason": "Comparison audit decision 引用了未知 audit_id",
            }
        )

    joined = []
    counts: dict[str, int] = defaultdict(int)
    for target in targets:
        audit_id = target.get("audit_id")
        linked_decisions = decisions_by_id.get(audit_id, [])
        if target_id_counts.get(audit_id or "", 0) > 1 or len(linked_decisions) > 1:
            status = "duplicate"
        elif not linked_decisions:
            status = "missing"
        else:
            status = linked_decisions[0]["disposition"]
        counts[status] += 1
        joined.append(
            {
                **target,
                "status": status,
                "decisions": linked_decisions,
            }
        )
        if status in {"missing", "duplicate"}:
            diagnostics.append(
                {
                    "stage": "review_contract",
                    "audit_id": audit_id,
                    "reason": (
                        "Comparison 缺少 required analysis audit decision"
                        if status == "missing"
                        else "Comparison analysis audit target 或 decision 重复"
                    ),
                }
            )
    return {
        "targets": joined,
        "counts": {
            "total": len(targets),
            "accepted": counts["accepted"],
            "finding": counts["finding"],
            "missing": counts["missing"],
            "duplicate": counts["duplicate"],
            "unmatched": len(unmatched_decisions),
        },
        "unmatched_decisions": unmatched_decisions,
    }, diagnostics


def _review_finding_projection(
    review: IndependentReviewResult | None,
    comparison_review: ComparisonReviewResult | None,
    results: dict[str, UnitSemanticResult],
    completed_closure_units: set[str],
    correction_targets: dict[tuple[str, str], list[dict]] | None = None,
    v2_closure_units: set[str] | None = None,
    correction_diagnostics: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    correction_targets = correction_targets or {}
    v2_closure_units = v2_closure_units or set()
    comparison_decisions: dict[str, list] = defaultdict(list)
    for decision in (
        comparison_review.independent_finding_decisions
        if comparison_review
        else []
    ):
        comparison_decisions[decision.finding_key].append(decision)
    closure_decisions: dict[tuple[str, str], list] = defaultdict(list)
    for unit_id, result in results.items():
        if unit_id not in completed_closure_units:
            continue
        for decision in result.review_finding_decisions:
            closure_decisions[(decision.finding_key, unit_id)].append(decision)
    active: list[dict] = []
    history: list[dict] = []
    diagnostics: list[dict] = list(correction_diagnostics or [])

    if review is None:
        diagnostics.append(
            {
                "stage": "review_contract",
                "reason": "Independent Review 结果缺失",
            }
        )
    if comparison_review is None:
        diagnostics.append(
            {
                "stage": "review_contract",
                "reason": "Comparison Review 结果缺失",
            }
        )
    for unit_id, result in results.items():
        if unit_id in completed_closure_units or not result.review_finding_decisions:
            continue
        diagnostics.append(
            {
                "stage": "final_integrity",
                "unit_id": unit_id,
                "reason": (
                    "未完成 Targeted Closure 的单元包含 "
                    "review_finding_decisions，未用于终结 finding"
                ),
            }
        )

    for source, finding in (
        [("independent", item) for item in (review.findings if review else [])]
        + [
            ("comparison", item)
            for item in (comparison_review.findings if comparison_review else [])
        ]
    ):
        finding_comparison_decisions = (
            comparison_decisions.get(finding.finding_key, [])
            if source == "independent"
            else []
        )
        comparison_disposition = (
            finding_comparison_decisions[0].disposition
            if len(finding_comparison_decisions) == 1
            else "ambiguous" if finding_comparison_decisions else None
        )
        per_unit = []
        correction_target_decisions = []
        terminal_dispositions = []
        missing_units = []
        ambiguous_units = []
        invalid_target_contract = False
        for unit_id in finding.affected_unit_ids:
            decisions = closure_decisions.get((finding.finding_key, unit_id), [])
            if unit_id in v2_closure_units:
                targets = correction_targets.get((finding.finding_key, unit_id), [])
                if not targets:
                    missing_units.append(unit_id)
                correction_target_decisions.extend(targets)
                target_statuses = [item["status"] for item in targets]
                invalid_target_contract = invalid_target_contract or any(
                    status in {"missing", "duplicate", "invalid"}
                    for status in target_statuses
                )
                terminal_dispositions.extend(
                    status
                    for status in target_statuses
                    if status in {"incorporated", "dismissed", "unresolved"}
                )
                per_unit.extend(
                    {"unit_id": unit_id, **decision.model_dump(mode="json")}
                    for decision in decisions
                )
                continue
            if not decisions:
                missing_units.append(unit_id)
                continue
            if len(decisions) > 1:
                ambiguous_units.append(unit_id)
            terminal_dispositions.extend(
                decision.disposition for decision in decisions
            )
            per_unit.extend(
                {"unit_id": unit_id, **decision.model_dump(mode="json")}
                for decision in decisions
            )

        if source == "independent":
            if len(finding_comparison_decisions) > 1:
                final_status = "active"
                diagnostics.append(
                    {
                        "stage": "review_contract",
                        "finding_key": finding.finding_key,
                        "reason": "Independent finding 存在重复的 Comparison Review decision",
                    }
                )
                missing_units = []
                ambiguous_units = []
            elif comparison_disposition == "dismissed":
                final_status = "dismissed"
                missing_units = []
                ambiguous_units = []
                invalid_target_contract = False
                terminal_dispositions = []
            elif comparison_disposition not in {"confirmed", "unresolved"}:
                final_status = "active"
                diagnostics.append(
                    {
                        "stage": "review_contract",
                        "finding_key": finding.finding_key,
                        "reason": "Independent finding 缺少 Comparison Review decision",
                    }
                )
                missing_units = []
            else:
                final_status = ""
        else:
            final_status = ""

        if not final_status and (
            ambiguous_units or missing_units or invalid_target_contract
        ):
            final_status = "active"
            diagnostics.extend(
                {
                    "stage": "closure_finding",
                    "unit_id": unit_id,
                    "finding_key": finding.finding_key,
                    "reason": "Targeted Closure 对该 finding 存在重复的最终 decision",
                }
                for unit_id in ambiguous_units
            )
            diagnostics.extend(
                {
                    "stage": "closure_finding",
                    "unit_id": unit_id,
                    "finding_key": finding.finding_key,
                    "reason": "Targeted Closure 缺少该 finding 的最终 decision",
                }
                for unit_id in missing_units
            )
        elif not final_status:
            dispositions = set(terminal_dispositions)
            if "unresolved" in dispositions:
                final_status = "unresolved"
            elif dispositions == {"incorporated"}:
                final_status = "incorporated"
            elif dispositions == {"dismissed"}:
                final_status = "dismissed"
            else:
                final_status = "resolved_mixed"

        history.append(
            {
                **finding.model_dump(mode="json"),
                "source": source,
                "comparison_disposition": comparison_disposition,
                "comparison_decisions": [
                    decision.model_dump(mode="json")
                    for decision in finding_comparison_decisions
                ],
                "closure_decisions": per_unit,
                "correction_target_decisions": correction_target_decisions,
                "final_status": final_status,
            }
        )
        if final_status in {"active", "unresolved"}:
            active.append(finding.model_dump(mode="json"))

    return active, history, diagnostics


def _completed_checks(
    test_cases: list[dict],
    scenarios: list[dict],
    progress,
    results: dict[str, UnitSemanticResult],
    review: IndependentReviewResult | None,
    comparison_review: ComparisonReviewResult | None,
    comparison_audit: dict | None,
) -> list[str]:
    branch_count = sum(len(result.branch_decisions) for result in results.values())
    coverage_count = sum(len(result.coverage_decisions) for result in results.values())
    mechanism_count = sum(len(result.mechanism_decisions) for result in results.values())
    checks = [
        f"分析单元：{len(progress.analysis_units)} 个；"
        f"已完成 {len(progress.completed_analysis_units)} 个",
        f"语义 decision：Branch {branch_count} / Coverage {coverage_count} / "
        f"Mechanism {mechanism_count}",
    ]
    if test_cases:
        ready_scenario_ids = {
            scenario["scenario_id"]
            for scenario in scenarios
            if scenario["readiness"] in READY_SCENARIO_STATES
        }
        traced_case_count = sum(
            bool(ready_scenario_ids.intersection(case["scenario_ids"]))
            for case in test_cases
        )
        checks.append(
            f"正式 TestCase：{len(test_cases)} 条；"
            f"其中 {traced_case_count} 条关联 ready Scenario"
        )
    else:
        developer_confirm_count = sum(
            scenario["readiness"] == "developer_confirm"
            for scenario in scenarios
        )
        if developer_confirm_count:
            checks.append(
                "正式 TestCase：0 条；"
                f"{developer_confirm_count} 个 Scenario 保持 developer_confirm"
            )
        elif scenarios:
            checks.append("正式 TestCase：0 条；本次 Scenario 未形成正式用例")
        else:
            checks.append("正式 TestCase：0 条；本次没有 Scenario")

    if progress.completed_closure_units:
        closure_counts: dict[str, int] = defaultdict(int)
        for unit_id in progress.completed_closure_units:
            for decision in results[unit_id].review_finding_decisions:
                closure_counts[decision.disposition] += 1
        checks.append(
            f"Targeted Closure：{len(progress.completed_closure_units)} 个单元；"
            f"finding decisions {sum(closure_counts.values())}（"
            f"incorporated {closure_counts['incorporated']} / "
            f"dismissed {closure_counts['dismissed']} / "
            f"unresolved {closure_counts['unresolved']}）"
        )
    else:
        checks.append("Targeted Closure：未触发")
    if review is None:
        checks.append("Independent Review：结果缺失")
    else:
        checks.append(f"Independent Review：findings {len(review.findings)}")
    if comparison_review is None:
        checks.append("Comparison Review：结果缺失")
    else:
        checks.append(
            "Comparison Review："
            f"decisions {len(comparison_review.independent_finding_decisions)} / "
            f"new findings {len(comparison_review.findings)}"
        )
    if comparison_audit is not None:
        counts = comparison_audit["counts"]
        checks.append(
            "Comparison audit："
            f"targets {counts['total']}（accepted {counts['accepted']} / "
            f"finding {counts['finding']} / missing {counts['missing']} / "
            f"duplicate {counts['duplicate']} / unmatched {counts['unmatched']}）"
        )
    return checks


def finalize_workflow(state: PangeaState) -> PangeaState:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("Run progress 不存在")
    run_dir = run_directory(state)
    source_manifest = read_json(run_dir / "inputs" / "source-manifest.json")
    inventory = read_json(run_dir / "inputs" / "inventory.json")
    coverage_gaps = read_json(run_dir / "inputs" / "coverage-gaps.json")
    zero_targets_by_coverage = {
        item["coverage_id"]: _zero_coverage_targets(item)
        for item in coverage_gaps
        if isinstance(item, dict) and item.get("coverage_id")
    }
    results = {
        unit.unit_id: _load_final_unit_result(state, progress, unit.unit_id)
        for unit in progress.analysis_units
    }

    risk_ids: dict[tuple[str, str], str] = {}
    scenario_ids: dict[tuple[str, str], str] = {}
    case_ids: dict[tuple[str, str], str] = {}

    risks: list[dict] = []
    scenarios: list[dict] = []
    test_cases: list[dict] = []
    flows: list[dict] = []
    input_decisions: list[dict] = []
    branch_decisions: list[dict] = []
    coverage_decisions: list[dict] = []
    mechanism_decisions: list[dict] = []
    unresolved: list[dict] = []

    for unit in progress.analysis_units:
        result = results[unit.unit_id]
        task = AnalysisTask.model_validate(
            read_json(analysis_task_path(state, unit.unit_id))
        )
        selected_inputs = read_json(Path(task.selected_inputs_path))
        integrity_issues = analysis_obligations(
            task,
            result,
            inventory,
            selected_inputs,
        )
        integrity_warnings = validate_unit_result(
            task,
            result,
            selected_inputs,
        )
        unresolved.extend(
            {
                "stage": "final_integrity",
                "unit_id": unit.unit_id,
                "reason": f"{item['code']} [{item['item_id']}]: {item['message']}",
            }
            for item in integrity_issues
        )
        unresolved.extend(
            {
                "stage": "final_integrity",
                "unit_id": unit.unit_id,
                "reason": warning,
            }
            for warning in integrity_warnings
        )
        unresolved.extend(_developer_confirm_items(unit.unit_id, result))
        unresolved.extend(
            {"unit_id": unit.unit_id, "reason": value}
            for value in result.unresolved
        )
        unresolved.extend(
            {
                "stage": "risk_test_coverage",
                "unit_id": unit.unit_id,
                "reason": obligation,
            }
            for obligation in risk_test_obligations(result)
        )

        for number, risk in enumerate(result.risks, 1):
            risk_id = f"R-{unit.unit_id}-{number:03d}"
            risk_ids[(unit.unit_id, risk.risk_key)] = risk_id
            risks.append(
                {
                    **risk.model_dump(
                        mode="json",
                        exclude={"risk_key", "evidence"},
                    ),
                    "risk_id": risk_id,
                    "unit_id": unit.unit_id,
                    "evidence": [_evidence(item) for item in risk.evidence],
                    "translation_status": "Uncovered",
                    "status": "identified",
                }
            )

        for number, scenario in enumerate(result.scenarios, 1):
            scenario_id = f"SCN-{unit.unit_id}-{number:03d}"
            scenario_ids[(unit.unit_id, scenario.scenario_key)] = scenario_id

        for number, case in enumerate(result.test_cases, 1):
            case_id = f"TC-{unit.unit_id}-{number:03d}"
            case_ids[(unit.unit_id, case.case_key)] = case_id

        for scenario in result.scenarios:
            scenario_id = scenario_ids[(unit.unit_id, scenario.scenario_key)]
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "unit_id": unit.unit_id,
                    "title": scenario.title,
                    "readiness": scenario.readiness,
                    "business_entry": scenario.business_entry,
                    "preconditions": scenario.preconditions,
                    "actions": scenario.actions,
                    "external_oracles": scenario.external_oracles,
                    "recovery": scenario.recovery,
                    "covered_flow_ids": [
                        f"F-{unit.unit_id}-{key}"
                        for key in scenario.covered_flow_keys
                    ],
                    "branch_ids": scenario.branch_ids,
                    "coverage_ids": scenario.coverage_ids,
                    "linked_risk_ids": [
                        risk_ids[(unit.unit_id, key)]
                        for key in scenario.linked_risk_keys
                        if (unit.unit_id, key) in risk_ids
                    ],
                    "linked_input_ids": scenario.linked_input_ids,
                    "evidence": [_evidence(item) for item in scenario.evidence],
                }
            )

        for case in result.test_cases:
            case_id = case_ids[(unit.unit_id, case.case_key)]
            unresolved_risk_keys = [
                key
                for key in case.linked_risk_keys
                if (unit.unit_id, key) not in risk_ids
            ]
            test_cases.append(
                {
                    "test_case_id": case_id,
                    "unit_id": unit.unit_id,
                    "title": case.title,
                    "case_type": case.level,
                    "basis": case.basis,
                    "scenario_ids": [
                        scenario_ids[(unit.unit_id, key)]
                        for key in case.scenario_keys
                        if (unit.unit_id, key) in scenario_ids
                    ],
                    "covered_flow_ids": [
                        f"F-{unit.unit_id}-{key}"
                        for key in case.covered_flow_keys
                    ],
                    "linked_input_ids": case.linked_input_ids,
                    "direct_coverage_claims": [
                        claim.model_dump(mode="json")
                        for claim in case.direct_coverage_claims
                    ],
                    "linked_risk_ids": [
                        risk_ids[(unit.unit_id, key)]
                        for key in case.linked_risk_keys
                        if (unit.unit_id, key) in risk_ids
                    ],
                    "unresolved_linked_risk_keys": unresolved_risk_keys,
                    "preconditions": case.preconditions,
                    "steps": [step.action for step in case.steps],
                    "expected_results": [
                        step.expected_result for step in case.steps
                    ],
                    "observability": case.observability,
                    "cleanup": case.cleanup,
                    "status": "ready",
                }
            )

        for flow in result.flows:
            flows.append(
                {
                    "flow_id": f"F-{unit.unit_id}-{flow.flow_key}",
                    "unit_id": unit.unit_id,
                    "title": flow.title,
                    "description": flow.summary,
                    "entry": flow.entry,
                    "steps": [
                        f"[{step.kind}] {step.label}"
                        for step in flow.steps
                    ],
                    "diagram": {
                        "nodes": [
                            {
                                "id": step.step_key,
                                "label": step.label,
                                "kind": step.kind,
                            }
                            for step in flow.steps
                        ],
                        "edges": [
                            edge.model_dump(mode="json")
                            for edge in flow.edges
                        ],
                    },
                    "mermaid": _mermaid(flow),
                    "evidence": [
                        _evidence(evidence)
                        for step in flow.steps
                        for evidence in step.evidence
                    ],
                }
            )

        input_decisions.extend(
            {"unit_id": unit.unit_id, **item.model_dump(mode="json")}
            for item in result.input_decisions
        )
        mechanism_case_keys = {
            item.mechanism_id: list(item.test_case_keys)
            for item in result.mechanism_decisions
        }
        coverage_claims = _coverage_claim_case_keys(result)

        branch_decisions.extend(
            {
                "unit_id": unit.unit_id,
                **item.model_dump(mode="json", exclude={"scenario_keys"}),
                "scenario_ids": [
                    scenario_ids[(unit.unit_id, key)]
                    for key in item.scenario_keys
                    if (unit.unit_id, key) in scenario_ids
                ],
            }
            for item in result.branch_decisions
        )

        for item in result.coverage_decisions:
            targets = list(zero_targets_by_coverage.get(item.coverage_id, []))
            target_test_case_ids = []
            claimed_case_keys: list[str] = []
            unresolved_case_keys: list[str] = []
            for target in targets:
                target_case_keys = coverage_claims.get(
                    (item.coverage_id, target),
                    [],
                )
                for case_key in target_case_keys:
                    if case_key not in claimed_case_keys:
                        claimed_case_keys.append(case_key)
                    if (
                        (unit.unit_id, case_key) not in case_ids
                        and case_key not in unresolved_case_keys
                    ):
                        unresolved_case_keys.append(case_key)
                target_test_case_ids.append(
                    {
                        "target": target,
                        "test_case_ids": [
                            case_ids[(unit.unit_id, case_key)]
                            for case_key in target_case_keys
                            if (unit.unit_id, case_key) in case_ids
                        ],
                    }
                )

            coverage_decisions.append(
                {
                    "unit_id": unit.unit_id,
                    **item.model_dump(
                        mode="json",
                        exclude={"scenario_keys", "test_case_keys"},
                    ),
                    "scenario_ids": [
                        scenario_ids[(unit.unit_id, key)]
                        for key in item.scenario_keys
                        if (unit.unit_id, key) in scenario_ids
                    ],
                    "test_case_ids": [
                        case_ids[(unit.unit_id, case_key)]
                        for case_key in claimed_case_keys
                        if (unit.unit_id, case_key) in case_ids
                    ],
                    "target_test_case_ids": target_test_case_ids,
                    "unresolved_test_case_keys": unresolved_case_keys,
                }
            )

        mechanism_decisions.extend(
            {
                "unit_id": unit.unit_id,
                **item.model_dump(
                    mode="json",
                    exclude={"test_case_keys", "evidence"},
                ),
                "test_case_ids": [
                    case_ids[(unit.unit_id, case_key)]
                    for case_key in mechanism_case_keys.get(item.mechanism_id, [])
                    if (unit.unit_id, case_key) in case_ids
                ],
                "unresolved_test_case_keys": [
                    case_key
                    for case_key in mechanism_case_keys.get(item.mechanism_id, [])
                    if (unit.unit_id, case_key) not in case_ids
                ],
                "evidence": [
                    _evidence(evidence)
                    for evidence in item.evidence
                ],
            }
            for item in result.mechanism_decisions
        )

        unresolved.extend(
            {
                "stage": "closure_finding",
                "unit_id": unit.unit_id,
                "finding_key": item.finding_key,
                "reason": item.conclusion,
            }
            for item in result.review_finding_decisions
            if (
                unit.unit_id in progress.completed_closure_units
                and item.disposition == "unresolved"
            )
        )

    scenarios_by_risk: dict[str, list[dict]] = defaultdict(list)
    for scenario in scenarios:
        for risk_id in scenario["linked_risk_ids"]:
            scenarios_by_risk[risk_id].append(scenario)

    cases_by_risk: dict[str, list[dict]] = defaultdict(list)
    for case in test_cases:
        for risk_id in case["linked_risk_ids"]:
            cases_by_risk[risk_id].append(case)

    for risk in risks:
        linked_scenarios = scenarios_by_risk.get(risk["risk_id"], [])
        ready_scenarios = [
            scenario
            for scenario in linked_scenarios
            if scenario["readiness"] in READY_SCENARIO_STATES
        ]
        ready_scenario_ids = {
            scenario["scenario_id"] for scenario in ready_scenarios
        }
        linked_cases = cases_by_risk.get(risk["risk_id"], [])
        ready_cases = [
            case
            for case in linked_cases
            if ready_scenario_ids.intersection(case["scenario_ids"])
        ]

        risk["scenario_ids"] = [
            scenario["scenario_id"] for scenario in linked_scenarios
        ]
        risk["test_case_ids"] = [
            case["test_case_id"] for case in ready_cases
        ]

        if risk.get("test_disposition") == "developer_confirm":
            risk["translation_status"] = "Developer-confirm"
        elif (
            risk.get("test_disposition")
            == "unreachable_from_supported_entry"
            and risk.get("unreachable_reason")
            and risk.get("unreachable_evidence")
        ):
            risk["translation_status"] = "Unreachable"
        elif ready_scenarios and ready_cases:
            risk["translation_status"] = "Test-ready"
        else:
            risk["translation_status"] = "Uncovered"

    planning = read_json(run_dir / "inputs" / "unit-plan.json")
    unresolved.extend(
        {"stage": "planning", "reason": value}
        for value in planning.get("unresolved", [])
    )

    review = None
    review_action_id = f"{state['run_id']}:review"
    if review_action_id in progress.actions:
        review = IndependentReviewResult.model_validate(
            read_json(validated_result_path(state, review_action_id))
        )
        unresolved.extend(
            {"stage": "review", "reason": value}
            for value in review.unresolved
        )

    comparison_review = None
    comparison_action_id = f"{state['run_id']}:comparison-review"
    if comparison_action_id in progress.actions:
        comparison_result_path = comparison_review_aggregate_path(state)
        if not comparison_result_path.is_file():
            comparison_result_path = validated_result_path(state, comparison_action_id)
        comparison_review = ComparisonReviewResult.model_validate(
            read_json(comparison_result_path)
        )
        unresolved.extend(
            {"stage": "review", "reason": value}
            for value in comparison_review.unresolved
        )

    comparison_audit, comparison_audit_diagnostics = _comparison_audit_projection(
        state,
        comparison_review,
    )
    (
        correction_targets,
        v2_closure_units,
        correction_diagnostics,
    ) = _closure_correction_projection(state, progress, results)

    (
        active_review_findings,
        review_finding_history,
        review_finding_diagnostics,
    ) = _review_finding_projection(
        review,
        comparison_review,
        results,
        set(progress.completed_closure_units),
        correction_targets,
        v2_closure_units,
        correction_diagnostics + comparison_audit_diagnostics,
    )

    semantic_review_diagnostics = [
        item for item in review_finding_diagnostics if item["stage"] == "review"
    ]
    workflow_review_diagnostics = [
        item for item in review_finding_diagnostics if item["stage"] != "review"
    ]
    unresolved.extend(semantic_review_diagnostics)

    semantic_unresolved = list(unresolved)
    unresolved.extend(workflow_review_diagnostics)
    degradations = _deduplicate_degradations(progress.degradations)
    validation_unresolved = [
        {
            "stage": "validation",
            "action_ids": item.get("action_ids", []),
            "reason": item.get("message", "结果存在待确认项"),
        }
        for item in degradations
    ]
    unresolved.extend(validation_unresolved)
    workflow_diagnostics = validation_unresolved + workflow_review_diagnostics
    quality_status = "UNRESOLVED" if unresolved else "PASS"

    final_state = {
        **state,
        "repositories": source_manifest["repositories"],
        "module_scope": source_manifest["source_scope"],
        "scope_expansion": source_manifest["scope_expansion"],
        "source_manifest": source_manifest,
        "inventory": inventory,
        "analysis_units": [
            {
                **unit.model_dump(mode="json"),
                "status": (
                    "COMPLETED"
                    if unit.unit_id in progress.completed_analysis_units
                    else "INCOMPLETE"
                ),
            }
            for unit in progress.analysis_units
        ],
        "completed_analysis_units": list(progress.completed_analysis_units),
        "completed_closure_units": list(progress.completed_closure_units),
        "analysis_summaries": [
            {
                "unit_id": unit_id,
                "worker_id": _analysis_worker_id(progress, unit_id),
                "summary": result.summary,
            }
            for unit_id, result in results.items()
        ],
        "business_flows": flows,
        "input_decisions": input_decisions,
        "branch_decisions": branch_decisions,
        "coverage_decisions": coverage_decisions,
        "mechanism_decisions": mechanism_decisions,
        "risks": risks,
        "scenarios": scenarios,
        "test_cases": test_cases,
        "coverage_report": {
            "matched": coverage_gaps,
            "ambiguous": [],
            "unmatched": [],
        },
        "review_findings": active_review_findings,
        "review_finding_history": review_finding_history,
        "comparison_audit": comparison_audit,
        "quality_report": {
            "status": quality_status,
            "checks": _completed_checks(
                test_cases,
                scenarios,
                progress,
                results,
                review,
                comparison_review,
                comparison_audit,
            ),
            "unresolved": unresolved,
            "semantic_unresolved": semantic_unresolved,
            "workflow_diagnostics": workflow_diagnostics,
            "advisories": [],
            "diagnostic_occurrence_count": sum(
                item["occurrence_count"]
                for item in degradations
            ) + len(workflow_review_diagnostics),
        },
        "degradations": degradations,
        "degradation_occurrence_count": sum(
            item["occurrence_count"]
            for item in degradations
        ),
        "errors": progress.errors,
        "phase": "COMPLETE",
        "run_status": "COMPLETE",
    }

    markdown_path, html_path = write_reports(run_dir, final_state)
    progress.lifecycle_status = "complete"
    progress.stage = "complete"
    progress.quality_status = quality_status
    progress.degradations = degradations
    progress.report_path = str(markdown_path)
    progress.html_report_path = str(html_path)
    save_progress(state, progress)

    completed = {
        **final_state,
        "lifecycle_status": progress.lifecycle_status,
        "stage": progress.stage,
        "report_path": str(markdown_path),
        "html_report_path": str(html_path),
    }
    write_json(run_dir / "final-state.json", completed)
    return completed
