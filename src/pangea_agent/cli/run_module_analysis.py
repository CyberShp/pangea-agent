from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from pangea_agent.agent_io import write_json
from pangea_agent.graph.graph import graph


def _default_run_id(contract: dict) -> str:
    target = re.sub(r"[^A-Za-z0-9_-]+", "-", str(contract.get("target", "analysis"))).strip("-").lower()
    target = (target or "analysis")[:24]
    stamp = date.today().strftime("%y%m%d")
    runs_root = Path(contract.get("data_root", "pangea-data")) / "runs"
    prefix = f"{target}-{stamp}"
    sequence = 1
    while (runs_root / f"{prefix}-{sequence:02d}").exists():
        sequence += 1
    return f"{prefix}-{sequence:02d}"


def run_module_analysis(contract_path: str) -> dict:
    path = Path(contract_path)
    contract = json.loads(path.read_text(encoding="utf-8"))
    run_id = contract.get("run_id")
    if not run_id:
        run_id = _default_run_id(contract)
        contract["run_id"] = run_id
        write_json(path, contract)
    state = {
        "run_id": run_id,
        "data_root": contract.get("data_root", "pangea-data"),
        "task_contract": contract,
    }
    return graph.invoke(state)
