from __future__ import annotations

from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.source_scanner import build_lightweight_inventory


def build_inventory(state: PangeaState) -> PangeaState:
    inventory = build_lightweight_inventory(state.get("repositories", []), state.get("module_scope", []))
    return {**state, "inventory": inventory}
