from __future__ import annotations

from pathlib import Path
from time import perf_counter

from pangea_agent.agent_io import write_json
from pangea_agent.documents.coverage import match_coverage_records
from pangea_agent.graph.run_store import load_progress, save_progress
from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.source_scanner import build_lightweight_inventory


def build_inventory(state: PangeaState) -> PangeaState:
    started = perf_counter()
    print("[pangea] build_inventory started", flush=True)

    inventory = build_lightweight_inventory(state.get("repositories", []), state.get("module_scope", []))
    coverage_report = match_coverage_records(
        state.get("source_manifest", {}).get("coverage_records", []), inventory
    )
    errors = list(state.get("errors", []))
    errors.extend(
        {"kind": "missing_dependency", "package": package, "scope": "C/C++ structural parsing"}
        for package in inventory.get("missing_dependencies", [])
    )
    result = {
        **state,
        "inventory": inventory,
        "parse_failures": inventory.get("parse_failures", []),
        "coverage_report": coverage_report,
        "errors": errors,
    }

    run_dir = Path(state["data_root"]) / "runs" / state["run_id"]
    write_json(run_dir / "inputs" / "inventory.json", inventory)
    progress = load_progress(result)
    if progress is None or progress.phase != "PREPARING":
        raise ValueError("build_inventory 完成时缺少 PREPARING progress")
    progress.init_step = "INVENTORY_READY"
    save_progress(result, progress)

    print(
        f"[pangea] build_inventory completed in {perf_counter() - started:.2f}s "
        f"(files={inventory.get('file_count', 0)})",
        flush=True,
    )
    return result
