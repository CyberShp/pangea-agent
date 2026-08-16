from __future__ import annotations

from typing import Any, TypedDict


class PangeaState(TypedDict, total=False):
    """Shared workflow state for pangea-agent.

    Keep state small and explicit. Large source content belongs in the per-run
    SQLite index and evidence packs, not in the graph state.
    """

    run_id: str
    data_root: str
    task_contract: dict[str, Any]
    repositories: list[dict[str, Any]]
    module_scope: list[str]
    scope_expansion: dict[str, Any]
    source_manifest: dict[str, Any]
    coverage_report: dict[str, Any]
    index_path: str
    inventory: dict[str, Any]
    analysis_units: list[dict[str, Any]]
    analysis_summaries: list[dict[str, Any]]
    business_flows: list[dict[str, Any]]
    visual_findings: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    test_cases: list[dict[str, Any]]
    quality_report: dict[str, Any]
    report_path: str
    html_report_path: str
    phase: str
    run_status: str
    agent_task_paths: list[str]
    parse_failures: list[dict[str, Any]]
    unread_images: list[dict[str, Any]]
    errors: list[dict[str, Any]]
