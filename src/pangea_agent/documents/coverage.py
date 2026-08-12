from __future__ import annotations

from pathlib import Path

from .extract import DependencyUnavailableError

_MODULE_HEADERS = {"module", "模块"}
_FUNCTION_HEADERS = {"function", "函数", "函数名"}
_COUNT_HEADERS = {"count", "coverage", "coverage count", "覆盖次数", "执行次数"}


def _column(headers: list[str], accepted: set[str]) -> int | None:
    for index, value in enumerate(headers):
        if value.strip().lower() in accepted:
            return index
    return None


def parse_coverage_xlsx(path: Path) -> tuple[list[dict], list[str]]:
    """Read the V1 module/function/execution-count coverage shape."""
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
            function_index = _column(headers, _FUNCTION_HEADERS)
            count_index = _column(headers, _COUNT_HEADERS)
            if None in (module_index, function_index, count_index):
                warnings.append(f"sheet {sheet.title}: expected module/function/count columns")
                continue
            for row_number, row in enumerate(rows, 2):
                module = row[module_index] if module_index < len(row) else None
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
                    "module": "" if module is None else str(module),
                    "function": "" if function is None else str(function),
                    "count": numeric_count,
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
        candidates = symbols.get(record["function"], [])
        item = {**record, "matches": candidates, "meaning": "function_execution_reference_only"}
        if len(candidates) == 1:
            matched.append(item)
        elif candidates:
            ambiguous.append(item)
        else:
            unmatched.append(item)
    return {"matched": matched, "ambiguous": ambiguous, "unmatched": unmatched}
