"""Coverage input preparation and paging; never infer source or test semantics."""
from __future__ import annotations

import csv
import json
import os
import shutil
import signal
import threading
import subprocess
import sys
from pathlib import Path


def local_query_skill() -> dict:
    root_value = os.environ.get("PANGEA_LOCAL_SKILLS_ROOT")
    root = Path(root_value).resolve() / "coverage-query" if root_value else None
    return {"available": bool(root and (root / "SKILL.md").is_file()
                              and (root / "scripts/coverage_query.py").is_file()),
            "root_path": str(root) if root else None,
            "placement": "<PANGEA 解压目录>/local-skills/coverage-query"}


def normalize_input(value: object) -> dict:
    if not isinstance(value, dict) or value.get("kind") not in {"file", "query"}:
        raise ValueError("coverage_input.kind 必须是 file 或 query")
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


def freeze_input(value: dict, run_root: Path) -> dict:
    folder = run_root / "inputs/coverage"
    folder.mkdir(parents=True)
    frozen = dict(value)
    if value["kind"] == "file":
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
    if path.suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    else:
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            values = iter(book.active.values)
            headers = next(values, ())
            rows = [dict(zip(headers, row)) for row in values if any(v is not None for v in row)]
        finally:
            book.close()
    data = {"status": "success" if rows else "no_data", "sources": [],
            "uncovered_functions": [], "uncovered_lines": [], "uncovered_branches": [],
            "missing": [], "warnings": [], "unknown_records": []}
    for index, row in enumerate(rows, 2):
        required = ("source", "file_path", "kind", "count")
        if any(key not in row for key in required):
            raise ValueError("表格必须包含 source,file_path,kind,count；function 另需 function；line 另需 line；branch 另需 line,block,branch")
        kind = str(row["kind"] or "")
        if kind not in {"function", "line", "branch"} or not row["source"] or not row["file_path"]:
            raise ValueError(f"表格第 {index} 行缺少来源/文件或 kind 不受支持")
        count = str(row["count"] if row["count"] is not None else "-")
        if count in {"-", "", "unknown"}:
            data["unknown_records"].append({**row, "count": count})
            continue
        try:
            hits = int(count)
        except ValueError as exc:
            raise ValueError(f"表格第 {index} 行 count 需为非负整数或 -，不接受百分比") from exc
        if hits < 0:
            raise ValueError(f"表格第 {index} 行 count 不能为负数")
        fields = ("function",) if kind == "function" else ("line",) if kind == "line" else ("line", "block", "branch")
        if any(row.get(k) is None or str(row[k]) == "" for k in fields):
            raise ValueError(f"表格第 {index} 行缺少 {','.join(fields)}")
        if hits:
            continue
        key = "uncovered_" + {"function": "functions", "line": "lines", "branch": "branches"}[kind]
        item = str(row["function"]) if kind == "function" else int(row["line"]) if kind == "line" else {
            "line": int(row["line"]), "block": str(row["block"]), "branch": str(row["branch"]), "count": "0"}
        data[key].append({"source": str(row["source"]), "file_path": str(row["file_path"]), key: [item]})
    return data


def read_input(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig")) if path.suffix == ".json" else _table(path)
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
    return records


def prepare_coverage(run_root: Path, *, timeout: int = 300) -> dict:
    folder = run_root / "inputs/coverage"
    frozen = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    output = folder / "combined.json"
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
    if frozen["kind"] == "file":
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return coverage_page(run_root)


def coverage_page(run_root: Path, *, cursor: int = 0, limit: int = 50,
                  source: str | None = None, file_path: str | None = None, kind: str | None = None) -> dict:
    if cursor < 0 or not 1 <= limit <= 200:
        raise ValueError("cursor >= 0 且 limit 为 1..200")
    path = run_root / "inputs/coverage/combined.json"
    if not path.is_file():
        return {"status": "pending", "items": [], "total": 0, "next_cursor": None}
    data = read_input(path)
    all_records = gap_records(data)
    records = [r for r in all_records if (source is None or r["source"] == source)
               and (file_path is None or r["file_path"] == file_path) and (kind is None or r["kind"] == kind)]
    return {"status": data["status"], "message": data.get("message"), "sources": data.get("sources", []),
            "missing": data.get("missing", []), "warnings": data.get("warnings", []),
            "unknown_count": len(data.get("unknown_records", [])), "total": len(records),
            "items": records[cursor:cursor + limit],
            "next_cursor": cursor + limit if cursor + limit < len(records) else None,
            "raw_path": str(path)}
