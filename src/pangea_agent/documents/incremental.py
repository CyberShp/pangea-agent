"""Freeze explicitly selected prior-Run evidence without accepting it as new work.

Only identities, containment, byte hashes and user selections are interpreted here.
Planning and Review retain responsibility for semantic impact and applicability.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.result_store import active_records, read_result
from pangea_agent.graph.workflow_store import run_directory
from pangea_agent.models.contract import IncrementalRequest


def _inside(root: Path, path: str | Path) -> Path:
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"增量分析输入越过 Run 边界：{path}")
    return path


def _run(data_root: str, run_id: str) -> Path:
    if not run_id or run_id in {".", ".."} or any(c in run_id for c in "/\\"):
        raise ValueError("parent_run_id 必须是明确 Run ID，不能包含路径分隔符")
    root = (Path(data_root) / "runs").resolve()
    path = _inside(root, root / run_id)
    if path.parent != root or not path.is_dir():
        raise ValueError(f"父 Run 不存在：{run_id}")
    return path


def _json(run: Path, relative: str):
    return read_json(_inside(run, run / relative))


def _source_files(run: Path, manifest: dict) -> dict[tuple[str, str], Path]:
    roots = {r["repo_id"]: _inside(run, r["source_root"]) for r in manifest["repositories"]}
    files = {}
    for group in manifest["scope_expansion"]["groups"]:
        for relative in dict.fromkeys([*group.get("code_paths", []), *group.get("context_paths", [])]):
            repo = group["repo_id"]
            path = _inside(roots[repo], roots[repo] / relative)
            if not path.is_file():
                raise ValueError(f"父 Run 源码快照缺失：{repo}:{relative}")
            files[repo, relative] = path
    return files


def _parent(data_root: str, run_id: str):
    run = _run(data_root, run_id)
    contract = _json(run, "inputs/task-contract.json")
    if contract.get("workflow_version") != "source-first-v1":
        raise ValueError("该历史 Run 不支持增量分析，需要 source-first-v1 冻结输入")
    progress = _json(run, "progress.json")
    if progress.get("lifecycle_status") not in {"complete", "stopped"}:
        raise ValueError("请等待父 Run 完成或先停止父 Run，再创建定向补充；运行中的输入尚未稳定")
    manifest = _json(run, "inputs/source-manifest.json")
    files = _source_files(run, manifest)
    if not files:
        raise ValueError("父 Run 没有可复用的冻结源码快照")
    plan = _json(run, "inputs/source-first-plan.json")
    entries = []
    for action_id, action in progress.get("actions", {}).items():
        if action.get("stage") not in {"unit_analysis", "targeted_closure"}:
            continue
        if action.get("status") != "accepted" and action.get("delivery_revision") is None:
            continue
        task = read_json(_inside(run, action["task_path"]))
        result_path = _inside(run, task["result_path"])
        result = read_result(result_path)
        if (result.binding.run_id != run_id or result.binding.action_id != action_id
                or Path(result.binding.data_root).resolve() != Path(data_root).resolve()
                or (action.get("task_id") and result.binding.task_id != action["task_id"])):
            raise ValueError(f"父 Run 结果身份绑定不一致：{action_id}")
        expected = action.get("delivery_revision") if action.get("delivery_revision") is not None else progress.get("accepted_revisions", {}).get(action_id)
        if expected is not None and result.revision != expected:
            raise ValueError(f"父 Run 结果已变化，请重新确认冻结修订：{action_id}")
        entries.append({"action_id": action_id, "unit_id": task.get("unit_id"),
                        "revision": result.revision, "status": action.get("status"),
                        "result": result, "result_path": result_path})
    return run, contract, progress, manifest, files, plan, entries


def _records(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        for record in active_records(entry["result"]):
            body = record.body if isinstance(record.body, dict) else {}
            result.append({"action_id": entry["action_id"], "unit_id": entry["unit_id"],
                           "record_id": record.record_id, "kind": record.kind,
                           "title": str(body.get("title") or body.get("name") or record.record_id),
                           "revision": entry["revision"]})
    return result


def derivation_options(data_root: str, run_id: str) -> dict:
    run = _run(data_root, run_id)  # Invalid or escaping identities remain an error.
    base = {"parent_run_id": run_id, "can_derive": False, "blocked_reason": None,
            "units": [], "records": [], "warnings": []}
    try:
        contract, progress = _json(run, "inputs/task-contract.json"), _json(run, "progress.json")
        base.update(workflow_version=contract.get("workflow_version"), repository=contract.get("repository"),
                    repositories=contract.get("repositories", []), lifecycle_status=progress.get("lifecycle_status"),
                    quality_status=progress.get("quality_status"))
    except (OSError, ValueError, TypeError):
        pass
    try:
        _, contract, progress, _, _, plan, entries = _parent(data_root, run_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {**base, "blocked_reason": str(exc)}
    return {**base, "can_derive": True, "workflow_version": contract["workflow_version"],
            "repository": contract.get("repository"), "repositories": contract.get("repositories", []),
            "lifecycle_status": progress.get("lifecycle_status"), "quality_status": progress.get("quality_status"),
            "units": plan.get("units", []), "records": _records(entries)}


def _changed_keys(request: dict, repo_ids: set[str]) -> list[tuple[str, str]]:
    result = []
    for raw in request["changed_paths"]:
        value = raw.replace("\\", "/")
        if ":" in value:
            repo, relative = value.split(":", 1)
        elif len(repo_ids) == 1:
            repo, relative = next(iter(repo_ids)), value
        else:
            raise ValueError("多仓文件变更必须使用 repo_id:path")
        if (repo not in repo_ids or not relative or PurePosixPath(relative).is_absolute()
                or PureWindowsPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts):
            raise ValueError(f"changed_paths 必须是仓库内相对文件路径：{raw}")
        key = (repo, str(PurePosixPath(relative)))
        if key not in result:
            result.append(key)
    return result


def prepare_incremental(state: dict) -> dict | None:
    raw = state["task_contract"].get("incremental_request")
    if not raw:
        return None
    request = IncrementalRequest.model_validate(raw).model_dump(mode="json")
    if request["parent_run_id"] == state["run_id"]:
        raise ValueError("增量分析必须新建独立 Run，不能复用父 Run ID")
    parent, contract, progress, manifest, sources, plan, entries = _parent(state["data_root"], request["parent_run_id"])
    child = run_directory(state).resolve()
    repo_ids = set(contract.get("repositories") or [contract.get("repository")])
    current_repos = set(state["task_contract"].get("repositories") or [state["task_contract"].get("repository")])
    if repo_ids != current_repos:
        raise ValueError("增量分析必须沿用父 Run 的仓库身份")
    units = {unit["unit_id"] for unit in plan.get("units", [])}
    if not set(request["selected_unit_ids"]).issubset(units):
        raise ValueError("选中的单元不属于父 Run 冻结计划")
    records = _records(entries)
    addresses = {(r["action_id"], r["record_id"]) for r in records}
    if any((r["action_id"], r["record_id"]) not in addresses for r in request["selected_records"]):
        raise ValueError("选中的记录不属于父 Run 可复用结果；请刷新候选记录")
    root = child / "inputs" / "incremental"
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "request.json", request)
    write_json(root / "parent-contract.json", contract)
    write_json(root / "parent-plan.json", plan)
    references = []
    reference_text = []
    offset = 0
    for entry in entries:
        frozen = {key: entry[key] for key in ("action_id", "unit_id", "revision", "status")}
        frozen.update(parent_run_id=request["parent_run_id"], reference_only=True,
                      records=[r.model_dump(mode="json") for r in active_records(entry["result"])])
        text = json.dumps(frozen, ensure_ascii=False) + "\n"
        reference_text.append(text)
        references.append({**{k: frozen[k] for k in ("action_id", "unit_id", "revision", "status")},
                           "input_id": "incremental_parent_records", "chars": len(text),
                           "cursor": base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")})
        offset += len(text)
    (root / "parent-records.jsonl").write_text("".join(reference_text), encoding="utf-8")
    baseline_files = [{"repo_id": repo, "path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                      for (repo, relative), path in sorted(sources.items())]
    # Freeze asset material as reference even in changed-files mode; never refresh
    # it during resume or relabel historical coverage as a new measurement.
    for name in ("asset-candidates.json", "asset-items.json", "coverage-gaps.json", "coverage-diagnostics.json",
                 "asset-snapshots.json", "coverage-match-summary.json"):
        path = _inside(parent, parent / "inputs" / name)
        if path.is_file():
            shutil.copyfile(path, root / name)
    examples = []
    for index, raw_path in enumerate(_json(parent, "inputs/test-case-examples.json")):
        source = _inside(parent, raw_path)
        target = root / "examples" / f"{index:04d}-{source.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        examples.append(str(target))
    previous_rules = {p.name: hashlib.sha256(_inside(parent, p).read_bytes()).hexdigest()
                      for p in (parent / "inputs/methodologies/builtin").glob("*.md")}
    metadata = {"parent_run_id": request["parent_run_id"], "mode": request["mode"],
                "parent_lifecycle_status": progress.get("lifecycle_status"), "parent_quality_status": progress.get("quality_status"),
                "source_policy": "parent-frozen" if request["mode"] == "supplement" else "current-working-tree",
                "reference_only": True, "baseline_files": baseline_files, "references": references,
                "records": records, "rule_hashes": previous_rules,
                "applicability": "requires_agent_revalidation", "coverage_policy": "parent-frozen-historical",
                "historical_paths_are_provenance_only": True}
    write_json(root / "baseline.json", metadata)
    changed = _changed_keys(request, repo_ids)
    for index, key in enumerate(changed, 1):
        if key in sources:
            target = root / f"before-{index:04d}.txt"
            shutil.copyfile(sources[key], target)
    return {"root": root, "request": request, "repositories": manifest["repositories"],
            "parent_manifest": manifest, "changed_keys": changed, "examples": examples, "metadata": metadata}


def incremental_assets(context: dict) -> dict:
    root = context["root"]
    def optional(name, default):
        path = root / name
        return read_json(path) if path.is_file() else default
    return {"candidates": optional("asset-candidates.json", []), "items": optional("asset-items.json", {}),
            "coverage_records": [], "coverage_diagnostics": optional("coverage-diagnostics.json", []),
            "snapshots": optional("asset-snapshots.json", [])}


def finish_incremental(context: dict | None, run_dir: Path, manifest: dict) -> None:
    if not context:
        return
    baseline = {(f["repo_id"], f["path"]): f["sha256"] for f in context["metadata"]["baseline_files"]}
    current = _source_files(run_dir, manifest)
    changes = []
    for index, (repo, relative) in enumerate(context["changed_keys"], 1):
        path = current.get((repo, relative))
        before = baseline.get((repo, relative))
        after = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
        status = ("unchanged" if before and before == after else "modified" if before and after
                  else "deleted" if before else "baseline-missing" if after else "missing-both")
        entry = {"repo_id": repo, "path": relative, "status": status, "before_sha256": before,
                 "after_sha256": after, "baseline_input_id": f"baseline_source_{index:04d}" if before else None}
        before_path = context["root"] / f"before-{index:04d}.txt"
        previous = before_path.read_text(encoding="utf-8", errors="replace") if before_path.is_file() else ""
        following = path.read_text(encoding="utf-8", errors="replace") if path else ""
        diff_path = context["root"] / f"diff-{index:04d}.txt"
        diff_path.write_text("".join(difflib.unified_diff(previous.splitlines(keepends=True), following.splitlines(keepends=True),
                                                       fromfile=f"parent/{repo}/{relative}", tofile=f"current/{repo}/{relative}")), encoding="utf-8")
        entry["diff_input_id"] = f"source_diff_{index:04d}"
        changes.append(entry)
    current_rules = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (run_dir / "inputs/methodologies/builtin").glob("*.md")}
    write_json(context["root"] / "changes.json", {
        "changed_files": changes, "rules_changed": current_rules != context["metadata"]["rule_hashes"],
        "applicability": "requires_agent_revalidation",
        "note": "字节变化只描述用户指定文件；未扫描语义影响。旧记录、规则和覆盖率均须按当前任务核验，不能直接计为新成果。"})


def attach_incremental_inputs(run_dir: Path, task: dict) -> None:
    root = run_dir / "inputs" / "incremental"
    if not (root / "request.json").is_file():
        return
    request = read_json(root / "request.json")
    task["incremental_request"] = request
    inputs = [("incremental_request", root / "request.json", "本次定向补充/文件变更目标"),
              ("incremental_baseline", root / "baseline.json", "父 Run 记录索引；仅供引用，需重新核验"),
              ("incremental_parent_plan", root / "parent-plan.json", "父 Run 单元范围及必要依赖"),
              ("incremental_parent_records", root / "parent-records.jsonl", "父记录原文；按 baseline 索引的 cursor 读取"),
              ("incremental_changes", root / "changes.json", "用户指定文件前后哈希、缺失与规则变更"),
              ("incremental_rubric", run_dir / "inputs/methodologies/builtin/incremental_analysis.md", "增量分析职责")]
    task.setdefault("rubric_paths", []).append(str(inputs[-1][1]))
    # Historical accepted records are baseline material, never the current Run's
    # initial analysis results. Standard independent review remains independent.
    for index, _ in enumerate(request["changed_paths"], 1):
        path = root / f"before-{index:04d}.txt"
        if path.is_file():
            inputs.append((f"baseline_source_{index:04d}", path, "变更前源码，仅作历史证据"))
        diff_path = root / f"diff-{index:04d}.txt"
        if diff_path.is_file():
            inputs.append((f"source_diff_{index:04d}", diff_path, "指定文件字节差异；影响由 Agent 判定"))
    task.setdefault("inputs", []).extend({"input_id": key, "path": str(path), "label": label}
                                          for key, path, label in inputs)
