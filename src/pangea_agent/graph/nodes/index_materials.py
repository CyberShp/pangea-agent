from __future__ import annotations

from pathlib import Path
from time import perf_counter

from pangea_agent.agent_io import write_json
from pangea_agent.graph.run_store import load_progress, save_progress
from pangea_agent.graph.state import PangeaState
from pangea_agent.index.ingest import build_run_index


def index_materials(state: PangeaState) -> PangeaState:
    started = perf_counter()
    print("[pangea] index_materials started", flush=True)

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
    result = {
        **state,
        "index_path": str(index_path),
        "source_manifest": manifest,
        "unread_images": attachments,
        "errors": errors,
    }

    write_json(run_dir / "inputs" / "source-manifest.json", manifest)
    progress = load_progress(result)
    if progress is None or progress.phase != "PREPARING":
        raise ValueError("index_materials 完成时缺少 PREPARING progress")
    progress.init_step = "INDEX_READY"
    save_progress(result, progress)

    index_size_mb = index_path.stat().st_size / (1024 * 1024) if index_path.is_file() else 0.0
    print(
        f"[pangea] index_materials completed in {perf_counter() - started:.2f}s "
        f"(files={manifest.get('file_count', 0)}, chunks={manifest.get('chunk_count', 0)}, "
        f"index_mb={index_size_mb:.1f})",
        flush=True,
    )
    return result
