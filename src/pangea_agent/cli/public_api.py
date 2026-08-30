from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json
from pangea_agent.assets import (
    archive_asset,
    asset_detail,
    import_asset,
    list_assets,
    prepare_asset_extraction,
    review_asset,
    update_asset_result,
)
from pangea_agent.repositories.registry import list_registered_repositories
from pangea_agent.graph.workflow_store import load_progress, save_progress
from pangea_agent.methodology import (
    complete_methodology_derivation,
    import_methodology_candidates,
    list_methodology_derivations,
    list_methodologies,
    prepare_methodology_derivation,
    run_methodology_manifests,
    set_methodology_status,
    show_methodology_derivation,
    show_methodology,
)
from pangea_agent.report import reports_are_complete


def system_capabilities(data_root: str) -> dict:
    return {
        "analysis_languages": ["c_cpp", "lua"],
        "asset_types": [
            "requirement",
            "design",
            "historical_defect",
            "reference",
            "coverage",
        ],
        "repositories": list_registered_repositories(data_root),
        "report_formats": ["html", "markdown"],
        "methodologies": {
            "schema_version": "1.0",
            "candidate_schema_path": str(
                Path(__file__).resolve().parents[3]
                / "schemas"
                / "methodology_candidate.schema.json"
            ),
            "derivation_task_schema_path": str(
                Path(__file__).resolve().parents[3]
                / "schemas"
                / "methodology_derivation_task.schema.json"
            ),
            "derivation_worker_path": str(
                Path(__file__).resolve().parents[3]
                / ".agents"
                / "pangea"
                / "methodology-worker.md"
            ),
            "statuses": ["candidate", "enabled", "disabled"],
            "derivation_statuses": ["pending", "ready", "completed"],
        },
    }


def _run_summary(run_dir: Path) -> dict:
    progress_path = run_dir / "progress.json"
    progress = read_json(progress_path) if progress_path.is_file() else {}
    phase = str(progress.get("phase") or progress.get("stage") or "UNKNOWN")
    stored_lifecycle = progress.get("lifecycle_status")
    if stored_lifecycle:
        lifecycle_status = stored_lifecycle
    elif phase == "COMPLETE":
        lifecycle_status = "complete"
    elif phase == "STOPPED":
        lifecycle_status = "stopped"
    elif phase == "INCOMPLETE":
        lifecycle_status = "complete"
    elif progress_path.is_file():
        lifecycle_status = "running"
    else:
        lifecycle_status = "failed"
    units = progress.get("analysis_units", [])
    completed = progress.get("completed_analysis_units", [])
    report_available = (
        lifecycle_status == "complete" and reports_are_complete(run_dir)
    )
    return {
        "run_id": run_dir.name,
        "lifecycle_status": lifecycle_status,
        "phase": phase,
        "stage": progress.get("stage"),
        "quality_status": progress.get("quality_status"),
        "unit_count": len(units),
        "completed_unit_count": len(completed),
        "errors": progress.get("errors", []),
        "report_available": report_available,
    }


def list_runs(data_root: str, *, cursor: int = 0, limit: int = 50) -> dict:
    if cursor < 0:
        raise ValueError("cursor 不能小于 0")
    if limit < 1 or limit > 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    root = Path(data_root) / "runs"
    runs = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    ) if root.exists() else []
    page = runs[cursor : cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "items": [_run_summary(path) for path in page],
        "next_cursor": next_cursor if next_cursor < len(runs) else None,
        "total": len(runs),
    }


def run_detail(data_root: str, run_id: str) -> dict:
    run_dir = Path(data_root) / "runs" / run_id
    if not run_dir.is_dir():
        raise ValueError(f"Run 不存在：{run_id}")
    summary = _run_summary(run_dir)
    progress_path = run_dir / "progress.json"
    summary["progress"] = read_json(progress_path) if progress_path.is_file() else None
    summary["reports"] = {
        "html": str(run_dir / "report.html") if summary["report_available"] else None,
        "markdown": str(run_dir / "report.md") if summary["report_available"] else None,
    }
    summary["methodologies"] = run_methodology_manifests(run_dir)
    return summary


def run_report(data_root: str, run_id: str, report_format: str) -> dict:
    suffix = {"html": ".html", "markdown": ".md"}.get(report_format)
    if suffix is None:
        raise ValueError("format 必须是 html 或 markdown")
    path = Path(data_root) / "runs" / run_id / f"report{suffix}"
    if not _run_summary(path.parent)["report_available"]:
        raise ValueError(f"报告不存在：{path}")
    return {"run_id": run_id, "format": report_format, "path": str(path)}


def stop_run(data_root: str, run_id: str) -> dict:
    state = {"data_root": data_root, "run_id": run_id}
    progress = load_progress(state)
    if progress is None:
        raise ValueError(f"Run 不存在：{run_id}")
    if progress.lifecycle_status == "complete":
        raise ValueError("已经完成的 Run 不能停止")
    progress.lifecycle_status = "stopped"
    for action in progress.actions.values():
        if action.status in {"pending", "dispatched", "settled"}:
            action.status = "failed"
            action.error = "用户停止 Run"
    save_progress(state, progress)
    return _run_summary(Path(data_root) / "runs" / run_id)


__all__ = [
    "archive_asset",
    "asset_detail",
    "import_asset",
    "import_methodology_candidates",
    "complete_methodology_derivation",
    "list_assets",
    "list_methodologies",
    "list_methodology_derivations",
    "list_runs",
    "prepare_asset_extraction",
    "prepare_methodology_derivation",
    "review_asset",
    "run_detail",
    "run_report",
    "set_methodology_status",
    "show_methodology_derivation",
    "show_methodology",
    "system_capabilities",
    "stop_run",
    "update_asset_result",
]
