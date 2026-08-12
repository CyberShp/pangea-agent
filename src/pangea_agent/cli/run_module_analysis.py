from __future__ import annotations

import json
from pathlib import Path

from pangea_agent.graph.graph import graph


def run_module_analysis(contract_path: str) -> dict:
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    state = {
        "run_id": contract.get("run_id", "RUN-local"),
        "data_root": contract.get("data_root", "pangea-data"),
        "task_contract": contract,
    }
    return graph.invoke(state)
