from __future__ import annotations

from pathlib import Path

from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.source_languages import analysis_language_for_path


def _unit_languages(paths: list[str]) -> list[str]:
    languages = {
        language
        for path in paths
        if (language := analysis_language_for_path(Path(path))) is not None
    }
    return sorted(languages)


def _unit_frameworks(repo_id: str, paths: list[str], inventory: dict) -> list[str]:
    owned = set(paths)
    return sorted({
        framework
        for item in inventory.get("files", [])
        if item.get("repo_id") == repo_id and item.get("path") in owned
        for framework in item.get("frameworks", [])
    })


def make_analysis_units(state: PangeaState) -> PangeaState:
    """Create coarse semantic units from source scope.

    This is intentionally deterministic. LLM-based planning can be added later,
    but schema remains the source of truth.
    """

    units = []
    expansion_groups = state.get("scope_expansion", {}).get("groups", [])
    inventory = state.get("inventory", {})
    for group in expansion_groups:
        if group["code_paths"]:
            requested = ", ".join(group["requested_scope"])
            languages = _unit_languages(group["code_paths"])
            units.append({
                "unit_id": f"U{len(units):02d}",
                "repo_id": group["repo_id"],
                "title": f"{group['repo_id']} 源码范围 {requested}",
                "source_scope": group["code_paths"],
                "context_scope": group["context_paths"],
                "focus": ["code_map", "flows", "branches", "risks", "test_cases"],
                "languages": languages or ["c_cpp"],
                "frameworks": _unit_frameworks(
                    group["repo_id"],
                    [*group["code_paths"], *group["context_paths"]],
                    inventory,
                ),
                "dfx": [
                    "功能与状态",
                    "资源与规格",
                    "性能与压力",
                    "并发与异常",
                    "升级与兼容",
                    "可靠性与一致性",
                ],
            })
    return {**state, "analysis_units": units}
