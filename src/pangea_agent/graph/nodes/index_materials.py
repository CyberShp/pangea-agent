from __future__ import annotations

from pathlib import Path

from pangea_agent.graph.state import PangeaState
from pangea_agent.index.ingest import build_run_index


def index_materials(state: PangeaState) -> PangeaState:
    run_dir = Path(state["data_root"]) / "runs" / state["run_id"]
    index_path = run_dir / "index.sqlite"
    manifest = build_run_index(
        index_path=index_path,
        repositories=state.get("repositories", []),
        module_scope=state.get("module_scope", []),
        data_root=Path(state["data_root"]),
    )
    return {**state, "index_path": str(index_path), "source_manifest": manifest}
