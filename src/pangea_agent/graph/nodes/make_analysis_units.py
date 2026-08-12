from __future__ import annotations

from pangea_agent.graph.state import PangeaState


def make_analysis_units(state: PangeaState) -> PangeaState:
    """Create coarse semantic units from source scope.

    This is intentionally deterministic. LLM-based planning can be added later,
    but schema remains the source of truth.
    """

    units = []
    for idx, item in enumerate(state.get("module_scope", []) or ["."]):
        units.append({
            "unit_id": f"U{idx:02d}",
            "title": f"源码范围 {item}",
            "source_scope": [item],
            "focus": ["code_map", "flows", "branches", "risks", "test_cases"],
            "dfx": ["功能与状态", "资源与规格", "并发与异常", "可靠性与一致性"],
            "priority": "P0" if idx == 0 else "P1",
        })
    return {**state, "analysis_units": units}
