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


def apply_run_event(run_id: str, data_root: str, event: dict) -> dict:
    contract_path = Path(data_root) / "runs" / run_id / "inputs" / "task-contract.json"
    if not contract_path.is_file():
        raise ValueError(f"冻结 task contract 不存在：{contract_path}")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return graph.invoke({
        "run_id": run_id,
        "data_root": data_root,
        "task_contract": contract,
        "event": event,
    })


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
    run_dir = Path(data_root) / "runs" / run_id
    progress_path = run_dir / "progress.json"
    if not progress_path.is_file():
        raise ValueError(f"指定 Run 不存在：{run_id}")
    raw_progress = json.loads(progress_path.read_text(encoding="utf-8"))
    workflow_version = raw_progress.get("workflow_version", 1)
    if workflow_version != 2:
        if workflow_version != 1:
            raise ValueError(f"不支持的 workflow_version：{workflow_version}")
        if workflow_version == 1 and raw_progress.get("phase") == "COMPLETE":
            report_path = run_dir / "report.md"
            if not report_path.is_file():
                raise ValueError("旧版 COMPLETE Run 缺少现有报告，Graph V2 不重建旧版产物")
            result = {
                "run_id": run_id,
                "data_root": data_root,
                "phase": "COMPLETE",
                "report_path": str(report_path),
            }
            html_path = run_dir / "report.html"
            if html_path.is_file():
                result["html_report_path"] = str(html_path)
            return result
        raise ValueError(
            "该 Run 使用旧版文本阶段流程，Graph V2 只读既有 COMPLETE 报告；"
            "旧版非终态不能继续，请新建一次模块分析。"
        )
    contract_path = run_dir / "inputs" / "task-contract.json"
    if not contract_path.is_file():
        raise ValueError(
            f"冻结 task contract 不存在：{contract_path}。"
            "该 Run 可能是在旧版本初始化阶段中断；请使用原 pending-task-contract.json "
            "重新执行 module-analysis 以建立可恢复状态。"
        )
    return run_module_analysis(str(contract_path))
