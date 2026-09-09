"""Lossless tabular coverage adaptation shared by assets and Run inputs."""
from __future__ import annotations

import csv
import re
from pathlib import Path


def header_key(value: object) -> str:
    return re.sub(r"[\s_]+", "", str(value or "").lstrip("\ufeff")).casefold()


ALIASES = {
    "module": ("module", "模块"), "feature": ("feature", "特性"),
    "file_path": ("file_path", "path", "路径", "代码路径", "文件路径"),
    "function": ("function", "函数", "函数名"),
    "count": ("count", "coverage", "coverage count", "覆盖次数", "执行次数"),
    "covered": ("covered", "是否覆盖"), "source": ("source", "来源"),
    "kind": ("kind", "覆盖类型"), "line": ("line", "行号"),
    "block": ("block", "块编号"), "branch": ("branch", "分支"),
    "branch_id": ("branch_id", "分支id", "分支编号"),
    "condition": ("condition", "分支条件", "条件"),
    "true_count": ("true_count", "真分支次数"),
    "false_count": ("false_count", "假分支次数"),
}
LOOKUP = {header_key(alias): key for key, aliases in ALIASES.items() for alias in aliases}


def _count(value: object) -> int | None:
    text = str(value if value is not None else "").strip()
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return int(text.split(".")[0])
    return None


def read_table(path: Path) -> dict:
    book = None
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            sheets = [(path.name, list(csv.reader(stream)))]
    else:
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        sheets = [(sheet.title, sheet.values) for sheet in book.worksheets]
    records, warnings, tables = [], [], []
    try:
        for name, values in sheets:
            rows = iter(values)
            headers = next(rows, ())
            mapping = [LOOKUP.get(header_key(h)) for h in headers]
            fields = set(mapping)
            legacy_branch = {"branch_id", "true_count", "false_count", "function"} <= fields
            recognized = legacy_branch or ("count" in fields and bool(fields & {"function", "line", "kind"}))
            table = {"sheet": name, "columns": [{"original": str(h or ""), "field": k} for h, k in zip(headers, mapping)],
                     "recognized": recognized, "row_count": 0}
            tables.append(table)
            if not recognized:
                warnings.append(f"{name}: 未识别覆盖率表头，未读取为覆盖记录")
                continue
            duplicates = {k for k in fields if k and mapping.count(k) > 1}
            for number, values in enumerate(rows, 2):
                if not any(v is not None and str(v).strip() for v in values):
                    continue
                raw = [v if isinstance(v, (str, int, float, bool, type(None))) else str(v) for v in values]
                row = {k: v for k, v in zip(mapping, raw) if k}
                kind = str(row.get("kind") or ("branch" if legacy_branch else "function" if "function" in fields else "line"))
                count = _count(row.get("count"))
                issues = []
                if duplicates:
                    issues.append(f"字段重复：{', '.join(sorted(duplicates))}")
                if legacy_branch:
                    if "condition" not in row and "branch" in row:
                        row["condition"] = row["branch"]
                    true, false = _count(row.get("true_count")), _count(row.get("false_count"))
                    count = true + false if true is not None and false is not None else None
                    row.update(true_count=true, false_count=false)
                if count is None:
                    issues.append("覆盖次数未知或不是非负整数")
                covered = str(row.get("covered", "")).strip().casefold()
                flag = True if covered in {"是", "已覆盖", "true", "yes", "1"} else False if covered in {"否", "未覆盖", "false", "no", "0"} else None
                conflict = count is not None and flag is not None and (count > 0) != flag
                if conflict:
                    issues.append("是否覆盖与覆盖次数冲突，待确认")
                if not row.get("file_path"):
                    issues.append("缺少代码路径，待定位")
                required = ("function",) if kind == "function" else ("branch_id",) if legacy_branch else ("line", "block", "branch") if kind == "branch" else ("line",)
                malformed = kind not in {"function", "line", "branch"} or any(row.get(k) in (None, "") for k in required)
                if not legacy_branch and kind in {"line", "branch"} and (_count(row.get("line")) or 0) < 1:
                    malformed = True
                if malformed:
                    issues.append("覆盖类型或位置字段不完整")
                status = "unknown" if count is None or conflict or duplicates or malformed else "uncovered" if count == 0 else "covered"
                if legacy_branch and status != "unknown" and (row["true_count"] == 0 or row["false_count"] == 0):
                    status = "uncovered"
                record = {**row, "kind": kind, "count": count, "source": str(row.get("source") or path),
                          "file_path": str(row.get("file_path") or ""), "sheet": name, "row": number,
                          "raw_cells": raw, "coverage_status": status, "issues": issues, "legacy_branch": legacy_branch}
                records.append(record)
                table["row_count"] += 1
                warnings.extend(f"{name}:{number}: {issue}" for issue in issues)
    finally:
        if book:
            book.close()
    if not any(t["recognized"] for t in tables):
        raise ValueError("未识别覆盖率表：函数表需函数名和覆盖次数；支持代码路径、模块及英文契约列。" + "；".join(warnings))
    return {"records": records, "warnings": warnings, "tables": tables, "parser_version": "pangea-coverage-parser-2"}


def table_combined(parsed: dict) -> dict:
    records = parsed["records"]
    data = {"status": "partial" if parsed["warnings"] else "success" if records else "no_data",
            "sources": [], "uncovered_functions": [], "uncovered_lines": [], "uncovered_branches": [],
            "missing": [], "warnings": parsed["warnings"], "unknown_records": [], "records": records,
            "tables": parsed["tables"], "parser_version": parsed["parser_version"], "unlocated_records": []}
    for row in records:
        if row["coverage_status"] == "unknown":
            data["unknown_records"].append(row)
            continue
        if row["coverage_status"] != "uncovered":
            continue
        if not row["file_path"] or row["legacy_branch"]:
            # Preserve these gaps without manufacturing source locations or branch directions.
            data["unlocated_records"].append(row)
            continue
        kind = row["kind"]
        key = "uncovered_" + {"function": "functions", "line": "lines", "branch": "branches"}[kind]
        value = str(row["function"]) if kind == "function" else int(float(row["line"])) if kind == "line" else {
            "line": int(float(row["line"])), "block": str(row["block"]), "branch": str(row["branch"]), "count": "0"}
        data[key].append({"source": row["source"], "file_path": row["file_path"], key: [value],
                          "sheet": row["sheet"], "row": row["row"]})
    return data
