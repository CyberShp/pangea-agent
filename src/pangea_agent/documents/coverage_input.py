"""Coverage input preparation and paging; never infer source or test semantics."""
from __future__ import annotations

import json
import os
import shutil
import signal
import threading
import subprocess
import sys
from uuid import uuid4
from pathlib import Path


def local_query_skill() -> dict:
    root_value = os.environ.get("PANGEA_LOCAL_SKILLS_ROOT")
    root = Path(root_value).resolve() / "coverage-query" if root_value else None
    return {"available": bool(root and (root / "SKILL.md").is_file()
                              and (root / "scripts/coverage_query.py").is_file()),
            "root_path": str(root) if root else None,
            "placement": "<PANGEA 解压目录>/local-skills/coverage-query"}


def normalize_input(value: object) -> dict:
    if not isinstance(value, dict) or value.get("kind") not in {"file", "query", "asset"}:
        raise ValueError("coverage_input.kind 必须是 file、query 或 asset")
    if value["kind"] == "asset":
        if not isinstance(value.get("asset_id"), str) or not value["asset_id"].strip():
            raise ValueError("请选择覆盖率资产")
        return {"kind": "asset", "asset_id": value["asset_id"]}
    if value["kind"] == "file":
        path = value.get("path")
        if not isinstance(path, str) or not Path(path).is_file():
            raise ValueError("coverage_input.path 必须是可读取文件")
        if Path(path).suffix.lower() not in {".json", ".csv", ".xlsx"}:
            raise ValueError("覆盖输入支持 combined JSON 或契约列 CSV/XLSX")
        return {"kind": "file", "path": str(Path(path).resolve())}
    query = value.get("query", {})
    if not isinstance(query, dict):
        raise ValueError("coverage_input.query 必须是对象")
    for key in ("product", "c_version", "module"):
        if not isinstance(query.get(key), str) or not query[key].strip():
            raise ValueError(f"coverage_input.query.{key} 必须是非空字符串")
    if not isinstance(query.get("b_version", ""), str):
        raise ValueError("b_version 必须是字符串")
    return {"kind": "query", "query": {k: query.get(k, "") for k in
            ("product", "c_version", "b_version", "module")}}


def freeze_input(value: dict, run_root: Path, assets: dict | None = None) -> dict:
    folder = run_root / "inputs/coverage"
    folder.mkdir(parents=True)
    frozen = dict(value)
    if value["kind"] == "asset":
        item = next((a for a in (assets or {}).get("assets", []) if a["asset_id"] == value["asset_id"] and a["asset_type"] == "coverage"), None)
        if not item or not item.get("frozen_result_path"):
            raise ValueError("覆盖率资产未冻结或未解析，请重新解析并选择该资产")
        data = read_input(Path(item["frozen_result_path"]))
        (folder / "combined.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        frozen.update(path=item["frozen_result_path"], revision=item["revision"], parser_version=item.get("parser_version"))
    elif value["kind"] == "file":
        target = folder / ("report" + Path(value["path"]).suffix.lower())
        shutil.copy2(value["path"], target)
        frozen["path"] = str(target)
    else:
        capability = local_query_skill()
        if not capability["available"]:
            raise ValueError(f"请放入覆盖率查询 Skill：{capability['placement']}")
        target = run_root / "inputs/coverage-query-skill"
        shutil.copytree(capability["root_path"], target)
        frozen["skill_root"] = str(target)
    (folder / "input.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
    return frozen


def _table(path: Path) -> dict:
    from .coverage_table import read_table, table_combined
    return table_combined(read_table(path))

def read_input(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig")) if path.suffix.lower() == ".json" else _table(path)
    if not isinstance(data, dict) or data.get("status") not in {"success", "partial", "no_data", "error"}:
        raise ValueError("需要 combined JSON，含 status=success/partial/no_data/error")
    for key in ("sources", "uncovered_functions", "uncovered_lines", "uncovered_branches", "missing", "warnings"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f"combined.{key} 必须是数组")
    return data


def gap_records(data: dict) -> list[dict]:
    records, seen = [], set()
    for kind, key in (("function", "uncovered_functions"), ("line", "uncovered_lines"), ("branch", "uncovered_branches")):
        for group in data.get(key, []):
            if not isinstance(group, dict) or not group.get("source") or not group.get("file_path"):
                raise ValueError(f"{key} 明细缺少 source/file_path，不能可靠关联源码")
            for value in group.get(key, []):
                if kind == "branch" and str(value.get("count")) != "0":
                    continue
                identity = json.dumps([group["source"], group["file_path"], kind, value], sort_keys=True, ensure_ascii=False)
                if identity in seen:
                    continue
                seen.add(identity)
                records.append({"gap_id": f"GAP-{len(records) + 1:06d}", "source": group["source"],
                                "file_path": group["file_path"], "kind": kind, "raw": value,
                                "coverage_status": "uncovered"})
    for row in data.get("unlocated_records", []):
        records.append({"gap_id": f"GAP-{len(records) + 1:06d}", "source": row["source"],
                        "file_path": row.get("file_path", ""), "kind": row["kind"], "raw": row,
                        "coverage_status": "uncovered", "location_status": "unresolved"})
    return records


def prepare_coverage(run_root: Path, *, timeout: int = 300, refresh: dict | None = None) -> dict:
    folder = run_root / "inputs/coverage"
    lock = folder / "acquisition.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("覆盖数据正在获取；若宿主异常退出，请先确认原查询进程已结束") from exc
    os.close(descriptor)
    try:
        return _prepare_coverage(run_root, timeout=timeout, refresh=refresh)
    finally:
        lock.unlink(missing_ok=True)


def _prepare_coverage(run_root: Path, *, timeout: int, refresh: dict | None) -> dict:
    folder = run_root / "inputs/coverage"
    frozen = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    output = folder / "combined.json"
    if refresh is not None:
        if frozen["kind"] != "query":
            raise ValueError("只有查询输入支持修正后重新获取")
        corrected = normalize_input({"kind": "query", "query": refresh})
        archive = folder / "acquisitions" / uuid4().hex
        archive.mkdir(parents=True)
        for name in ("input.json", "combined.json", "query-stderr.log"):
            if (folder / name).is_file():
                shutil.copy2(folder / name, archive / name)
        frozen["query"] = corrected["query"]
        (folder / "input.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
        output.unlink(missing_ok=True)
    # Reuse any valid acquisition. Retry failures; a successful frozen input is immutable.
    if output.is_file() and read_input(output)["status"] in {"success", "partial", "no_data"}:
        return coverage_page(run_root)
    if frozen["kind"] == "file":
        data = read_input(Path(frozen["path"]))
    else:
        query = frozen["query"]
        argv = [sys.executable, str(Path(frozen["skill_root"]) / "scripts/coverage_query.py"), "combined",
                "--product", query["product"], "--version", query["c_version"], "--module", query["module"]]
        if query.get("b_version"):
            argv += ["--b-version", query["b_version"]]
        temporary = folder / "query-output.tmp"
        with temporary.open("wb") as stdout, (folder / "query-stderr.log").open("wb") as stderr:
            child = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
            previous_term = None
            if threading.current_thread() is threading.main_thread():
                previous_term = signal.getsignal(signal.SIGTERM)
                def terminate_query(_signum, _frame):
                    raise KeyboardInterrupt("coverage query stopped")
                signal.signal(signal.SIGTERM, terminate_query)
            try:
                code = child.wait(timeout=timeout)
            finally:
                if previous_term is not None:
                    signal.signal(signal.SIGTERM, previous_term)
                if child.poll() is None:
                    child.kill()
                    child.wait()
        try:
            data = json.loads(temporary.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"查询未返回合法 JSON（exit={code}），见 inputs/coverage/query-stderr.log") from exc
        if code and data.get("status") not in {"error", "partial", "no_data"}:
            raise ValueError(f"查询退出码 {code} 与输出状态不一致；保留原输出待核对")
        temporary.replace(output)
        data = read_input(output)
        resolution = data.get("query_resolution")
        if isinstance(resolution, dict) and resolution.get("status") in {"not_found", "ambiguous"}:
            data["status"] = "error"
            data["message"] = resolution.get("message") or "平台查询对象未找到或存在多个匹配，请核对查询对象"
        if not isinstance(resolution, dict) or resolution.get("status") not in {"matched", "not_found", "ambiguous"}:
            data.setdefault("warnings", []).append("查询 Skill 未返回平台对象匹配结果；no_data 不足以证明版本存在或没有缺口")
        data["query_input"] = frozen["query"]
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if frozen["kind"] == "file":
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return coverage_page(run_root)


SCOPE_STATUSES = ("in_scope", "out_of_scope", "unresolved", "unclassified")
ANNOTATION_FIELDS = ("scope_status", "scope_reason", "scope_evidence_ids", "linked_flow_ids",
                     "linked_branch_ids", "analysis_status", "disposition", "source_location",
                     "trigger_path", "guard_conditions", "external_result", "uncertainties",
                     "linked_test_case_ids", "linked_risk_ids", "evidence_ids")


def projection_traceability_warnings(projection: dict) -> list[str]:
    """Advisory shape/reference observations; no verdict or lifecycle changes."""
    warnings = []
    flows = projection.get("business_flows", [])
    if not isinstance(flows, list):
        return ["业务流程投影不是数组，内容待补齐"]
    flow_ids, branch_ids = set(), set()
    for flow in flows:
        if not isinstance(flow, dict):
            continue
        identifier = flow.get("flow_id")
        if isinstance(identifier, str):
            flow_ids.add(identifier)
        steps = flow.get("mainline_steps")
        if not isinstance(steps, list) or not steps:
            warnings.append(f"{identifier} 主干步骤待补齐；流程名称不代表路径分析完成")
        step_ids = {s.get("step_id") for s in (steps if isinstance(steps, list) else [])
                    if isinstance(s, dict) and isinstance(s.get("step_id"), str)}
        for branch in flow.get("branches", []) if isinstance(flow.get("branches", []), list) else []:
            if not isinstance(branch, dict):
                continue
            if isinstance(branch.get("branch_id"), str):
                branch_ids.add(branch["branch_id"])
            origin, destination = branch.get("from_step_id"), branch.get("to_step_id")
            if (not isinstance(origin, str) or origin not in step_ids
                    or destination and (not isinstance(destination, str) or destination not in step_ids)):
                warnings.append(f"{identifier}/{branch.get('branch_id')} 挂接步骤待核对")
    for collection, id_key in (("coverage_gaps", "gap_id"), ("test_cases", "test_case_id")):
        for item in projection.get(collection, []) if isinstance(projection.get(collection, []), list) else []:
            if not isinstance(item, dict):
                continue
            for field, known in (("linked_flow_ids", flow_ids), ("linked_branch_ids", branch_ids)):
                values = item.get(field, [])
                if not isinstance(values, list):
                    warnings.append(f"{item.get(id_key)} 的 {field} 不是数组")
                elif any(not isinstance(value, str) or value not in known for value in values):
                    warnings.append(f"{item.get(id_key)} 的 {field} 引用未发布路径")
    return warnings


def coverage_annotations(run_root: Path, records: list[dict]) -> tuple[list[dict], dict, list[str]]:
    """Join explicit Agent statements by ID; never classify scope or infer a path."""
    warnings: list[str] = []
    projection_path = run_root / "内部索引/工作台投影.json"
    projection = {}
    if projection_path.is_file():
        try:
            projection = json.loads(projection_path.read_text(encoding="utf-8-sig"))
            if not isinstance(projection, dict) or projection.get("run_id") != run_root.name:
                raise ValueError("投影不属于当前 Run")
        except (ValueError, UnicodeError, OSError) as exc:
            warnings.append(f"分析记录暂不可读取：{exc}")
            projection = {}
    entries = projection.get("coverage_gaps", [])
    if not isinstance(entries, list):
        warnings.append("coverage_gaps 不是数组；原始覆盖事实仍可读取")
        entries = []
    warnings.extend(projection_traceability_warnings(projection))
    raw_by_id = {item["gap_id"]: item for item in records}
    annotations: dict[str, dict] = {}
    duplicates: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            warnings.append("缺口分析记录不是对象")
            continue
        identifier = entry.get("gap_id")
        if not isinstance(identifier, str) or identifier not in raw_by_id:
            warnings.append(f"分析记录引用未知 gap_id：{identifier}")
            continue
        if identifier in annotations:
            duplicates.add(identifier)
        annotations[identifier] = entry
        if any(key in entry and entry[key] != raw_by_id[identifier][key]
               for key in ("source", "file_path", "kind", "raw", "coverage_status")):
            warnings.append(f"{identifier} 的投影与原始覆盖事实不一致；显示原始事实")
    cases = {item.get("test_case_id") for item in projection.get("test_cases", [])
             if isinstance(item, dict) and isinstance(item.get("test_case_id"), str)} if isinstance(projection.get("test_cases", []), list) else set()
    summary = {key: 0 for key in SCOPE_STATUSES}
    summary.update(total=len(records), designed_in_scope=0)
    annotated = []
    for record in records:
        identifier = record["gap_id"]
        entry = annotations.get(identifier, {})
        if identifier in duplicates:
            warnings.append(f"{identifier} 有重复分析记录；待 Agent 消除歧义")
            entry = {}
        item = {**record, **{key: entry[key] for key in ANNOTATION_FIELDS if key in entry}}
        scope = item.get("scope_status")
        if scope not in SCOPE_STATUSES:
            if scope is not None:
                warnings.append(f"{identifier} 范围状态未识别：{scope}")
            scope = "unclassified"
        # This display status describes the presence of a decision, not business scope.
        item["scope_status"] = scope
        summary[scope] += 1
        if scope != "unclassified" and (not isinstance(item.get("scope_reason"), str) or not item["scope_reason"].strip()):
            warnings.append(f"{identifier} 范围声明缺少依据")
        linked = item.get("linked_test_case_ids", [])
        if isinstance(linked, list):
            unknown = [value for value in linked if not isinstance(value, str) or value not in cases]
            if unknown:
                warnings.append(f"{identifier} 引用未发布用例：{unknown}")
            if scope == "in_scope" and any(isinstance(value, str) and value in cases for value in linked):
                summary["designed_in_scope"] += 1
        annotated.append(item)
    if summary["unclassified"]:
        warnings.append(f"{summary['unclassified']} 条输入缺口尚无唯一有效的范围声明；不计为补测完成")
    return annotated, summary, warnings


def coverage_page(run_root: Path, *, cursor: int = 0, limit: int = 50,
                  source: str | None = None, file_path: str | None = None, kind: str | None = None,
                  scope_status: str | None = None, flow_id: str | None = None, query: str | None = None,
                  analysis_status: str | None = None, disposition: str | None = None) -> dict:
    if cursor < 0 or not 1 <= limit <= 200:
        raise ValueError("cursor >= 0 且 limit 为 1..200")
    if scope_status is not None and scope_status not in SCOPE_STATUSES:
        raise ValueError("scope_status 必须为 in_scope/out_of_scope/unresolved/unclassified")
    path = run_root / "inputs/coverage/combined.json"
    if not path.is_file():
        return {"status": "pending", "items": [], "total": 0, "next_cursor": None}
    data = read_input(path)
    all_records, summary, trace_warnings = coverage_annotations(run_root, gap_records(data))
    records = [r for r in all_records if (source is None or r["source"] == source)
               and (file_path is None or r["file_path"] == file_path) and (kind is None or r["kind"] == kind)
               and (scope_status is None or r["scope_status"] == scope_status)
               and (analysis_status is None or r.get("analysis_status") == analysis_status)
               and (disposition is None or r.get("disposition") == disposition)
               and (flow_id is None or isinstance(r.get("linked_flow_ids"), list) and flow_id in r["linked_flow_ids"])
               and (not query or query.casefold() in json.dumps(
                   [r["gap_id"], r["file_path"], r["raw"]], ensure_ascii=False).casefold())]
    return {"status": data["status"], "message": data.get("message"), "sources": data.get("sources", []),
            "missing": data.get("missing", []), "warnings": data.get("warnings", []),
            "unknown_count": len(data.get("unknown_records", [])), "total": len(records),
            "record_count": len(data.get("records", [])), "tables": data.get("tables", []),
            "parser_version": data.get("parser_version"), "query_resolution": data.get("query_resolution"),
            "query_input": data.get("query_input"),
            "unlocated_count": len(data.get("unlocated_records", [])),
            "scope_summary": summary, "traceability_warnings": trace_warnings,
            "items": records[cursor:cursor + limit],
            "next_cursor": cursor + limit if cursor + limit < len(records) else None,
            "raw_path": str(path)}
