from __future__ import annotations

from pathlib import Path
from time import perf_counter

from pangea_agent.agent_io import write_json
from pangea_agent.graph.run_store import load_progress, save_progress
from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.scope_expander import expand_analysis_scope


def locate_module(state: PangeaState) -> PangeaState:
    """Resolve module scope from the task contract."""

    started = perf_counter()
    print("[pangea] locate_module started", flush=True)

    contract = state["task_contract"]
    scope = contract.get("source_scope") or []
    if isinstance(scope, str):
        scope = [scope]
    expansion = expand_analysis_scope(
        state.get("repositories", []),
        list(scope),
        target=str(contract.get("target", "")),
        focus=list(contract.get("focus", [])),
    )
    expanded_scope = [
        path
        for group in expansion["groups"]
        for path in group["code_paths"]
    ]
    result = {
        **state,
        "module_scope": list(dict.fromkeys(expanded_scope)),
        "scope_expansion": expansion,
    }

    run_dir = Path(state["data_root"]) / "runs" / state["run_id"]
    write_json(run_dir / "inputs" / "scope-expansion.json", expansion)
    progress = load_progress(result)
    if progress is None or progress.phase != "PREPARING":
        raise ValueError("locate_module 完成时缺少 PREPARING progress")
    progress.init_step = "SCOPE_READY"
    save_progress(result, progress)

    print(
        f"[pangea] locate_module completed in {perf_counter() - started:.2f}s "
        f"(groups={len(expansion.get('groups', []))}, scope_files={len(result['module_scope'])})",
        flush=True,
    )
    return result
