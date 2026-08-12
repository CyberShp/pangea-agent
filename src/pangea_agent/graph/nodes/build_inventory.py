from __future__ import annotations

from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.source_scanner import build_lightweight_inventory
from pangea_agent.documents.coverage import match_coverage_records


def build_inventory(state: PangeaState) -> PangeaState:
    inventory = build_lightweight_inventory(state.get("repositories", []), state.get("module_scope", []))
    coverage_report = match_coverage_records(
        state.get("source_manifest", {}).get("coverage_records", []), inventory
    )
    errors = list(state.get("errors", []))
    errors.extend(
        {"kind": "missing_dependency", "package": package, "scope": "C/C++ structural parsing"}
        for package in inventory.get("missing_dependencies", [])
    )
    return {
        **state,
        "inventory": inventory,
        "parse_failures": inventory.get("parse_failures", []),
        "coverage_report": coverage_report,
        "errors": errors,
    }
