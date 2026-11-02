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
            branch_id_index = _column(headers, _BRANCH_ID_HEADERS)
            condition_index = _column(headers, _CONDITION_HEADERS)
            true_count_index = _column(headers, _TRUE_COUNT_HEADERS)
            false_count_index = _column(headers, _FALSE_COUNT_HEADERS)
            has_functions = None not in (module_index, function_index, count_index)
            branch_columns = (branch_id_index, condition_index, true_count_index, false_count_index)
            has_branches = function_index is not None and None not in branch_columns
            if count_index is not None and not has_functions:
                warnings.append(f"sheet {sheet.title}: incomplete function coverage columns; expected module/function/count")
            if any(index is not None for index in branch_columns) and not has_branches:
                warnings.append(
                    f"sheet {sheet.title}: incomplete branch coverage columns; "
                    "expected branch_id/function/condition/true_count/false_count"
                )
            for row_number, row in enumerate(rows, 2):
                if all(value is None for value in row):
                    continue
                source_path = row[path_index] if path_index is not None and path_index < len(row) else None
                function = row[function_index] if function_index is not None and function_index < len(row) else None
                module = row[module_index] if module_index is not None and module_index < len(row) else None
                count = row[count_index] if count_index is not None and count_index < len(row) else None
                branch_only_row = has_branches and module is None and count is None and any(
                    index < len(row) and row[index] is not None for index in branch_columns
                )
                if has_functions and not branch_only_row and any(
                    value is not None for value in (module, function, count)
                ):
                    try:
                        numeric_count = int(count)
                    except (TypeError, ValueError):
                        warnings.append(f"sheet {sheet.title} row {row_number}: invalid coverage count {count!r}")
                    else:
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
                if has_branches:
                    branch_id = row[branch_id_index] if branch_id_index < len(row) else None
                    condition = row[condition_index] if condition_index < len(row) else None
                    true_count = row[true_count_index] if true_count_index < len(row) else None
                    false_count = row[false_count_index] if false_count_index < len(row) else None
                    if all(value is None for value in (branch_id, condition, true_count, false_count)):
                        continue
                    try:
                        numeric_true = int(true_count)
                        numeric_false = int(false_count)
                    except (TypeError, ValueError):
                        warnings.append(
                            f"sheet {sheet.title} row {row_number}: invalid branch counts "
                            f"{true_count!r}/{false_count!r}"
                        )
                    else:
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
        path_files = []
        if requested_path:
            exact_files = []
            suffix_files = []
            for file in inventory.get("files", []):
                file_path = file["path"].replace("\\", "/").strip("/")
                if file_path == requested_path:
                    exact_files.append(file)
                elif file_path.endswith("/" + requested_path) or requested_path.endswith("/" + file_path):
                    suffix_files.append(file)
            path_files = exact_files or suffix_files
        if record.get("coverage_type") in {"line", "branch"} and "line" in record:
            # Query line/block/branch IDs are coverage-tool coordinates, not
            # parser branch IDs. Match the file and exact line, never invent a
            # true/false condition or select one of several same-name files.
            candidates = [
                {"repo_id": file["repo_id"], "path": file["path"], "line": record["line"]}
                for file in path_files
                if 1 <= record["line"] <= file.get("line_count", 0)
            ]
        elif requested_path:
            candidates = [
                {
                    "repo_id": file["repo_id"],
                    "path": file["path"],
                    "line": function["line"],
                }
                for file in path_files
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
    if not isinstance(data, dict) or data.get("status") not in {"success", "partial", "no_data", "scope_not_found", "error"}:
        raise ValueError("需要含 status 的覆盖率 combined JSON")
    warnings = list(data.get("warnings") or [])
    modern = isinstance(data.get("uncovered"), dict)
    sources = sorted({g.get("source") for groups in (data.get("uncovered", {}), data.get("indeterminate", {}))
                      for items in groups.values() if isinstance(items, list) for g in items if g.get("source")}
                     | {r["source"] for r in data.get("summary", {}).get("per_source", []) if r.get("source")})
    preferred = data.get("query_input", {}).get("source") or data.get("source_semantics", {}).get("preferred") or "summary"
    selected = preferred if preferred in sources else (sources[0] if sources else None)
    if modern and selected and selected != preferred:
        warnings.append(f"首选来源 {preferred} 缺失，实际采用 {selected}；不同来源未合并")
    scope = data.get("scope") or data.get("meta", {}).get("scope", {})
    requested = str(scope.get("requested", "")).replace("\\", "/").strip("/").casefold()
    records, seen = [], set()
    for kind in ("function", "line", "branch"):
        key = {"function": "uncovered_functions", "line": "uncovered_lines", "branch": "uncovered_branches"}[kind]
        groups = data.get("uncovered", {}).get({"function": "functions", "line": "lines", "branch": "branches"}[kind], []) if modern else data.get(key, [])
        if modern and kind == "branch":
            groups = [*groups, *[{**g, key: g.get("indeterminate_branches", []), "indeterminate": True}
                                for g in data.get("indeterminate", {}).get("branches", [])]]
        if not isinstance(groups, list):
            raise ValueError(f"{key} 必须是数组")
        for group in groups:
            if modern and isinstance(group, dict):
                if group.get("source") != selected:
                    continue
                group = {**group, "file_path": group.get("relative_path")}
                file_path = str(group.get("file_path") or "").replace("\\", "/")
                parent = file_path.rsplit("/", 1)[0].casefold()
                if requested and not (parent == requested or (scope.get("recursive", True) and parent.startswith(requested + "/"))):
                    warnings.append(f"报告路径不在所选目录内：{file_path}；未作为分析目标")
                    continue
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
                          "count": None if group.get("indeterminate") else 0, "raw": raw,
                          "coverage_state": "indeterminate" if group.get("indeterminate") else "uncovered",
                          "platform_path": group.get("platform_path")}
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
                        if not isinstance(raw, dict) or str(raw.get("count")) != ("-" if group.get("indeterminate") else "0"):
                            warnings.append(f"{group['file_path']}:{line} 分支计数非零或未知，未作为零覆盖缺口")
                            continue
                        record.update(block=raw.get("block"), branch=raw.get("branch"))
                if isinstance(raw, dict) and "count" in raw and str(raw["count"]) != ("-" if group.get("indeterminate") else "0"):
                    warnings.append(f"{group['file_path']} 存在非零或未知计数，未作为零覆盖缺口")
                    continue
                identity = (record["source"], record["path"], kind, record.get("function"),
                            record.get("line"), record.get("block"), record.get("branch"), record["coverage_state"])
                if identity not in seen:
                    seen.add(identity)
                    records.append(record)
    return {"records": records if data["status"] in {"success", "partial"} else [],
            "warnings": warnings,
            "acquisition": {**{key: data.get(key) for key in
                            ("status", "message", "sources", "missing", "query_input", "query_resolution",
                             "scope", "summary", "snapshot", "source_semantics", "pagination")},
                            "selected_source": selected if modern else None}}


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


def in_query_scopes(path: str, scopes: list[dict]) -> bool:
    """Match whole directory segments, including repository prefixes before module."""
    parent = path.replace("\\", "/").rsplit("/", 1)[0].casefold()
    for scope in scopes:
        requested = str(scope["requested"]).replace("\\", "/").strip("/").casefold()
        if parent == requested or parent.endswith("/" + requested):
            return True
        if scope.get("recursive", True) and (parent.startswith(requested + "/") or "/" + requested + "/" in "/" + parent + "/"):
            return True
    return False
