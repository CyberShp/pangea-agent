from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pangea_agent.models.analysis import AnalysisTask, UnitSemanticResult


def normalize_analysis_result(
    task: AnalysisTask,
    raw_result: Any,
    inventory: Mapping[str, Any],
    selected_inputs: Mapping[str, Any],
    warnings: list[str],
) -> UnitSemanticResult:
    """Add only Workflow-owned fields, then validate the Agent result unchanged."""

    del inventory, selected_inputs, warnings
    if not isinstance(raw_result, Mapping):
        raise ValueError("Analysis 结果必须是一个 JSON 对象")

    payload = deepcopy(dict(raw_result))
    _inject_evidence_repo_ids(payload, task.unit.repo_id)
    _inject_case_keys(payload)
    _discard_submitted_derived_links(payload)

    result = UnitSemanticResult.model_validate(payload)
    return _derive_test_case_links(result)


def _inject_evidence_repo_ids(payload: dict[str, Any], repo_id: str) -> None:
    for flow in _mapping_items(payload.get("flows")):
        for step in _mapping_items(flow.get("steps")):
            _set_repo_id(step.get("evidence"), repo_id, force=True)

    for decision in _mapping_items(payload.get("input_decisions")):
        _set_repo_id(decision.get("evidence"), repo_id, force=True)
    for decision in _mapping_items(payload.get("mechanism_decisions")):
        _set_repo_id(decision.get("evidence"), repo_id, force=True)

    for risk in _mapping_items(payload.get("risks")):
        _set_repo_id(risk.get("evidence"), repo_id, force=True)
        _set_repo_id(risk.get("unreachable_evidence"), repo_id, force=True)

    for decision in _mapping_items(payload.get("review_finding_decisions")):
        _set_repo_id(decision.get("evidence"), repo_id, force=False)


def _set_repo_id(value: Any, repo_id: str, *, force: bool) -> None:
    if not isinstance(value, list):
        return
    for evidence in value:
        if not isinstance(evidence, dict):
            continue
        if force or not evidence.get("repo_id"):
            evidence["repo_id"] = repo_id


def _inject_case_keys(payload: dict[str, Any]) -> None:
    cases = payload.get("test_cases")
    if not isinstance(cases, list):
        return
    for index, case in enumerate(cases, 1):
        if isinstance(case, dict):
            case["case_key"] = f"CASE-{index:03d}"


def _discard_submitted_derived_links(payload: dict[str, Any]) -> None:
    for name in ("coverage_decisions", "mechanism_decisions"):
        for decision in _mapping_items(payload.get(name)):
            decision.pop("test_case_keys", None)


def _derive_test_case_links(result: UnitSemanticResult) -> UnitSemanticResult:
    cases_by_input: dict[str, list[str]] = defaultdict(list)
    for case in result.test_cases:
        for item_id in case.linked_input_ids:
            cases_by_input[item_id].append(case.case_key)

    return result.model_copy(
        update={
            "coverage_decisions": [
                decision.model_copy(
                    update={"test_case_keys": cases_by_input.get(decision.coverage_id, [])}
                )
                for decision in result.coverage_decisions
            ],
            "mechanism_decisions": [
                decision.model_copy(
                    update={"test_case_keys": cases_by_input.get(decision.mechanism_id, [])}
                )
                for decision in result.mechanism_decisions
            ],
        },
        deep=True,
    )


def _mapping_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
