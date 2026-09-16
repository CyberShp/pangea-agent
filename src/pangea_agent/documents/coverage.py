from __future__ import annotations

import json
from pathlib import Path

from .extract import DependencyUnavailableError

_MODULE_HEADERS = {"module", "模块"}
_PATH_HEADERS = {"path", "路径"}
_FUNCTION_HEADERS = {"function", "函数", "函数名"}
_COUNT_HEADERS = {"count", "coverage", "coverage count", "覆盖次数", "执行次数"}
_BRANCH_ID_HEADERS = {"branch_id", "branch id", "分支id", "分支编号"}
_CONDITION_HEADERS = {"condition", "branch", "分支条件", "条件"}
_TRUE_COUNT_HEADERS = {"true_count", "true count", "真分支次数"}
_FALSE_COUNT_HEADERS = {"false_count", "false count", "假分支次数"}


def _column(headers: list[str], accepted: set[str]) -> int | None:
    for index, value in enumerate(headers):
        if value.strip().lower() in accepted:
            return index
    return None


def parse_coverage_xlsx(path: Path) -> tuple[list[dict], list[str]]:
    """Read function and branch execution counts from coverage workbooks."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise DependencyUnavailableError("openpyxl", "coverage XLSX") from exc

    workbook = load_workbook(path, read_only=True, data_only=True)
    records: list[dict] = []
    warnings: list[str] = []
    try:
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            first = next(rows, None)
            if first is None:
                continue
            headers = ["" if value is None else str(value) for value in first]
            module_index = _column(headers, _MODULE_HEADERS)
            path_index = _column(headers, _PATH_HEADERS)
            function_index = _column(headers, _FUNCTION_HEADERS)
            count_index = _column(headers, _COUNT_HEADERS)
            if None not in (module_index, function_index, count_index):
                for row_number, row in enumerate(rows, 2):
                    module = row[module_index] if module_index < len(row) else None
                    source_path = row[path_index] if path_index is not None and path_index < len(row) else None
                    function = row[function_index] if function_index < len(row) else None
                    count = row[count_index] if count_index < len(row) else None
                    if module is None and function is None and count is None:
                        continue
                    try:
                        numeric_count = int(count)
                    except (TypeError, ValueError):
                        warnings.append(f"sheet {sheet.title} row {row_number}: invalid coverage count {count!r}")
                        continue
                    records.append({
                        "coverage_type": "function",
                        "module": "" if module is None else str(module),
                        "path": "" if source_path is None else str(source_path),
                        "function": "" if function is None else str(function),
                        "count": numeric_count,
                        "source": str(path),
                        "sheet": sheet.title,
                        "row": row_number,
                    })
                continue

            branch_id_index = _column(headers, _BRANCH_ID_HEADERS)
            condition_index = _column(headers, _CONDITION_HEADERS)
            true_count_index = _column(headers, _TRUE_COUNT_HEADERS)
            false_count_index = _column(headers, _FALSE_COUNT_HEADERS)
            if None in (branch_id_index, function_index, condition_index, true_count_index, false_count_index):
                continue
            for row_number, row in enumerate(rows, 2):
                branch_id = row[branch_id_index] if branch_id_index < len(row) else None
                source_path = row[path_index] if path_index is not None and path_index < len(row) else None
                function = row[function_index] if function_index < len(row) else None
                condition = row[condition_index] if condition_index < len(row) else None
                true_count = row[true_count_index] if true_count_index < len(row) else None
                false_count = row[false_count_index] if false_count_index < len(row) else None
                if all(value is None for value in (branch_id, function, condition, true_count, false_count)):
                    continue
                try:
                    numeric_true = int(true_count)
                    numeric_false = int(false_count)
                except (TypeError, ValueError):
                    warnings.append(
                        f"sheet {sheet.title} row {row_number}: invalid branch counts "
                        f"{true_count!r}/{false_count!r}"
                    )
                    continue
                records.append({
                    "coverage_type": "branch",
                    "branch_id": "" if branch_id is None else str(branch_id),
                    "module": "",
                    "path": "" if source_path is None else str(source_path),
                    "function": "" if function is None else str(function),
                    "condition": "" if condition is None else str(condition),
                    "true_count": numeric_true,
                    "false_count": numeric_false,
                    "count": numeric_true + numeric_false,
                    "source": str(path),
                    "sheet": sheet.title,
                    "row": row_number,
                })
    finally:
        workbook.close()
    return records, warnings


def match_coverage_records(records: list[dict], inventory: dict) -> dict:
    """Match coverage to in-scope symbols without treating execution as risk coverage."""
    symbols: dict[str, list[dict]] = {}
    for file in inventory.get("files", []):
        for function in file.get("functions", []):
            symbols.setdefault(function["symbol"], []).append({
                "repo_id": file["repo_id"],
                "path": file["path"],
                "line": function["line"],
            })
    matched: list[dict] = []
    unmatched: list[dict] = []
    ambiguous: list[dict] = []
    for record in records:
        requested_path = str(record.get("path", "")).replace("\\", "/").strip("/")
        if record.get("coverage_type") in {"line", "branch"} and "line" in record:
            # Query line/block/branch IDs are coverage-tool coordinates, not
            # parser branch IDs. Match the file and exact line, never invent a
            # true/false condition or select one of several same-name files.
            candidates = [
                {"repo_id": file["repo_id"], "path": file["path"], "line": record["line"]}
                for file in inventory.get("files", [])
                if requested_path and (
                    file["path"].replace("\\", "/").strip("/") == requested_path
                    or requested_path.endswith("/" + file["path"].replace("\\", "/").strip("/"))
                )
                and 1 <= record["line"] <= file.get("line_count", 0)
            ]
        elif requested_path:
            candidates = [
                {
                    "repo_id": file["repo_id"],
                    "path": file["path"],
                    "line": function["line"],
                }
                for file in inventory.get("files", [])
                if (
                    file["path"].replace("\\", "/").strip("/") == requested_path
                    or requested_path.endswith(
                        "/" + file["path"].replace("\\", "/").strip("/")
                    )
                )
                for function in file.get("functions", [])
                if function["symbol"] == record["function"]
            ]
        else:
            candidates = symbols.get(record["function"], [])
        meaning = f"{record.get('coverage_type', 'function')}_execution_reference_only"
        item = {**record, "matches": candidates, "meaning": meaning}
        if len(candidates) == 1:
            matched.append(item)
        elif candidates:
            ambiguous.append(item)
        else:
            unmatched.append(item)
    return {"matched": matched, "ambiguous": ambiguous, "unmatched": unmatched}


def parse_coverage_combined(path: Path) -> dict:
    """Translate query facts into existing Coverage records without semantics."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("status") not in {"success", "partial", "no_data", "error"}:
        raise ValueError("需要含 status 的覆盖率 combined JSON")
    warnings = list(data.get("warnings") or [])
    records, seen = [], set()
    for kind in ("function", "line", "branch"):
        key = {"function": "uncovered_functions", "line": "uncovered_lines", "branch": "uncovered_branches"}[kind]
        groups = data.get(key, [])
        if not isinstance(groups, list):
            raise ValueError(f"{key} 必须是数组")
        for group in groups:
            if not isinstance(group, dict) or not group.get("source") or not group.get("file_path"):
                warnings.append(f"{key} 记录缺少 source/file_path；原始记录保留，未作为可定位缺口")
                continue
            values = group.get(key, [])
            if not isinstance(values, list):
                warnings.append(f"{key} 明细不是数组；原始记录保留")
                continue
            for raw in values:
                record = {"coverage_type": kind, "source": group["source"],
                          "path": group["file_path"], "file_path": group["file_path"],
                          "count": 0, "raw": raw}
                if kind == "function":
                    function = raw.get("function") if isinstance(raw, dict) else raw
                    if not isinstance(function, str) or not function.strip():
                        warnings.append(f"{group['file_path']} 存在无法定位的函数记录")
                        continue
                    record["function"] = function
                else:
                    line = raw.get("line") if isinstance(raw, dict) else raw
                    if isinstance(line, bool) or not str(line).isdigit() or int(line) < 1:
                        warnings.append(f"{group['file_path']} 存在无效行号；原始记录保留")
                        continue
                    record["line"] = int(line)
                    if kind == "branch":
                        if not isinstance(raw, dict) or str(raw.get("count")) != "0":
                            warnings.append(f"{group['file_path']}:{line} 分支计数非零或未知，未作为零覆盖缺口")
                            continue
                        record.update(block=raw.get("block"), branch=raw.get("branch"))
                if isinstance(raw, dict) and "count" in raw and str(raw["count"]) != "0":
                    warnings.append(f"{group['file_path']} 存在非零或未知计数，未作为零覆盖缺口")
                    continue
                identity = json.dumps(record, sort_keys=True, ensure_ascii=False)
                if identity not in seen:
                    seen.add(identity)
                    records.append(record)
    return {"records": records if data["status"] in {"success", "partial"} else [],
            "warnings": warnings,
            "acquisition": {key: data.get(key) for key in
                            ("status", "message", "sources", "missing", "query_input", "query_resolution")}}


def relevant_zero_coverage(report: dict) -> list[dict]:
    return [
        record
        for record in report.get("matched", [])
        if record.get("count") == 0
        or (
            record.get("coverage_type") == "branch"
            and (record.get("true_count") == 0 or record.get("false_count") == 0)
        )
    ]
