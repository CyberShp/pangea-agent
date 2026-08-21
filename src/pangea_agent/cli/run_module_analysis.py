from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from pangea_agent.graph.graph import graph
from pangea_agent.inventory.scope_expander import preflight_source_scopes
from pangea_agent.repositories.resolver import resolve_repositories_from_contract


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


def _invoke_contract(contract: dict) -> dict:
    state = {
        "run_id": contract["run_id"],
        "data_root": contract.get("data_root", "pangea-data"),
        "task_contract": contract,
    }
    return graph.invoke(state)


def _preflight_new_contract(contract: dict) -> None:
    data_root = contract.get("data_root", "pangea-data")
    repositories = resolve_repositories_from_contract(contract, data_root)
    scope = contract.get("source_scope") or []
    if isinstance(scope, str):
        scope = [scope]
    preflight_source_scopes(repositories, list(scope))


def run_module_analysis(contract_path: str) -> dict:
    path = Path(contract_path)
    contract = json.loads(path.read_text(encoding="utf-8"))
    if not contract.get("run_id"):
        _preflight_new_contract(contract)
        contract["run_id"] = _default_run_id(contract)
    return _invoke_contract(contract)


def start_module_analysis(contract_path: str) -> dict:
    path = Path(contract_path)
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract.pop("run_id", None)
    _preflight_new_contract(contract)
    contract["run_id"] = _default_run_id(contract)
    return _invoke_contract(contract)


def resume_module_analysis(run_id: str, data_root: str = "pangea-data") -> dict:
    contract_path = Path(data_root) / "runs" / run_id / "inputs" / "task-contract.json"
    if not contract_path.is_file():
        raise ValueError(
            f"冻结 task contract 不存在：{contract_path}。"
            "该 Run 可能是在旧版本初始化阶段中断；请使用原 pending-task-contract.json "
            "重新执行 module-analysis 以建立可恢复状态。"
        )
    return run_module_analysis(str(contract_path))
