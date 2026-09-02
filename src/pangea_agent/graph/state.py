from __future__ import annotations

from typing import Any, TypedDict


class PangeaState(TypedDict, total=False):
    """Small graph state; large frozen inputs stay in the Run directory."""

    run_id: str
    data_root: str
    task_contract: dict[str, Any]
    repositories: list[dict[str, Any]]
    module_scope: list[str]
    scope_expansion: dict[str, Any]
    source_manifest: dict[str, Any]
    coverage_report: dict[str, Any]
    inventory: dict[str, Any]
    analysis_units: list[dict[str, Any]]
    analysis_summaries: list[dict[str, Any]]
    business_flows: list[dict[str, Any]]
    input_decisions: list[dict[str, Any]]
    coverage_decisions: list[dict[str, Any]]
    mechanism_decisions: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    test_cases: list[dict[str, Any]]
    review_findings: list[dict[str, Any]]
    review_finding_history: list[dict[str, Any]]
    quality_report: dict[str, Any]
    report_path: str
    html_report_path: str
    phase: str
    run_status: str
    errors: list[dict[str, Any]]
    needs_prepare: bool
    lifecycle_status: str
    stage: str
    ready_to_finalize: bool
    agent_actions: list[dict[str, Any]]
