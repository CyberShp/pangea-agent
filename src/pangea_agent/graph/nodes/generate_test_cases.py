from __future__ import annotations

from pangea_agent.graph.state import PangeaState


def generate_test_cases(state: PangeaState) -> PangeaState:
    cases = []
    for idx, point in enumerate(state.get("test_points", []), 1):
        cases.append({
            "test_case_id": f"TC-{idx:03d}",
            "title": point["title"],
            "linked_risk_ids": [point["risk_id"]],
            "preconditions": ["准备对应模块的可运行测试环境"],
            "steps": [point["objective"]],
            "expected_results": [point["observation"]],
            "observability": [point["observation"]],
            "cleanup": ["恢复测试环境到基线状态"],
            "status": "draft",
        })
    return {**state, "test_cases": cases}
