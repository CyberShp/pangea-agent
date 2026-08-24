from __future__ import annotations

from pathlib import Path
from time import perf_counter

from pangea_agent.agent_io import write_json
from pangea_agent.documents.coverage import (
    filter_inventory_to_sources,
    match_coverage_records,
    relevant_zero_coverage,
)
from pangea_agent.graph.run_store import load_progress, save_progress
from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.source_scanner import build_lightweight_inventory
from pangea_agent.inventory.source_languages import inventory_context_for_path


def build_inventory(state: PangeaState) -> PangeaState:
    started = perf_counter()
    print("[pangea] build_inventory started", flush=True)

    groups = state.get("scope_expansion", {}).get("groups", [])
    inventories = []
    for repository in state.get("repositories", []):
        repo_groups = [group for group in groups if group.get("repo_id") == repository["repo_id"]]
        inventory_scope = list(dict.fromkeys(
            path
            for group in repo_groups
            for path in [
                *group.get("code_paths", []),
                *(
                    item
                    for item in group.get("context_paths", [])
                    if inventory_context_for_path(Path(item))
                ),
            ]
        ))
        if not inventory_scope and not repo_groups:
            inventory_scope = list(state.get("module_scope", []))
        if inventory_scope:
            inventories.append(build_lightweight_inventory([repository], inventory_scope))
    files = [item for inventory in inventories for item in inventory.get("files", [])]
    missing_dependencies = sorted({
        package
        for inventory in inventories
        for package in inventory.get("missing_dependencies", [])
    })
    parse_failures = [
        item
        for inventory in inventories
        for item in inventory.get("parse_failures", [])
    ]
    inventory = {
        "files": files,
        "file_count": len(files),
        "missing_dependencies": missing_dependencies,
        "parse_failures": parse_failures,
        "structural_parse_complete": not missing_dependencies and not parse_failures,
    }
    resolved_dependencies = {
        (
            item.get("repo_id"),
            item.get("path"),
            item.get("line"),
            item.get("module"),
        ): item.get("resolved_path")
        for item in state.get("scope_expansion", {}).get("resolved_dependencies", [])
    }
    for file in inventory["files"]:
        for item in file.get("imports", []):
            item["resolved_path"] = resolved_dependencies.get(
                (file.get("repo_id"), file.get("path"), item.get("line"), item.get("module")),
                item.get("resolved_path"),
            )
    source_paths = {
        (group.get("repo_id"), path)
        for group in groups
        for path in group.get("code_paths", [])
    }
    path_coverage_paths = {
        (group.get("repo_id"), path)
        for group in groups
        for path in [*group.get("code_paths", []), *group.get("context_paths", [])]
    }
    coverage_report = relevant_zero_coverage(
        match_coverage_records(
            state.get("source_manifest", {}).get("coverage_records", []),
            filter_inventory_to_sources(inventory, source_paths),
            path_inventory=filter_inventory_to_sources(inventory, path_coverage_paths),
        )
    )
    errors = list(state.get("errors", []))
    errors.extend(
        {"kind": "missing_dependency", "package": package, "scope": "源码结构化解析"}
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
