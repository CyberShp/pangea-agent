from __future__ import annotations

from pathlib import Path
import shutil
from time import perf_counter

from pangea_agent.agent_io import write_json
from pangea_agent.graph.run_store import load_progress, save_progress
from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.scope_expander import expand_analysis_scope

DOCUMENT_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".xlsx"}


def _freeze_inputs(state: PangeaState, expansion: dict, run_dir: Path) -> list[dict]:
    paths_by_repo: dict[str, set[str]] = {}
    for group in expansion.get("groups", []):
        paths_by_repo.setdefault(group["repo_id"], set()).update(group.get("code_paths", []))
        paths_by_repo[group["repo_id"]].update(group.get("context_paths", []))

    staging_root = run_dir / "inputs" / ".frozen-staging"
    frozen_root = run_dir / "inputs" / "frozen"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)
    frozen: list[dict] = []
    for repository in state.get("repositories", []):
        repo_id = repository["repo_id"]
        source_root = Path(repository["source_root"]).resolve()
        snapshot_root = staging_root / "source" / repo_id
        for relative in sorted(paths_by_repo.get(repo_id, set())):
            source = (source_root / relative).resolve()
            try:
                source.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(f"源码范围越过仓库边界：{repo_id}:{relative}") from exc
            if not source.is_file():
                raise ValueError(f"冻结源码时文件不存在：{repo_id}:{relative}")
            destination = snapshot_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        frozen.append({
            "repo_id": repo_id,
            "source_root": str(frozen_root / "source" / repo_id),
            "git": repository.get("git", {}),
        })
    for folder in ("inbox", "coverage"):
        source_root = Path(state["data_root"]) / folder
        if not source_root.exists():
            continue
        for source in source_root.rglob("*"):
            if not source.is_file() or source.suffix.lower() not in DOCUMENT_SUFFIXES:
                continue
            destination = staging_root / "materials" / folder / source.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    if frozen_root.exists():
        shutil.rmtree(frozen_root)
    staging_root.replace(frozen_root)
    return frozen


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
    frozen_repositories = _freeze_inputs(state, expansion, run_dir)
    result["repositories"] = frozen_repositories
    write_json(run_dir / "inputs" / "scope-expansion.json", expansion)
    write_json(run_dir / "inputs" / "source-repositories.json", frozen_repositories)
    progress = load_progress(result)
    if progress is None or progress.phase != "PREPARING":
        raise ValueError("locate_module 完成时缺少 PREPARING progress")
    progress.init_step = "SOURCE_READY"
    save_progress(result, progress)

    print(
        f"[pangea] locate_module completed in {perf_counter() - started:.2f}s "
        f"(groups={len(expansion.get('groups', []))}, scope_files={len(result['module_scope'])})",
        flush=True,
    )
    return result
