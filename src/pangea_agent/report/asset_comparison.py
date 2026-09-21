"""Read-only evidence for a deliberately selected pair of asset experiments.

Counts describe frozen inputs and explicit references, never semantic benefit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json
from pangea_agent.execution_metrics import summarize_execution
from pangea_agent.graph.result_store import active_records, read_result
from pangea_agent.report.source_first import _body_mapping


def _inside(root: Path, path: str | Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"comparison input is outside Run boundary: {path}")
    return resolved


def _optional(run: Path, relative: str, warnings: list[str]) -> Any:
    path = _inside(run, run / relative)
    if not path.is_file():
        warnings.append(f"not recorded: {relative}")
        return None
    return read_json(path)


def _fingerprint_sources(run: Path, manifest: dict | None) -> list[dict] | None:
    if not manifest or not manifest.get("scope_expansion", {}).get("groups"):
        return None
    roots = {repo["repo_id"]: _inside(run, repo["source_root"])
             for repo in manifest.get("repositories", [])}
    sources = []
    for group in manifest["scope_expansion"]["groups"]:
        repo_id = group["repo_id"]
        for role in ("code_paths", "context_paths"):
            for relative in group.get(role, []):
                path = _inside(roots[repo_id], roots[repo_id] / relative)
                sources.append({"repo_id": repo_id, "path": relative,
                                "role": role, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return sorted(sources, key=lambda item: (item["repo_id"], item["path"], item["role"]))


def _strings(value: Any) -> set[str]:
    """Read explicit relation values; no prose search or semantic inference."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return set().union(*(_strings(item) for item in value)) if value else set()
    return set()


def _case_records(run: Path, progress: dict | None, warnings: list[str]) -> tuple[list[dict], bool]:
    if not isinstance(progress, dict) or not isinstance(progress.get("actions"), dict):
        warnings.append("not recorded: actions; case total is unknown")
        return [], False
    complete = progress.get("lifecycle_status") == "complete"
    actions = []
    for action_id, action in progress["actions"].items():
        if action.get("stage") not in {"unit_analysis", "targeted_closure"}:
            continue
        if not (action.get("status") == "accepted" or action.get("delivery_revision") is not None):
            continue
        task_path = action.get("task_path")
        if not task_path:
            warnings.append(f"not recorded: task_path for {action_id}")
            complete = False
            continue
        task = read_json(_inside(run, task_path))
        actions.append((action_id, action, task))
    closure_units = {task.get("unit_id") for _, action, task in actions
                     if action.get("stage") == "targeted_closure"}
    cases = []
    for action_id, action, task in actions:
        if action.get("stage") == "unit_analysis" and task.get("unit_id") in closure_units:
            continue
        result_path = task.get("result_path")
        if not result_path:
            warnings.append(f"not recorded: result_path for {action_id}")
            complete = False
            continue
        result = read_result(_inside(run, result_path))
        for record in active_records(result):
            if record.kind not in {"test_case", "test_case_group"}:
                continue
            body = _body_mapping(record.body) or {}
            refs = _strings([record.evidence, record.relates_to,
                             *[body.get(key) for key in ("coverage_refs", "asset_item_ids", "methodology_ids")]])
            cases.append({"action_id": action_id, "unit_id": task.get("unit_id"),
                          "record_id": record.record_id, "case_id": body.get("case_id"),
                          "kind": record.kind, "body": record.body,
                          "evidence": record.evidence, "relates_to": record.relates_to,
                          "explicit_references": sorted(refs), "result_path": str(result_path)})
    return cases, complete


def _run_evidence(data_root: str, run_id: str) -> tuple[dict, dict]:
    root = (Path(data_root) / "runs").resolve()
    run = _inside(root, root / run_id)
    if run.parent != root or not run.is_dir():
        raise ValueError(f"Run does not exist: {run_id}")
    warnings: list[str] = []
    contract = _optional(run, "inputs/task-contract.json", warnings) or {}
    progress = _optional(run, "progress.json", warnings)
    manifest = _optional(run, "inputs/source-manifest.json", warnings)
    plan = _optional(run, "inputs/source-first-plan.json", warnings)
    assets = _optional(run, "inputs/asset-items.json", warnings)
    gaps = _optional(run, "inputs/coverage-gaps.json", warnings)
    catalog = _optional(run, "inputs/methodologies/catalog.json", warnings)
    cases, cases_complete = _case_records(run, progress, warnings)
    known = {
        "asset_item_ids": set(assets) if isinstance(assets, dict) else None,
        "coverage_ids": {item["coverage_id"] for item in gaps if item.get("coverage_id")} if isinstance(gaps, list) else None,
        "methodology_ids": {item["methodology_id"] for key in ("enabled_user_methodologies", "builtin_methodologies")
                            for item in catalog.get(key, [])} if isinstance(catalog, dict) else None,
    }
    units = [{"unit_id": unit.get("unit_id"), **{key: unit.get(key) for key in known}}
             for unit in plan["units"]] if isinstance(plan, dict) and isinstance(plan.get("units"), list) else None
    linkage = {}
    for key, identifiers in known.items():
        selected = None
        if units is not None and all(isinstance(unit[key], list) for unit in units):
            selected = set().union(*(_strings(unit[key]) for unit in units)) if units else set()
        linked_cases = []
        for case in cases:
            matched = sorted(set(case["explicit_references"]) & identifiers) if identifiers is not None else None
            case.setdefault("input_links", {})[key] = matched
            if matched:
                linked_cases.append({"action_id": case["action_id"], "record_id": case["record_id"], "ids": matched})
        linkage[key] = {"available_ids": sorted(identifiers) if identifiers is not None else None,
                        "planned_ids": sorted(selected) if selected is not None else None,
                        "unverified_planned_ids": sorted(selected - identifiers) if selected is not None and identifiers is not None else None,
                        "linked_case_records": linked_cases if identifiers is not None else None}
    settings_keys = ("workflow_version", "analysis_profile", "analysis_settings", "model_id",
                     "effective_context_budget", "mode", "target", "focus", "source_scope", "context_scope")
    settings = {key: contract.get(key) for key in settings_keys}
    provenance = contract.get("runtime_provenance")
    runtime = {key: value for key, value in provenance.items() if key != "recorded_at"} if isinstance(provenance, dict) else None
    builtin = _inside(run, run / "inputs/methodologies/builtin")
    rubrics = {path.name: hashlib.sha256(_inside(run, path).read_bytes()).hexdigest() for path in sorted(builtin.glob("*.md"))} if builtin.is_dir() else None
    examples = _optional(run, "inputs/test-case-examples.json", warnings)
    example_hashes = [hashlib.sha256(_inside(run, path).read_bytes()).hexdigest() for path in examples] if isinstance(examples, list) else None
    comparable = {**settings, "source_snapshot": _fingerprint_sources(run, manifest),
                  "runtime": runtime, "builtin_rubrics": rubrics, "test_case_examples": example_hashes}
    return ({"run_id": run_id, "lifecycle_status": progress.get("lifecycle_status") if progress else None,
             "quality_status": progress.get("quality_status") if progress else None, "selected_asset_ids": contract.get("asset_ids"),
             "frozen_input_paths": {name: str(run / "inputs" / name) for name in (
                 "asset-items.json", "coverage-gaps.json", "methodologies/catalog.json")},
             "units": units, "input_linkage": linkage, "cases": cases,
             "case_record_count": len(cases) if cases_complete else None,
             "case_records_complete": cases_complete,
             "execution_metrics": summarize_execution(progress) if progress and isinstance(progress.get("actions"), dict) else None,
             "warnings": warnings}, comparable)


def compare_run_assets(data_root: str, baseline_run_id: str, candidate_run_id: str) -> dict:
    """Inspect two explicit Runs without writing, settling, or scoring either."""
    if baseline_run_id == candidate_run_id:
        raise ValueError("baseline and candidate must be different Runs")
    baseline, baseline_inputs = _run_evidence(data_root, baseline_run_id)
    candidate, candidate_inputs = _run_evidence(data_root, candidate_run_id)
    checks = []
    for field, left in baseline_inputs.items():
        right = candidate_inputs[field]
        status = "unknown" if left is None or right is None else "same" if left == right else "different"
        checks.append({"field": field, "status": status, "baseline": left, "candidate": right})
    status = "different" if any(item["status"] == "different" for item in checks) else "unknown" if any(item["status"] == "unknown" for item in checks) else "same"
    complete_timing = all(item["lifecycle_status"] == "complete" and item["execution_metrics"] is not None
                          and item["execution_metrics"]["worker_elapsed_ms"] is not None
                          and item["execution_metrics"]["timed_action_count"] == item["execution_metrics"]["action_count"]
                          and not item["execution_metrics"]["unfinished_timed_action_count"] for item in (baseline, candidate))
    complete_cases = baseline["case_records_complete"] and candidate["case_records_complete"]
    return {"baseline": baseline, "candidate": candidate,
            "comparability": {"status": status, "checks": checks},
            "difference": {"case_record_count": candidate["case_record_count"] - baseline["case_record_count"] if complete_cases else None,
                           "worker_elapsed_ms": candidate["execution_metrics"]["worker_elapsed_ms"] - baseline["execution_metrics"]["worker_elapsed_ms"] if complete_timing else None},
            "semantic_assessment": "requires_review",
            "interpretation": [
                "Counts describe records and exact input references, not valid new cases, accuracy, or causal benefit.",
                "Run-local case IDs are not matched across Runs. Review both case bodies for overlap, correctness, and new value.",
                "Planned input selection does not prove adoption in a case; unlinked prose is not interpreted as rejection.",
                "Worker elapsed time is the sum of recorded action execution, not Run wall-clock time; missing metrics stay unknown.",
                "Differences require complete Runs and complete evidence. Draft cases are excluded; incomplete case totals stay unknown.",
                "A different or unknown comparison setting limits attribution to assets. Lifecycle and quality values are copied, not adjudicated.",
            ]}
