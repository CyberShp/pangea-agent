"""Acquire coverage through the installation's private, read-only query skill."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import copy
from pathlib import Path
from uuid import uuid4

from pangea_agent.agent_io import write_json


def local_query_skill() -> dict:
    value = os.environ.get("PANGEA_LOCAL_SKILLS_ROOT")
    root = Path(value).resolve() / "coverage-query" if value else None
    return {
        "available": bool(root and (root / "SKILL.md").is_file()
                          and (root / "scripts/coverage_query.py").is_file()),
        "root_path": str(root) if root else None,
        "placement": "<PANGEA 解压目录>/local-skills/coverage-query",
    }


def query_coverage(data_root: str, query: dict, *, timeout: float = 300) -> dict:
    """Paginated combined query; keep acquisition facts separate from analysis verdicts."""
    from pangea_agent.assets import import_asset, prepare_asset_extraction, asset_detail
    from pangea_agent.documents.coverage import parse_coverage_combined

    query = dict(query)
    scope = query.get("scope") or query.get("module") or ""
    if not isinstance(scope, str):
        raise ValueError("scope 必须是字符串")
    query["scope"] = scope.replace("\\", "/").strip().strip("/")
    for key in ("product", "c_version", "scope"):
        if not isinstance(query.get(key), str) or not query[key].strip():
            raise ValueError(f"覆盖率查询缺少 {key}")
    if not isinstance(query.get("b_version", ""), str):
        raise ValueError("b_version 必须是字符串")
    query = {**query, "b_version": query.get("b_version", ""), "recursive": query.get("recursive", True), "source": query.get("source") or "summary"}
    capability = local_query_skill()
    if not capability["available"]:
        raise ValueError(f"请放入覆盖率查询 Skill：{capability['placement']}")
    folder = Path(data_root).resolve() / "coverage" / "queries" / uuid4().hex
    folder.mkdir(parents=True)
    write_json(folder / "query.json", query)
    output = folder / "combined.json"
    argv = [sys.executable, str(Path(capability["root_path"]) / "scripts/coverage_query.py"),
            "combined", "--product", query["product"], "--version", query["c_version"],
            "--scope", query["scope"], "--recursive" if query["recursive"] else "--no-recursive"]
    if query["b_version"]:
        argv += ["--b-version", query["b_version"]]
    result = {"status": "error", "asset": None, "query_input": query,
              "raw_path": str(output), "stderr_path": str(folder / "query-stderr.log")}
    try:
        data, exit_code = collect_pages(argv, folder, timeout)
        write_json(output, data)
        result["exit_code"] = exit_code
        if not isinstance(data, dict) or data.get("status") not in {"success", "partial", "no_data", "scope_not_found", "error"}:
            raise ValueError("查询未返回含有效 status 的 combined JSON")
        for key in ("sources", "missing", "warnings"):
            if key in data and not isinstance(data[key], list):
                raise ValueError(f"combined.{key} 必须是数组")
        result.update({key: data.get(key) for key in ("status", "message", "query_resolution")})
        result.update({key: data.get(key) for key in ("scope", "summary", "pagination", "snapshot", "source_semantics")})
        result["sources"] = data.get("snapshot", {}).get("sources", data.get("sources", []))
        result["missing"] = data.get("missing", [])
        result["warnings"] = list(data.get("warnings") or [])
        resolution = data.get("query_resolution") or {}
        if not isinstance(resolution, dict):
            raise ValueError("query_resolution 必须是对象")
        if resolution.get("status") in {"not_found", "ambiguous"}:
            result.update(status="error", message=resolution.get("message") or "平台查询对象未找到或存在多个匹配")
        elif resolution.get("status") != "matched" and not data.get("scope"):
            result["warnings"].append("查询 Skill 未返回平台对象匹配结果；空数据不能证明版本存在或没有覆盖缺口")
        if exit_code and result["status"] == "success":
            result.update(status="error", message=f"查询退出码 {exit_code} 与 success 状态不一致")
        # Each page preserves original stdout; the assembled asset carries query provenance.
        if result["status"] in {"success", "partial"}:
            enriched = {**data, "query_input": query, "warnings": result["warnings"],
                        "acquisition": {"raw_path": str(output), "exit_code": exit_code}}
            prepared = folder / "coverage.json"
            write_json(prepared, enriched)
            parsed = parse_coverage_combined(prepared)
            result["warnings"] = parsed["warnings"]
            result["selected_source"] = parsed["acquisition"].get("selected_source")
            asset = import_asset(data_root, str(prepared), "coverage",
                                 f"内网覆盖率 · {query['product']} / {query['c_version']} / {query['scope']}")
            extracted = prepare_asset_extraction(data_root, asset.asset_id)
            result["asset"] = asset_detail(data_root, asset.asset_id)["asset"]
            result["record_count"] = extracted["asset"]["structured_item_count"]
        if result["status"] == "no_data" and not result.get("message"):
            result["message"] = "查询未返回覆盖数据，请核对产品、版本和模块；不能解释为没有覆盖缺口"
    except subprocess.TimeoutExpired:
        result.update(status="error", message=f"覆盖率查询超时（{timeout:g} 秒），查询进程已结束")
    except (OSError, ValueError, TypeError) as exc:
        result.update(status="error", message=str(exc))
    write_json(folder / "result.json", result)
    return result


def collect_pages(argv: list[str], folder: Path, timeout: float) -> tuple[dict, int]:
    """Collect one scope without combining different report snapshots."""
    deadline = time.monotonic() + timeout
    offset, limit = 0, 100
    merged = None
    fingerprint = None
    while True:
        page_path = folder / f"page-{offset}.json"
        with page_path.open("wb") as stdout, (folder / "query-stderr.log").open("ab") as stderr:
            process = subprocess.run([*argv, "--limit", str(limit), "--offset", str(offset)],
                                     stdout=stdout, stderr=stderr, timeout=max(0.001, deadline - time.monotonic()))
        page = json.loads(page_path.read_text(encoding="utf-8-sig"))
        if not isinstance(page, dict):
            raise ValueError("查询未返回 combined JSON 对象")
        if process.returncode or page.get("status") not in {"success", "partial"}:
            return {**page, "status": "error" if merged or process.returncode else page.get("status"),
                    "message": page.get("message") or "覆盖率分页查询未完成"}, process.returncode
        returned_scope = page.get("scope") or page.get("meta", {}).get("scope", {})
        requested_scope = argv[argv.index("--scope") + 1]
        if str(returned_scope.get("requested", "")).replace("\\", "/").strip("/").casefold() != requested_scope.casefold():
            raise ValueError("返回覆盖率范围与请求不一致，请核对查询 Skill")
        if returned_scope.get("recursive") is not ("--no-recursive" not in argv):
            raise ValueError("返回覆盖率递归模式与请求不一致")
        sources = page.get("snapshot", {}).get("sources", [])
        current = sorted((item.get("source", ""), item.get("coverage_timestamp", "")) for item in sources)
        current += sorted((item.get("source", ""), ",".join(sorted(item.get("metrics", []))), item.get("coverage_timestamp", ""))
                          for item in page.get("summary", {}).get("per_source", []))
        identity = (current, page.get("scope", {}).get("requested"), page.get("scope", {}).get("recursive"),
                    page.get("product"), page.get("c_version"), page.get("b_version"), page.get("summary"))
        if merged is None:
            merged = copy.deepcopy(page)
            fingerprint = identity
        else:
            if identity != fingerprint:
                raise ValueError("覆盖率报告来源、时间戳或范围已变化，请重新查询；未合并不同报告")
            for section, kinds in (("uncovered", ("functions", "lines", "branches")), ("indeterminate", ("branches",))):
                for kind in kinds:
                    merged.setdefault(section, {}).setdefault(kind, []).extend(page.get(section, {}).get(kind, []))
            for key in ("uncovered_functions", "uncovered_lines", "uncovered_branches", "missing", "warnings"):
                merged.setdefault(key, []).extend(page.get(key, []))
            if page["status"] == "partial":
                merged["status"] = "partial"
        pagination = page.get("pagination")
        if not isinstance(pagination, dict) or not isinstance(pagination.get("has_more"), bool):
            raise ValueError("查询缺少有效分页信息，无法确认完整性")
        if pagination.get("has_more") is False:
            if pagination:
                merged["pagination"] = {**pagination, "offset": 0, "complete": True,
                    "has_more": False, "continue_with": None,
                    "returned_files": pagination.get("total_files")}
            return merged, 0
        continuation = pagination.get("continue_with") or {}
        next_offset = continuation.get("offset")
        if not current or not isinstance(next_offset, int) or next_offset <= offset:
            raise ValueError("覆盖率分页缺少快照信息或有效续页位置，无法确认完整性")
        offset = next_offset
        limit = continuation.get("limit", limit)
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("覆盖率续页 limit 无效")
