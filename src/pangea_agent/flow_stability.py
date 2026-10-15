"""Frozen input facts and exact-identity flow comparisons, never semantic verdicts.

No history discovery, label matching, graph rewriting or baseline substitution is
performed here. Baselines are selected by exact Run/version and exposed only to
the comparison reviewer. Missing identities remain visible as uncertainty.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.flow_schema import flow_stability_warnings
from pangea_agent.graph.result_store import active_records, read_result
from pangea_agent.graph.workflow_store import run_directory
from pangea_agent.models.contract import ModelSettings


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _inside(root: Path, path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"流程稳定性输入越过 Run 边界：{path}")
    return path


def canonical_flow(flow: dict) -> dict:
    """Stable presentation order only; never edit a path's actual node sequence."""
    result = deepcopy(flow)
    if flow.get("layout_version") != "stable-v1":
        return result
    keys = {n.get("id"): n.get("semantic_key") or n.get("id") for n in _list(flow.get("nodes")) if isinstance(n, dict)}
    for field, key in (
        ("nodes", lambda n: (str(n.get("semantic_key") or n.get("id") or ""), _json(n))),
        ("edges", lambda e: (str(e.get("edge_id") or ""), str(keys.get(e.get("source_step_key"), "")),
                              str(keys.get(e.get("target_step_key"), "")), _json(e))),
        ("paths", lambda p: (str(p.get("path_id") or ""), _json(p))),
    ):
        if isinstance(result.get(field), list):
            result[field] = sorted(result[field], key=lambda item: key(item) if isinstance(item, dict) else ("", _json(item)))
    if isinstance(result.get("main_path_ids"), list):
        result["main_path_ids"] = sorted(result["main_path_ids"], key=_json)
    return result


def freeze_flow_inputs(state: dict, manifest: dict, scene: dict | None) -> dict | None:
    """Called once while preparing a new Run; old frozen Runs are never upgraded."""
    if not scene or scene.get("flow_layout_version") != "stable-v1":
        return None
    run = run_directory(state).resolve()
    path = run / "inputs/flow-stability.json"
    if path.is_file():
        freeze_selected_baseline(state)
        return read_json(path)
    contract = state["task_contract"]
    from pangea_agent.documents.incremental import _source_files
    sources = [{"repo_id": repo, "path": relative, "sha256": _file_hash(source)}
               for (repo, relative), source in sorted(_source_files(run, manifest).items())]
    assets_path = run / "inputs/asset-snapshots.json"
    snapshots = read_json(assets_path) if assets_path.is_file() else []
    assets = sorted([{"asset_id": item.get("asset_id"), "input_revision": item.get("input_revision"),
                      "content_sha256": _hash(item.get("result"))} for item in snapshots], key=_json)
    examples_path = run / "inputs/test-case-examples.json"
    examples = sorted(_file_hash(_inside(run, Path(p))) for p in read_json(examples_path)) if examples_path.is_file() else []
    rules_root = run / "inputs/methodologies"
    rules = [{"path": p.relative_to(rules_root).as_posix(), "sha256": _file_hash(_inside(run, p))}
             for p in sorted(rules_root.rglob("*.md"))]
    declared = ModelSettings.model_validate(contract.get("model_settings") or {}).model_dump(exclude_none=True)
    complete_model_metadata = (contract.get("model_id") and declared.get("provider")
                               and (declared.get("model_revision") or declared.get("system_fingerprint"))
                               and all(name in declared for name in ("temperature", "top_p", "seed")))
    model = {"model_id": contract.get("model_id"), "settings": declared,
             "metadata_status": "declared" if complete_model_metadata else "partial" if contract.get("model_id") or declared else "unknown"}
    scope = {"requested": sorted(manifest.get("requested_scope", [])),
             "owned": sorted([{"repo_id": g["repo_id"], "paths": sorted(g.get("code_paths", []))}
                              for g in manifest.get("scope_expansion", {}).get("groups", [])], key=_json),
             "context": sorted([{"repo_id": g["repo_id"], "paths": sorted(g.get("context_paths", []))}
                                for g in manifest.get("scope_expansion", {}).get("groups", [])], key=_json)}
    dimensions = {"source": sources, "scope": scope, "assets": {"snapshots": assets, "examples": examples},
                  "methodology": {"scene": scene, "rules": rules}, "model": model,
                  "objective": {"target": contract.get("target"), "focus": contract.get("focus", []),
                                "analysis_settings": contract.get("analysis_settings"),
                                "effective_context_budget": contract.get("effective_context_budget"),
                                "incremental_request": contract.get("incremental_request")}}
    payload = {"format_version": "flow-stability-v1", "run_id": state["run_id"],
               "fingerprint": _hash(dimensions), "dimension_hashes": {k: _hash(v) for k, v in dimensions.items()},
               "model": model, "source_files": sources,
               "note": "仅比较冻结输入与已声明模型设置；未知设置不视为一致，不保证模型输出确定。"}
    write_json(path, payload)
    freeze_selected_baseline(state)
    return payload


def freeze_selected_baseline(state: dict) -> None:
    request = state["task_contract"].get("flow_baseline")
    if not request:
        return
    run = run_directory(state).resolve()
    destination = run / "inputs/flow-baseline.json"
    if destination.is_file():
        previous = read_json(destination)
        _verify_baseline(previous, request)
        return
    from pangea_agent.documents.incremental import _run
    parent = _run(state["data_root"], request["run_id"])
    if parent == run:
        raise ValueError("流程基线必须来自显式选择的其他 Run")
    snapshot = read_json(_inside(parent, parent / "outputs/flow-baseline.json"))
    progress = read_json(_inside(parent, parent / "progress.json"))
    _verify_baseline(snapshot, request)
    if (progress.get("lifecycle_status") != "complete" or progress.get("quality_status") != "PASS"
            or progress.get("partial_delivery")):
        raise ValueError("所选流程基线不是完整审核通过的冻结版本")
    write_json(destination, snapshot)


def _verify_baseline(snapshot: dict, request: dict) -> None:
    if (snapshot.get("run_id") != request.get("run_id") or snapshot.get("version_id") != request.get("version_id")
            or snapshot.get("format_version") != "flow-baseline-v1"):
        raise ValueError("流程基线 Run/version 身份不匹配，请重新选择")
    if snapshot.get("eligible") is not True or snapshot.get("review_status") != "PASS":
        raise ValueError("所选流程基线不是完整审核通过的冻结版本")
    expected_version = "flow-" + _hash({k: v for k, v in snapshot.items() if k != "version_id"})[:24]
    if expected_version != request["version_id"]:
        raise ValueError("流程基线内容指纹损坏")


def compare_inputs(baseline: dict, candidate: dict) -> dict:
    dimensions = {}
    for name in ("source", "scope", "assets", "methodology", "model", "objective"):
        old, new = baseline.get("dimension_hashes", {}).get(name), candidate.get("dimension_hashes", {}).get(name)
        dimensions[name] = "unknown" if not old or not new else "same" if old == new else "changed"
    old_model, new_model = baseline.get("model", {}), candidate.get("model", {})
    if old_model.get("metadata_status") != "declared" or new_model.get("metadata_status") != "declared":
        known_old = {**old_model.get("settings", {}), "model_id": old_model.get("model_id")}
        known_new = {**new_model.get("settings", {}), "model_id": new_model.get("model_id")}
        known_difference = any(known_old[k] != known_new[k] for k in known_old.keys() & known_new.keys()
                               if known_old[k] is not None and known_new[k] is not None)
        dimensions["model"] = "changed" if known_difference else "unknown"
    status = "different_inputs" if "changed" in dimensions.values() else "unknown" if "unknown" in dimensions.values() else "comparable"
    return {"status": status, "dimensions": dimensions,
            "notes": ["输入不一致时只列出结构差异，不能归因为模型不稳定。", "模型比较限已声明设置，未提供的设置与服务端变化仍未知。"]}


def _topology(flow: dict) -> tuple[dict, list[str]]:
    warnings = flow_stability_warnings(flow)
    if flow.get("layout_version") != "stable-v1":
        warnings.append("旧流程缺少 stable-v1 身份，不推测跨 Run 节点对应关系")
    nodes, node_keys, edges, paths = {}, {}, {}, {}
    for node in _list(flow.get("nodes")):
        if not isinstance(node, dict):
            continue
        key = node.get("semantic_key")
        if not isinstance(key, str) or not key or key in nodes:
            continue
        node_keys[node.get("id")] = key
        nodes[key] = {"kind": node.get("kind")}
    for edge in _list(flow.get("edges")):
        if not isinstance(edge, dict) or not isinstance(edge.get("edge_id"), str):
            continue
        edges[edge["edge_id"]] = {"source": node_keys.get(edge.get("source_step_key")),
                                  "target": node_keys.get(edge.get("target_step_key")),
                                  "condition": edge.get("condition", "")}
    for path in _list(flow.get("paths")):
        if not isinstance(path, dict) or not isinstance(path.get("path_id"), str):
            continue
        paths[path["path_id"]] = {"nodes": [node_keys.get(n) for n in path.get("node_ids", [])],
                                  "edges": path.get("edge_ids"), "condition": path.get("condition", "")}
    main_paths = flow.get("main_path_ids")
    return {"nodes": nodes, "edges": edges, "paths": paths,
            "main_path_ids": sorted(main_paths, key=_json) if isinstance(main_paths, list) else [],
            "role": flow.get("role"), "attach_to": flow.get("attach_to")}, warnings


def _mapping_diff(before: dict, after: dict) -> dict:
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": [{"identity": key, "before": before[key], "after": after[key]}
                        for key in sorted(set(before) & set(after)) if before[key] != after[key]]}


def _content_fields(flow: dict) -> dict:
    """Keep wording/evidence changes reviewable without judging equivalence."""
    result = {}
    for group, identity, exclude in (
        ("nodes", "semantic_key", {"id", "semantic_key", "kind"}),
        ("edges", "edge_id", {"edge_id", "source_step_key", "target_step_key", "condition"}),
        ("paths", "path_id", {"path_id", "node_ids", "edge_ids", "condition"}),
    ):
        for item in _list(flow.get(group)):
            if isinstance(item, dict) and isinstance(item.get(identity), str):
                result[f"{group}:{item[identity]}"] = {k: v for k, v in item.items() if k not in exclude}
    result["flow"] = {k: v for k, v in flow.items() if k not in {
        "nodes", "edges", "paths", "flow_id", "main_path_ids", "layout_version", "role", "attach_to"}}
    return result


def compare_flows(baseline: list[dict], candidate: list[dict]) -> tuple[list[dict], list[str]]:
    warnings = []
    def index(entries):
        indexed = {}
        duplicate = set()
        for entry in entries:
            body = entry.get("body", {})
            key = body.get("flow_id") if isinstance(body, dict) else None
            if not isinstance(key, str) or not key:
                warnings.append("有流程缺少 flow_id，未推测对应关系；原始内容保留")
                continue
            if key in indexed:
                duplicate.add(key)
            indexed[key] = body
        for key in duplicate:
            indexed.pop(key)
            warnings.append(f"流程身份 {key} 重复，未推测单元或标签对应关系")
        return indexed, duplicate
    before, dup_before = index(baseline)
    after, dup_after = index(candidate)
    diffs = []
    for key in sorted(set(before) | set(after) | dup_before | dup_after):
        if key in dup_before | dup_after:
            diffs.append({"flow_id": key, "status": "unresolved", "warnings": ["重复流程身份"]})
            continue
        if key not in before or key not in after:
            diffs.append({"flow_id": key, "status": "added" if key in after else "removed"})
            continue
        old, old_warnings = _topology(before[key])
        new, new_warnings = _topology(after[key])
        content_changes = _mapping_diff(_content_fields(before[key]), _content_fields(after[key]))
        entry = {"flow_id": key, **{name: _mapping_diff(old[name], new[name]) for name in ("nodes", "edges", "paths")},
                 "main_paths_changed": old["main_path_ids"] != new["main_path_ids"],
                 "attachment_changed": (old["role"], old["attach_to"]) != (new["role"], new["attach_to"]),
                 "presentation_changed": canonical_flow(before[key]) != canonical_flow(after[key]),
                 "content_changed": any(content_changes.values()), "content_changes": content_changes,
                 "warnings": [*old_warnings, *new_warnings]}
        entry["status"] = "unresolved" if entry["warnings"] else "unchanged" if old == new else "changed"
        diffs.append(entry)
    return diffs, warnings


def _flow_records(records: list[tuple[dict, Any]], *, partial: bool = False) -> list[dict]:
    def delivered(action):
        return action.get("status") == "accepted" or (partial and action.get("delivery_revision") is not None)
    closure_units = {a.get("task", {}).get("unit_id") for a, _ in records
                     if a.get("stage") == "targeted_closure" and delivered(a)}
    flows = []
    for action, result in records:
        unit = action.get("task", {}).get("unit_id")
        if not delivered(action) or action.get("stage") not in {"unit_analysis", "targeted_closure"}:
            continue
        if action.get("stage") == "unit_analysis" and unit in closure_units:
            continue
        for record in active_records(result):
            if record.kind == "flow":
                flows.append({"action_id": action["action_id"], "unit_id": unit, "record_id": record.record_id,
                              "revision": result.revision, "body": record.body})
    return sorted(flows, key=lambda f: (str(f["unit_id"]), f["action_id"], f["record_id"]))


def publish_flow_baseline(state: dict, progress: dict, records: list[tuple[dict, Any]]) -> dict | None:
    run = run_directory(state)
    fingerprint = run / "inputs/flow-stability.json"
    baseline_path = run / "inputs/flow-baseline.json"
    if fingerprint.is_file() and baseline_path.is_file() and progress.get("lifecycle_status") in {"complete", "stopped"}:
        # The Reviewer's frozen first-round comparison is not overwritten after
        # closure. Final delivery gets its own mechanical comparison artifact.
        baseline = read_json(baseline_path)
        contract = state.get("task_contract") or read_json(run / "inputs/task-contract.json")
        _verify_baseline(baseline, contract.get("flow_baseline") or {})
        final_flows = _flow_records(records, partial=True)
        diffs, warnings = compare_flows(baseline.get("flows", []), final_flows)
        write_json(run / "outputs/flow-delivery-comparison.json", {
            "format_version": "flow-comparison-v1", "comparison_scope": "delivery", "reviewed": False,
            "baseline": {k: baseline[k] for k in ("run_id", "version_id")},
            "candidate": {"run_id": state["run_id"], "revisions": [{k: f[k] for k in ("action_id", "revision", "record_id")} for f in final_flows]},
            "comparability": compare_inputs(baseline.get("input_fingerprint", {}), read_json(fingerprint)),
            "flows": diffs, "warnings": warnings,
            "summary": {status: sum(d["status"] == status for d in diffs) for status in ("unchanged", "changed", "added", "removed", "unresolved")},
            "note": "交付修订的机械差异，不是新一轮语义审核；首轮 Reviewer 输入仍冻结于 inputs/flow-comparison.json。",
        })
    if not fingerprint.is_file() or progress.get("lifecycle_status") != "complete" or progress.get("quality_status") != "PASS" or progress.get("partial_delivery"):
        return None
    flows = _flow_records(records)
    if not flows:
        return None
    selected_actions = {f["action_id"] for f in flows}
    for action, result in records:
        if action["action_id"] not in selected_actions:
            continue
        if (progress.get("accepted_revisions", {}).get(action["action_id"]) != result.revision
                or result.binding.run_id != state["run_id"] or result.binding.action_id != action["action_id"]
                or result.binding.task_id != action.get("task_id")
                or Path(result.binding.data_root).resolve() != Path(state["data_root"]).resolve()):
            raise ValueError("流程基线不能发布：交付结果与已接受修订或身份绑定不一致")
    contract = state.get("task_contract") or read_json(run / "inputs/task-contract.json")
    payload = {"format_version": "flow-baseline-v1", "run_id": state["run_id"], "review_status": "PASS", "eligible": True,
               "repository": contract.get("repository"), "repositories": contract.get("repositories", []),
               "target": contract.get("target"), "scenario": (contract.get("analysis_settings") or {}).get("scenario"),
               "input_fingerprint": read_json(fingerprint),
               "flow_count": len(flows), "flows": flows,
               "warnings": [warning for f in flows if isinstance(f["body"], dict) for warning in flow_stability_warnings(f["body"])]}
    payload["version_id"] = "flow-" + _hash(payload)[:24]
    write_json(run / "outputs/flow-baseline.json", payload)
    return payload


def attach_flow_comparison(state: dict, task: dict) -> None:
    """Comparison-only inputs: absent from independent review task authority."""
    if task.get("review_stage") != "comparison_review":
        return
    run = run_directory(state)
    baseline_path = run / "inputs/flow-baseline.json"
    fingerprint_path = run / "inputs/flow-stability.json"
    if not baseline_path.is_file() or not fingerprint_path.is_file():
        return
    baseline = read_json(baseline_path)
    _verify_baseline(baseline, state["task_contract"].get("flow_baseline") or {})
    version_set = read_json(_inside(run, Path(task["version_set_path"])))
    current = []
    for entry in version_set.get("entries", []):
        if entry.get("role") != "analysis":
            continue
        result = read_result(_inside(run, Path(entry["result_path"])))
        if (result.revision != entry["revision"] or result.binding.run_id != state["run_id"]
                or result.binding.action_id != entry["action_id"] or result.binding.task_id != entry["task_id"]
                or Path(result.binding.data_root).resolve() != Path(state["data_root"]).resolve()):
            raise ValueError("流程对照的已接受结果版本或身份不一致")
        for record in active_records(result):
            if record.kind == "flow":
                current.append({"body": record.body, "record_id": record.record_id, "action_id": entry["action_id"]})
    diffs, warnings = compare_flows(baseline.get("flows", []), current)
    payload = {"format_version": "flow-comparison-v1", "comparison_scope": "first_round", "baseline": {k: baseline[k] for k in ("run_id", "version_id")},
               "candidate": {"run_id": state["run_id"], "version_set_id": task["version_set_id"]},
               "comparability": compare_inputs(baseline.get("input_fingerprint", {}), read_json(fingerprint_path)),
               "flows": diffs, "warnings": warnings,
               "note": "unchanged 仅表示精确身份拓扑未变；文字与源码依据变化须由 Reviewer 判断，不能据此认定业务等价。",
               "summary": {status: sum(d["status"] == status for d in diffs) for status in ("unchanged", "changed", "added", "removed", "unresolved")}}
    comparison_path = run / "inputs/flow-comparison.json"
    write_json(comparison_path, payload)
    task.setdefault("inputs", []).extend([
        {"input_id": "flow_baseline", "path": str(baseline_path), "label": "显式选择的已审核流程基线；不替代本次分析"},
        {"input_id": "flow_comparison", "path": str(comparison_path), "label": "输入指纹与精确身份结构差异；变更依据由 Reviewer 核实"},
    ])


def attach_flow_stability_input(state: dict, task: dict) -> None:
    path = run_directory(state) / "inputs/flow-stability.json"
    if path.is_file():
        task.setdefault("inputs", []).append({"input_id": "flow_stability", "path": str(path),
                                               "label": "本次冻结输入指纹与已声明模型元数据"})
