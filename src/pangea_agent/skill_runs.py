from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.assets import load_asset
from pangea_agent.repositories.resolver import resolve_repository
from pangea_agent.skills import SOURCE_ROOT, freeze_skill_package, validate_skill_package


def _safe_run_id(value: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError("run_id 必须是非空文件名，且不能包含路径分隔符")
    return value


def _required_text(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串")
    return value.strip()


def _text_list(raw: dict, key: str) -> list[str]:
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} 必须是字符串数组")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _skill_request(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Skill Run request 必须是 JSON 对象")
    allowed = {"run_id", "data_root", "repository", "target", "source_scope", "focus", "asset_ids", "test_case_examples"}
    extras = sorted(set(raw) - allowed)
    if extras:
        raise ValueError(f"Skill Run request 包含未知字段：{', '.join(extras)}")
    run_id = raw.get("run_id")
    if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
        raise ValueError("run_id 必须是非空字符串")
    return {
        "run_id": run_id.strip() if isinstance(run_id, str) else None,
        "data_root": _required_text(raw, "data_root") if "data_root" in raw else "pangea-data",
        "repository": _required_text(raw, "repository"),
        "target": _required_text(raw, "target"),
        "source_scope": _text_list(raw, "source_scope"),
        "focus": _text_list(raw, "focus"),
        "asset_ids": _text_list(raw, "asset_ids"),
        "test_case_examples": _text_list(raw, "test_case_examples"),
    }


def _allocate_run_id(data_root: Path, target: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", target).strip("-_").lower()
    slug = (slug[:24].rstrip("-_") or "analysis")
    prefix = f"{slug}-{date.today():%y%m%d}"
    runs_root = data_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    sequence = 1
    while (runs_root / f"{prefix}-{sequence:02d}").exists():
        sequence += 1
    return f"{prefix}-{sequence:02d}"


def _request_root(data_root: Path, run_id: str) -> Path:
    return data_root / ".pangea" / "skill-runs" / run_id


def _metadata_path(data_root: Path, run_id: str) -> Path:
    return _request_root(data_root, run_id) / "metadata.json"


def _resolve_scope(repository_root: Path, source_scope: list[str]) -> list[dict[str, str]]:
    resolved = []
    repository_root = repository_root.resolve()
    for raw in source_scope:
        candidate = Path(raw)
        if candidate.is_absolute():
            raise ValueError(f"source_scope 必须使用仓库相对路径：{raw}")
        verified = (repository_root / candidate).resolve()
        try:
            verified.relative_to(repository_root)
        except ValueError as exc:
            raise ValueError(f"source_scope 越过仓库边界：{raw}") from exc
        if not verified.exists():
            raise ValueError(f"source_scope 不存在：{raw}")
        resolved.append({"raw": raw, "verified": str(verified)})
    return resolved


def _asset_lines(data_root: Path, asset_ids: list[str]) -> list[str]:
    if not asset_ids:
        return ["- 无"]
    lines = []
    for asset_id in asset_ids:
        record = load_asset(str(data_root), asset_id)
        lines.append(
            f"- `{record.asset_id}` | {record.asset_type} | `{record.source_path}` | {record.title}"
        )
    return lines


def _request_markdown(
    *,
    request: dict,
    run_id: str,
    run_root: Path,
    skill_root: Path,
    repository_root: Path,
    scope: list[dict[str, str]],
    asset_lines: list[str],
) -> str:
    scope_lines = [
        f"- raw=`{item['raw']}`\n  verified=`{item['verified']}`" for item in scope
    ] or ["- 用户未限定子路径；以已验证仓库根目录为范围起点，在 Step 01 内收敛范围。"]
    focus_lines = [f"- {item}" for item in request["focus"]] or ["- 无额外重点"]
    example_lines = [f"- `{item}`" for item in request["test_case_examples"]] or ["- 无"]
    return "\n".join([
        "# Codetalks Skill 运行请求",
        "",
        "这是用户已经确认启动的深度型模块全量分析。立即执行，不再创建 PANGEA Graph、Planning、Worker action 或 settle。",
        "",
        "## 唯一执行协议",
        "",
        f"- Skill：`{skill_root / 'SKILL.md'}`",
        f"- Skill 根目录：`{skill_root}`",
        f"- Run ID：`{run_id}`",
        f"- 运行根目录：`{run_root}`",
        "- 场景：`module-analysis`",
        "- 模式：`depth`",
        "",
        "先完整读取 SKILL.md。随后第一条执行命令必须是该 Skill 规定的 `run_guard.py init`，再按 JIT 规则逐步执行 01–09。",
        "Producer 完成 Step 07 后必须启动一个与 Producer 分离的独立 Judge 执行 Step 08；Judge 只以运行计划、活文档、源码和证据为依据。",
        "只有 `run_guard.py finalize` 成功后才可宣称完成。",
        "",
        "## 分析对象",
        "",
        f"- target：{request['target']}",
        f"- repository：`{request['repository']}`",
        f"- source_raw：`{repository_root}`",
        f"- source_verified：`{repository_root.resolve()}`",
        "",
        "### 用户范围",
        "",
        *scope_lines,
        "",
        "### 分析重点",
        "",
        *focus_lines,
        "",
        "### 输入材料",
        "",
        *asset_lines,
        "",
        "### 用例表达参考",
        "",
        *example_lines,
        "",
        "## 输出约束",
        "",
        "所有过程内容写入运行根目录的 `活文档/`，内部状态只使用 Skill 允许的索引，最终交付只写 `正式输出/`。",
        "不要生成 `progress.json`、`final-state.json`、`agent-results/`、`report.md` 或 `report.html`。",
        "",
    ])


def create_skill_run(request_path_value: str) -> dict:
    path = Path(request_path_value)
    raw = json.loads(path.read_text(encoding="utf-8"))
    request = _skill_request(raw)
    data_root = Path(request["data_root"]).resolve()
    repository = resolve_repository(request["repository"], str(data_root))
    repository_root = Path(repository["source_root"]).resolve()
    run_id = _safe_run_id(request["run_id"]) if request["run_id"] else _allocate_run_id(
        data_root, request["target"]
    )
    run_root = data_root / "runs" / run_id
    request_root = _request_root(data_root, run_id)
    if run_root.exists() or request_root.exists():
        raise ValueError(f"Run 已存在：{run_id}")
    scope = _resolve_scope(repository_root, request["source_scope"])
    try:
        run_root.mkdir(parents=True)
        request_root.mkdir(parents=True)
        frozen_skill = freeze_skill_package(request_root / "skill")
        request_path = request_root / "request.md"
        request_path.write_text(_request_markdown(
            request=request,
            run_id=run_id,
            run_root=run_root,
            skill_root=frozen_skill,
            repository_root=repository_root,
            scope=scope,
            asset_lines=_asset_lines(data_root, request["asset_ids"]),
        ), encoding="utf-8")
        metadata = {
            "run_id": run_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "preparing",
            "request": request,
            "repository_root": str(repository_root),
            "source_scope": scope,
            "run_root": str(run_root),
            "skill_root": str(frozen_skill),
            "request_path": str(request_path),
        }
        write_json(_metadata_path(data_root, run_id), metadata)
    except Exception:
        shutil.rmtree(run_root, ignore_errors=True)
        shutil.rmtree(request_root, ignore_errors=True)
        raise
    return skill_run_detail(str(data_root), run_id)


def _state(data_root: Path, run_id: str) -> tuple[dict, dict | None]:
    metadata_path = _metadata_path(data_root, run_id)
    if not metadata_path.is_file():
        raise ValueError(f"Skill Run 元数据不存在：{run_id}")
    metadata = read_json(metadata_path)
    state_path = data_root / "runs" / run_id / "内部索引" / "运行状态.json"
    state = read_json(state_path) if state_path.is_file() else None
    return metadata, state


def _lifecycle(metadata: dict, state: dict | None) -> tuple[str, str, str | None]:
    if metadata.get("status") == "stopped":
        return "stopped", "STOPPED", None
    if state is None:
        return "preparing", "PREPARING", None
    status = state.get("status")
    if status == "complete":
        return "complete", "COMPLETE", state.get("verdict")
    if status == "validation_failed":
        return "attention_required", "INCOMPLETE", state.get("verdict")
    return "running", f"STEP_{state.get('current_step') or 'BOOTSTRAP'}", state.get("verdict")


def skill_run_detail(data_root: str, run_id: str) -> dict:
    root = Path(data_root).resolve()
    run_id = _safe_run_id(run_id)
    metadata, state = _state(root, run_id)
    lifecycle, phase, verdict = _lifecycle(metadata, state)
    run_root = Path(metadata["run_root"])
    formal_root = run_root / "正式输出"
    formal_outputs = sorted(str(path) for path in formal_root.glob("*.md")) if formal_root.is_dir() else []
    live_documents = sorted(str(path) for path in (run_root / "活文档").rglob("*.md")) if (run_root / "活文档").is_dir() else []
    report = formal_root / "完整分析报告.md"
    return {
        "run_id": run_id,
        "lifecycle_status": lifecycle,
        "phase": phase,
        "stage": state.get("current_step") if state else None,
        "verdict": verdict,
        "quality_status": verdict,
        "skill": {
            "skill_id": "codetalks-skill",
            "version": "1.0.0",
            "root_path": metadata["skill_root"],
        },
        "completed_steps": state.get("completed_steps", []) if state else [],
        "current_step": state.get("current_step") if state else None,
        "run_root": str(run_root),
        "request_path": metadata["request_path"],
        "target": metadata["request"]["target"],
        "repository": metadata["request"]["repository"],
        "source_scope": metadata["source_scope"],
        "artifacts": {
            "state": str(run_root / "内部索引" / "运行状态.json") if state else None,
            "live_documents": live_documents,
            "formal_outputs": formal_outputs,
            "report_markdown": str(report) if report.is_file() else None,
        },
        "report_available": report.is_file() and lifecycle == "complete",
        "attention_required": lifecycle == "attention_required",
    }


def list_skill_runs(data_root: str, *, cursor: int = 0, limit: int = 50) -> dict:
    if cursor < 0 or not 1 <= limit <= 200:
        raise ValueError("cursor/limit 超出范围")
    root = Path(data_root).resolve()
    request_root = root / ".pangea" / "skill-runs"
    ids = sorted(
        (path.name for path in request_root.iterdir() if path.is_dir()),
        reverse=True,
    ) if request_root.is_dir() else []
    items = []
    for run_id in ids[cursor:cursor + limit]:
        try:
            items.append(skill_run_detail(str(root), run_id))
        except Exception:
            continue
    next_cursor = cursor + len(items)
    return {
        "items": items,
        "next_cursor": next_cursor if next_cursor < len(ids) else None,
        "total": len(ids),
    }


def stop_skill_run(data_root: str, run_id: str) -> dict:
    root = Path(data_root).resolve()
    metadata, state = _state(root, _safe_run_id(run_id))
    if state and state.get("status") == "complete":
        raise ValueError("已经完成的 Skill Run 不能停止")
    metadata["status"] = "stopped"
    metadata["stopped_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_json(_metadata_path(root, run_id), metadata)
    return skill_run_detail(str(root), run_id)


def validate_runtime_skill() -> dict:
    validate_skill_package(SOURCE_ROOT)
    return {
        "skill_id": "codetalks-skill",
        "version": "1.0.0",
        "derived_from": "codetalks-fused-v2.4",
        "root_path": str(SOURCE_ROOT),
    }
