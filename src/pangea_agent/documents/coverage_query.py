"""Acquire coverage through the installation's private, read-only query skill."""
from __future__ import annotations

import json
import os
import subprocess
import sys
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
    """One combined query; keep acquisition facts separate from analysis verdicts."""
    from pangea_agent.assets import import_asset, prepare_asset_extraction, asset_detail
    from pangea_agent.documents.coverage import parse_coverage_combined

    for key in ("product", "c_version", "module"):
        if not isinstance(query.get(key), str) or not query[key].strip():
            raise ValueError(f"覆盖率查询缺少 {key}")
    if not isinstance(query.get("b_version", ""), str):
        raise ValueError("b_version 必须是字符串")
    query = {key: query.get(key, "") for key in ("product", "c_version", "module", "b_version")}
    capability = local_query_skill()
    if not capability["available"]:
        raise ValueError(f"请放入覆盖率查询 Skill：{capability['placement']}")
    folder = Path(data_root).resolve() / "coverage" / "queries" / uuid4().hex
    folder.mkdir(parents=True)
    write_json(folder / "query.json", query)
    output = folder / "combined.json"
    argv = [sys.executable, str(Path(capability["root_path"]) / "scripts/coverage_query.py"),
            "combined", "--product", query["product"], "--version", query["c_version"],
            "--module", query["module"]]
    if query["b_version"]:
        argv += ["--b-version", query["b_version"]]
    result = {"status": "error", "asset": None, "query_input": query,
              "raw_path": str(output), "stderr_path": str(folder / "query-stderr.log")}
    try:
        with output.open("wb") as stdout, (folder / "query-stderr.log").open("wb") as stderr:
            process = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=timeout)
        result["exit_code"] = process.returncode
        data = json.loads(output.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("status") not in {"success", "partial", "no_data", "error"}:
            raise ValueError("查询未返回含有效 status 的 combined JSON")
        for key in ("sources", "missing", "warnings"):
            if key in data and not isinstance(data[key], list):
                raise ValueError(f"combined.{key} 必须是数组")
        result.update({key: data.get(key) for key in ("status", "message", "query_resolution")})
        result["sources"] = data.get("sources", [])
        result["missing"] = data.get("missing", [])
        result["warnings"] = list(data.get("warnings") or [])
        resolution = data.get("query_resolution") or {}
        if not isinstance(resolution, dict):
            raise ValueError("query_resolution 必须是对象")
        if resolution.get("status") in {"not_found", "ambiguous"}:
            result.update(status="error", message=resolution.get("message") or "平台查询对象未找到或存在多个匹配")
        elif resolution.get("status") != "matched":
            result["warnings"].append("查询 Skill 未返回平台对象匹配结果；空数据不能证明版本存在或没有覆盖缺口")
        if process.returncode and result["status"] == "success":
            result.update(status="error", message=f"查询退出码 {process.returncode} 与 success 状态不一致")
        # Keep the original stdout untouched; the asset carries query provenance.
        if result["status"] in {"success", "partial"}:
            enriched = {**data, "query_input": query, "warnings": result["warnings"],
                        "acquisition": {"raw_path": str(output), "exit_code": process.returncode}}
            prepared = folder / "coverage.json"
            write_json(prepared, enriched)
            parsed = parse_coverage_combined(prepared)
            result["warnings"] = parsed["warnings"]
            asset = import_asset(data_root, str(prepared), "coverage",
                                 f"内网覆盖率 · {query['product']} / {query['c_version']} / {query['module']}")
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
