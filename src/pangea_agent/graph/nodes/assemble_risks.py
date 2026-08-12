from __future__ import annotations

from pangea_agent.graph.state import PangeaState


def assemble_risks(state: PangeaState) -> PangeaState:
    """Merge and deduplicate risks.

    First version keeps order and removes duplicate risk_id only.
    """

    seen = set()
    merged = []
    for risk in state.get("risks", []):
        rid = risk.get("risk_id")
        if rid in seen:
            continue
        seen.add(rid)
        merged.append(risk)
    return {**state, "risks": merged}
