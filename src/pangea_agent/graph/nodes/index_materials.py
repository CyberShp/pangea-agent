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
        scope_expansion=state.get("scope_expansion", {}),
    )
    attachments = manifest.get("attachments", [])
    errors = list(state.get("errors", []))
    errors.extend(
        {"kind": "document_parse_warning", **warning}
        for warning in manifest.get("warnings", [])
    )
    errors.extend(
        {"kind": "missing_dependency", **dependency}
        for dependency in manifest.get("missing_dependencies", [])
    )
    return {
        **state,
        "index_path": str(index_path),
        "source_manifest": manifest,
        "unread_images": attachments,
        "errors": errors,
    }
