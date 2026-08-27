from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from pangea_agent.agent_io import write_json
from pangea_agent.graph.graph import graph


def _default_run_id(contract: dict) -> str:
    target = re.sub(r"[^A-Za-z0-9_-]+", "-", str(contract.get("target", "analysis"))).strip("-").lower()
    target = ((target or "analysis")[:24].rstrip("-_") or "analysis")
    stamp = date.today().strftime("%y%m%d")
    runs_root = Path(contract.get("data_root", "pangea-data")) / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    prefix = f"{target}-{stamp}"
    sequence = 1
    while True:
        run_id = f"{prefix}-{sequence:02d}"
        try:
            (runs_root / run_id).mkdir()
        except FileExistsError:
            sequence += 1
            continue
        return run_id


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


def resume_module_analysis(run_id: str, data_root: str = "pangea-data") -> dict:
    contract_path = Path(data_root) / "runs" / run_id / "inputs" / "task-contract.json"
    if not contract_path.is_file():
        raise ValueError(f"冻结 task contract 不存在，不能恢复 Run：{contract_path}")
    return run_module_analysis(str(contract_path))
