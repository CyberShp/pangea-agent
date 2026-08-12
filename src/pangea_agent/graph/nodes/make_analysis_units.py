from __future__ import annotations

from pangea_agent.graph.state import PangeaState


def make_analysis_units(state: PangeaState) -> PangeaState:
    """Create coarse semantic units from source scope.

    This is intentionally deterministic. LLM-based planning can be added later,
    but schema remains the source of truth.
    """

    units = []
    expansion_groups = state.get("scope_expansion", {}).get("groups", [])
    for group in expansion_groups:
        if group["code_paths"]:
            requested = ", ".join(group["requested_scope"])
            units.append({
                "unit_id": f"U{len(units):02d}",
                "repo_id": group["repo_id"],
                "title": f"{group['repo_id']} 源码范围 {requested}",
                "source_scope": group["code_paths"],
                "context_scope": group["context_paths"],
                "focus": ["code_map", "flows", "branches", "risks", "test_cases"],
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
