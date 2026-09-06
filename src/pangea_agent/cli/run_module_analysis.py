from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

from pangea_agent.agent_io import write_json
from pangea_agent.graph.graph import graph
from pangea_agent.graph.workflow_store import load_progress


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
    allocated_run = False
    if not run_id:
        run_id = _default_run_id(contract)
        allocated_run = True
        contract["run_id"] = run_id
    run_root = Path(contract.get("data_root", "pangea-data")) / "runs" / run_id
    # A missing version is an old frozen contract when progress already
    # exists, but a newly-created contract must opt into the source-first
    # production path explicitly.  This keeps historical Runs readable
    # without making the new client remember an internal version knob.
    if not contract.get("workflow_version") and not (run_root / "progress.json").is_file():
        contract["workflow_version"] = "source-first-v1"
    if (
        contract.get("workflow_version") == "source-first-v1"
        and not (run_root / "progress.json").is_file()
        and "analysis_profile" not in contract
    ):
        contract["analysis_profile"] = "behavior-test-v1"
    if allocated_run or contract.get("workflow_version") == "source-first-v1":
        write_json(path, contract)
    state = {
        "run_id": run_id,
        "data_root": contract.get("data_root", "pangea-data"),
        "task_contract": contract,
    }
    try:
        result = graph.invoke(state)
        if contract.get("workflow_version") != "source-first-v1":
            return result
        progress = load_progress(state)
        response = {
            "run_id": run_id,
            "data_root": str(Path(state["data_root"]).resolve()),
            "workflow_version": "source-first-v1",
            "lifecycle_status": result.get("lifecycle_status", progress.lifecycle_status if progress else "running"),
            "stage": result.get("stage", progress.stage if progress else "preparing"),
            "quality_status": result.get("quality_status", progress.quality_status if progress else None),
            "needs_user": bool(progress.needs_user) if progress else False,
            "blocking_reason": progress.blocking_reason if progress else None,
            "agent_actions": result.get("agent_actions", []),
            "first_finish_revisions": progress.first_finish_revisions if progress else {},
            "accepted_revisions": progress.accepted_revisions if progress else {},
            "report_path": progress.report_path if progress else None,
            "html_report_path": progress.html_report_path if progress else None,
        }
        return response
    except Exception:
        run_dir = Path(state["data_root"]) / "runs" / run_id
        if allocated_run and not (run_dir / "progress.json").is_file():
            shutil.rmtree(run_dir, ignore_errors=True)
        raise


def resume_module_analysis(run_id: str, data_root: str = "pangea-data") -> dict:
    contract_path = Path(data_root) / "runs" / run_id / "inputs" / "task-contract.json"
    if not contract_path.is_file():
        raise ValueError(f"冻结 task contract 不存在，不能恢复 Run：{contract_path}")
    return run_module_analysis(str(contract_path))
