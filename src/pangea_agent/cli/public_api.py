from __future__ import annotations

from pathlib import Path

from pangea_agent.assets import (
    archive_asset,
    asset_detail,
    import_asset,
    import_asset_revision,
    list_assets,
    preview_asset_import,
    prepare_asset_extraction,
    restore_asset,
    review_asset,
    review_asset_items,
    update_asset_metadata,
    update_asset_result,
)
from pangea_agent.repositories.registry import list_registered_repositories
from pangea_agent.methodology import (
    complete_methodology_derivation,
    import_methodology_candidates,
    list_methodology_derivations,
    list_methodologies,
    prepare_methodology_derivation,
    set_methodology_status,
    show_methodology_derivation,
    show_methodology,
)
from pangea_agent.skill_runs import (
    list_skill_runs,
    skill_run_detail,
    stop_skill_run,
    validate_runtime_skill,
)


def system_capabilities(data_root: str) -> dict:
    return {
        "analysis_skill": validate_runtime_skill(),
        "analysis_runtime": "direct-skill",
        "analysis_languages": ["c_cpp", "lua"],
        "asset_types": [
            "requirement",
            "design",
            "historical_defect",
            "reference",
            "coverage",
            "test_case_example",
        ],
        "asset_schema_version": "2.0",
        "repositories": list_registered_repositories(data_root),
        "report_formats": ["markdown"],
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


def list_runs(data_root: str, *, cursor: int = 0, limit: int = 50) -> dict:
    return list_skill_runs(data_root, cursor=cursor, limit=limit)


def run_detail(data_root: str, run_id: str) -> dict:
    summary = skill_run_detail(data_root, run_id)
    summary["reports"] = {
        "html": None,
        "markdown": summary["artifacts"]["report_markdown"],
    }
    return summary


def run_report(data_root: str, run_id: str, report_format: str) -> dict:
    if report_format != "markdown":
        raise ValueError("Codetalks Skill 正式交付只提供 markdown")
    detail = skill_run_detail(data_root, run_id)
    path = detail["artifacts"]["report_markdown"]
    if not path:
        raise ValueError(f"完整分析报告不存在：{run_id}")
    return {"run_id": run_id, "format": report_format, "path": str(path)}


def stop_run(data_root: str, run_id: str) -> dict:
    return stop_skill_run(data_root, run_id)


__all__ = [
    "archive_asset",
    "asset_detail",
    "import_asset",
    "import_asset_revision",
    "import_methodology_candidates",
    "complete_methodology_derivation",
    "list_assets",
    "list_methodologies",
    "list_methodology_derivations",
    "list_runs",
    "prepare_asset_extraction",
    "preview_asset_import",
    "prepare_methodology_derivation",
    "review_asset",
    "review_asset_items",
    "restore_asset",
    "run_detail",
    "run_report",
    "set_methodology_status",
    "show_methodology_derivation",
    "show_methodology",
    "system_capabilities",
    "update_asset_metadata",
    "stop_run",
    "update_asset_result",
]
