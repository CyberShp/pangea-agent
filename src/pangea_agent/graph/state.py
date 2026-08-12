from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class PangeaState(TypedDict):
    """Shared workflow state for pangea-agent.

    Keep state small and explicit. Large source content belongs in the per-run
    SQLite index and evidence packs, not in the graph state.
    """

    run_id: str
    data_root: str
    task_contract: dict[str, Any]
    repositories: NotRequired[list[dict[str, Any]]]
    module_scope: NotRequired[list[str]]
    source_manifest: NotRequired[dict[str, Any]]
    index_path: NotRequired[str]
    inventory: NotRequired[dict[str, Any]]
    analysis_units: NotRequired[list[dict[str, Any]]]
    risks: NotRequired[list[dict[str, Any]]]
    test_points: NotRequired[list[dict[str, Any]]]
    test_cases: NotRequired[list[dict[str, Any]]]
    quality_report: NotRequired[dict[str, Any]]
    report_path: NotRequired[str]
    errors: NotRequired[list[dict[str, Any]]]
