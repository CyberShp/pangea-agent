from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.workflow_store import (
    load_progress,
    run_directory,
    save_progress,
    validated_result_path,
)
from pangea_agent.models.analysis import (
    ComparisonReviewResult,
    IndependentReviewResult,
    UnitSemanticResult,
)
from pangea_agent.report import write_reports


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


def _load_final_unit_result(state: PangeaState, progress, unit_id: str) -> UnitSemanticResult:
    closure_action_id = f"{state['run_id']}:closure:{unit_id}"
    action_id = (
        closure_action_id
        if closure_action_id in progress.actions
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
        entry = grouped.setdefault(key, {
            "kind": kind,
            "message": message,
            "action_ids": [],
            "occurrence_count": 0,
        })
        for action_id in action_ids:
            if action_id and action_id not in entry["action_ids"]:
                entry["action_ids"].append(action_id)
        entry["occurrence_count"] += int(item.get("occurrence_count", 1))
    return list(grouped.values())


def _analysis_worker_id(progress, unit_id: str) -> str:
    action = progress.actions.get(f"{progress.run_id}:analysis:{unit_id}")
    return action.task_id if action and action.task_id else "未绑定"


def finalize_workflow(state: PangeaState) -> PangeaState:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("Run progress 不存在")
    run_dir = run_directory(state)
    results = {
        unit.unit_id: _load_final_unit_result(state, progress, unit.unit_id)
        for unit in progress.analysis_units
    }
    risk_ids: dict[tuple[str, str], str] = {}
    case_ids: dict[tuple[str, str], str] = {}
    risks = []
    test_cases = []
    flows = []
    input_decisions = []
    coverage_decisions = []
    mechanism_decisions = []
    unresolved = []

    for unit in progress.analysis_units:
        result = results[unit.unit_id]
        unresolved.extend({"unit_id": unit.unit_id, "reason": value} for value in result.unresolved)
        for number, risk in enumerate(result.risks, 1):
            risk_id = f"R-{unit.unit_id}-{number:03d}"
            risk_ids[(unit.unit_id, risk.risk_key)] = risk_id
            risks.append({
                **risk.model_dump(mode="json", exclude={"risk_key", "evidence"}),
                "risk_id": risk_id,
                "evidence": [_evidence(item) for item in risk.evidence],
                "translation_status": "Uncovered",
                "status": "identified",
            })
        for number, case in enumerate(result.test_cases, 1):
            case_id = f"TC-{unit.unit_id}-{number:03d}"
            case_ids[(unit.unit_id, case.case_key)] = case_id
            unresolved_risk_keys = [
                key
                for key in case.linked_risk_keys
                if (unit.unit_id, key) not in risk_ids
            ]
            test_cases.append({
                "test_case_id": case_id,
                "title": case.title,
                "case_type": case.level,
                "basis": case.basis,
                "covered_flow_ids": [
                    f"F-{unit.unit_id}-{key}" for key in case.covered_flow_keys
                ],
                "linked_input_ids": case.linked_input_ids,
                "linked_risk_ids": [
                    risk_ids[(unit.unit_id, key)]
                    for key in case.linked_risk_keys
                    if (unit.unit_id, key) in risk_ids
                ],
                "unresolved_linked_risk_keys": unresolved_risk_keys,
                "preconditions": case.preconditions,
                "steps": [step.action for step in case.steps],
                "expected_results": [step.expected_result for step in case.steps],
                "observability": case.observability,
                "cleanup": case.cleanup,
                "status": "ready",
            })
        for flow in result.flows:
            flows.append({
                "flow_id": f"F-{unit.unit_id}-{flow.flow_key}",
                "unit_id": unit.unit_id,
                "title": flow.title,
                "description": flow.summary,
                "entry": flow.entry,
                "steps": [f"[{step.kind}] {step.label}" for step in flow.steps],
                "diagram": {
                    "nodes": [
                        {
                            "id": step.step_key,
                            "label": step.label,
                            "kind": step.kind,
                        }
                        for step in flow.steps
                    ],
                    "edges": [edge.model_dump(mode="json") for edge in flow.edges],
                },
                "mermaid": _mermaid(flow),
                "evidence": [
                    _evidence(evidence)
                    for step in flow.steps
                    for evidence in step.evidence
                ],
            })
        input_decisions.extend(
            {"unit_id": unit.unit_id, **item.model_dump(mode="json")}
            for item in result.input_decisions
        )
        coverage_decisions.extend(
            {
                "unit_id": unit.unit_id,
                **item.model_dump(mode="json", exclude={"test_case_keys"}),
                "test_case_ids": [
                    case_ids[(unit.unit_id, key)]
                    for key in item.test_case_keys
                    if (unit.unit_id, key) in case_ids
                ],
                "unresolved_test_case_keys": [
                    key
                    for key in item.test_case_keys
                    if (unit.unit_id, key) not in case_ids
                ],
            }
            for item in result.coverage_decisions
        )
        mechanism_decisions.extend(
            {
                "unit_id": unit.unit_id,
                **item.model_dump(mode="json", exclude={"test_case_keys", "evidence"}),
                "test_case_ids": [
                    case_ids[(unit.unit_id, key)]
                    for key in item.test_case_keys
                    if (unit.unit_id, key) in case_ids
                ],
                "unresolved_test_case_keys": [
                    key
                    for key in item.test_case_keys
                    if (unit.unit_id, key) not in case_ids
                ],
                "evidence": [_evidence(evidence) for evidence in item.evidence],
            }
            for item in result.mechanism_decisions
        )
        unresolved.extend(
            {
                "unit_id": unit.unit_id,
                "finding_key": item.finding_key,
                "reason": item.conclusion,
            }
            for item in result.review_finding_decisions
            if item.disposition == "unresolved"
        )

    linked_cases_by_risk: dict[str, list[str]] = defaultdict(list)
    for case in test_cases:
        for risk_id in case["linked_risk_ids"]:
            linked_cases_by_risk[risk_id].append(case["test_case_id"])
    for risk in risks:
        linked_case_ids = linked_cases_by_risk.get(risk["risk_id"], [])
        risk["test_case_ids"] = linked_case_ids
        risk["translation_status"] = (
            "Test-ready" if linked_case_ids else "Uncovered"
        )

    planning = read_json(run_dir / "inputs" / "unit-plan.json")
    unresolved.extend({"stage": "planning", "reason": value} for value in planning.get("unresolved", []))
    review = None
    review_action_id = f"{state['run_id']}:review"
    if review_action_id in progress.actions:
        review = IndependentReviewResult.model_validate(
            read_json(validated_result_path(state, review_action_id))
        )
    comparison_review = None
    comparison_action_id = f"{state['run_id']}:comparison-review"
    if comparison_action_id in progress.actions:
        comparison_review = ComparisonReviewResult.model_validate(
            read_json(validated_result_path(state, comparison_action_id))
        )
        unresolved.extend(
            {"stage": "review", "reason": value}
            for value in comparison_review.unresolved
        )
    comparison_decisions = {
        decision.finding_key: decision.disposition
        for decision in (
            comparison_review.independent_finding_decisions
            if comparison_review
            else []
        )
    }
    semantic_unresolved = list(unresolved)
    degradations = _deduplicate_degradations(progress.degradations)
    advisories = [
        item
        for item in degradations
        if item.get("kind") == "agent_result_warning"
    ]
    blocking_degradations = [
        item
        for item in degradations
        if item.get("kind") != "agent_result_warning"
    ]
    validation_unresolved = [
        {
            "stage": "validation",
            "action_ids": item.get("action_ids", []),
            "reason": item.get("message", "结果存在待确认项"),
        }
        for item in blocking_degradations
    ]
    unresolved.extend(validation_unresolved)
    quality_status = "UNRESOLVED" if unresolved else "PASS"
    source_manifest = read_json(run_dir / "inputs" / "source-manifest.json")
    inventory = read_json(run_dir / "inputs" / "inventory.json")
    coverage_gaps = read_json(run_dir / "inputs" / "coverage-gaps.json")
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
        "coverage_decisions": coverage_decisions,
        "mechanism_decisions": mechanism_decisions,
        "risks": risks,
        "test_cases": test_cases,
        "coverage_report": {"matched": coverage_gaps, "ambiguous": [], "unmatched": []},
        "review_findings": [
            item.model_dump(mode="json")
            for item in (
                [
                    finding
                    for finding in review.findings
                    if comparison_decisions.get(
                        finding.finding_key,
                        "unresolved",
                    ) != "dismissed"
                ]
                + comparison_review.findings
                if review and comparison_review
                else []
            )
        ],
        "quality_report": {
            "status": quality_status,
            "checks": [
                "请求源码均由一个分析单元负责",
                "相关结构化资料、Coverage 缺口和缺陷机理均已给出处置",
                "独立复核已完成，可信遗漏已定向补齐",
            ],
            "unresolved": unresolved,
            "semantic_unresolved": semantic_unresolved,
            "workflow_diagnostics": validation_unresolved,
            "advisories": advisories,
            "diagnostic_occurrence_count": sum(
                item["occurrence_count"] for item in blocking_degradations
            ),
        },
        "degradations": degradations,
        "degradation_occurrence_count": sum(
            item["occurrence_count"] for item in degradations
        ),
        "errors": progress.errors,
        "phase": "COMPLETE" if quality_status == "PASS" else "INCOMPLETE",
        "run_status": "COMPLETE" if quality_status == "PASS" else "INCOMPLETE",
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
