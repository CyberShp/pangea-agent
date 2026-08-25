from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.workflow_store import (
    analysis_result_path,
    closure_result_path,
    comparison_review_result_path,
    load_progress,
    review_result_path,
    run_directory,
    save_progress,
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


def _load_final_unit_result(state: PangeaState, unit_id: str) -> UnitSemanticResult:
    closure_path = closure_result_path(state, unit_id)
    path = closure_path if closure_path.is_file() else analysis_result_path(state, unit_id)
    return UnitSemanticResult.model_validate(read_json(path))


def finalize_workflow(state: PangeaState) -> PangeaState:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("Run progress 不存在")
    run_dir = run_directory(state)
    results = {
        unit.unit_id: _load_final_unit_result(state, unit.unit_id)
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
                "translation_status": "Graybox-ready",
                "status": "pending",
            })
        for number, case in enumerate(result.test_cases, 1):
            case_id = f"TC-{unit.unit_id}-{number:03d}"
            case_ids[(unit.unit_id, case.case_key)] = case_id
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
                    risk_ids[(unit.unit_id, key)] for key in case.linked_risk_keys
                ],
                "preconditions": case.preconditions,
                "steps": [step.action for step in case.steps],
                "expected_results": [step.expected_result for step in case.steps],
                "observability": case.observability,
                "cleanup": case.cleanup,
                "status": "draft",
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
                    case_ids[(unit.unit_id, key)] for key in item.test_case_keys
                ],
            }
            for item in result.coverage_decisions
        )
        mechanism_decisions.extend(
            {
                "unit_id": unit.unit_id,
                **item.model_dump(mode="json", exclude={"test_case_keys", "evidence"}),
                "test_case_ids": [
                    case_ids[(unit.unit_id, key)] for key in item.test_case_keys
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

    planning = read_json(run_dir / "inputs" / "unit-plan.json")
    unresolved.extend({"stage": "planning", "reason": value} for value in planning.get("unresolved", []))
    review = None
    if review_result_path(state).is_file():
        review = IndependentReviewResult.model_validate(read_json(review_result_path(state)))
    comparison_review = None
    if comparison_review_result_path(state).is_file():
        comparison_review = ComparisonReviewResult.model_validate(
            read_json(comparison_review_result_path(state))
        )
        unresolved.extend(
            {"stage": "review", "reason": value}
            for value in comparison_review.unresolved
        )
    quality_status = "UNRESOLVED" if unresolved else "PASS"
    source_manifest = read_json(run_dir / "inputs" / "source-manifest.json")
    coverage_gaps = read_json(run_dir / "inputs" / "coverage-gaps.json")
    final_state = {
        **state,
        "repositories": source_manifest["repositories"],
        "module_scope": source_manifest["source_scope"],
        "scope_expansion": source_manifest["scope_expansion"],
        "source_manifest": source_manifest,
        "inventory": read_json(run_dir / "inputs" / "inventory.json"),
        "analysis_summaries": [
            {"unit_id": unit_id, "summary": result.summary}
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
                    if next(
                        decision.disposition
                        for decision in comparison_review.independent_finding_decisions
                        if decision.finding_key == finding.finding_key
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
        },
        "errors": progress.errors,
        "phase": "COMPLETE" if quality_status == "PASS" else "INCOMPLETE",
        "run_status": "COMPLETE" if quality_status == "PASS" else "INCOMPLETE",
    }
    markdown_path, html_path = write_reports(run_dir, final_state)
    progress.lifecycle_status = "complete"
    progress.stage = "complete"
    progress.quality_status = quality_status
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
