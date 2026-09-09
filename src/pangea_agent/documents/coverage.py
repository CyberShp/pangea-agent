from __future__ import annotations

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
    """Compatibility view of the shared parser for legacy asset consumers."""
    from .coverage_table import read_table
    parsed = read_table(path)
    records = [{**row, "coverage_type": row["kind"], "path": row["file_path"],
                "function": str(row.get("function") or ""), "module": str(row.get("module") or "")}
               for row in parsed["records"]]
    return records, parsed["warnings"]

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
        if requested_path:
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
        meaning = (
            "branch_execution_reference_only"
            if record.get("coverage_type") == "branch"
            else "function_execution_reference_only"
        )
        item = {**record, "matches": candidates, "meaning": meaning}
        if len(candidates) == 1:
            matched.append(item)
        elif candidates:
            ambiguous.append(item)
        else:
            unmatched.append(item)
    return {"matched": matched, "ambiguous": ambiguous, "unmatched": unmatched}


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
