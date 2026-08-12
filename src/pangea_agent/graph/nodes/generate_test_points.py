from __future__ import annotations

from pangea_agent.graph.state import PangeaState


def generate_test_points(state: PangeaState) -> PangeaState:
    points = []
    for idx, risk in enumerate(state.get("risks", []), 1):
        points.append({
            "test_point_id": f"TP-{idx:03d}",
            "risk_id": risk["risk_id"],
            "title": risk["title"],
            "objective": risk.get("trigger", "构造风险触发条件"),
            "observation": risk.get("external_observation", "补充外部观测"),
            "status": "candidate",
        })
    return {**state, "test_points": points}
