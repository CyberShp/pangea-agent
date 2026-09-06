#!/usr/bin/env python3
"""Codetalks Skill 1.3.0 Markdown-first workflow and directory-layout guard."""

from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

RESERVED_DIR_NAMES = {"活文档", "内部索引", "正式输出"}
FORBIDDEN_NESTED = [
    Path("活文档/活文档"),
    Path("活文档/内部索引"),
    Path("活文档/正式输出"),
    Path("内部索引/活文档"),
    Path("正式输出/活文档"),
]
STEP_FILE_RE = re.compile(r"^\d{2}-.*\.md$")
FORMAL_STEP_FILE_RE = re.compile(r"^\d{2}-.*\.md$")
PUBLICATION_STEPS = {"03", "04", "05", "07", "08", "09"}

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def artifact_metrics(root: Path) -> dict[str, int]:
    """Return cheap output metrics without scanning the frozen source tree."""
    count = 0
    byte_count = 0
    for directory_name in ("活文档", "正式输出"):
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            try:
                byte_count += path.stat().st_size
                count += 1
            except OSError:
                # A concurrent Agent write is not a reason to fail a guard
                # command; the next progress/complete event will refresh it.
                continue
    return {"artifact_count": count, "artifact_bytes": byte_count}


def elapsed_ms(started_at: str | None, ended_at: str) -> int | None:
    if not isinstance(started_at, str) or not started_at:
        return None
    try:
        started = dt.datetime.fromisoformat(started_at)
        ended = dt.datetime.fromisoformat(ended_at)
    except ValueError:
        return None
    return max(0, int((ended - started).total_seconds() * 1000))


def performance_state(state: dict) -> dict:
    value = state.get("performance")
    if not isinstance(value, dict):
        value = {}
    steps = value.get("steps")
    if not isinstance(steps, dict):
        steps = {}
    value["version"] = 1
    value["steps"] = steps
    value.setdefault("progress_updates", 0)
    return value


def finish_step_timing(state: dict, root: Path, step_id: str) -> None:
    performance = performance_state(state)
    timing = performance["steps"].get(step_id)
    if not isinstance(timing, dict):
        return
    ended_at = now()
    current_metrics = artifact_metrics(root)
    timing["ended_at"] = ended_at
    timing["duration_ms"] = elapsed_ms(timing.get("started_at"), ended_at)
    timing["artifact_count_end"] = current_metrics["artifact_count"]
    timing["artifact_bytes_end"] = current_metrics["artifact_bytes"]
    timing["artifact_count_delta"] = current_metrics["artifact_count"] - int(timing.get("artifact_count_start", 0))
    timing["artifact_bytes_delta"] = current_metrics["artifact_bytes"] - int(timing.get("artifact_bytes_start", 0))
    performance["updated_at"] = ended_at
    state["performance"] = performance

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temp.replace(path)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def resolve_run_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if root.name in RESERVED_DIR_NAMES:
        raise SystemExit(
            f"`--workspace` 必须指向三个目录的共同父目录，不能直接指向 `{root.name}/`。\n"
            f"正确示例：--workspace {root.parent}\n"
            f"系统将自动创建：{root.parent}/活文档、{root.parent}/内部索引、{root.parent}/正式输出"
        )
    return root

def state_path(root: Path) -> Path:
    return root / "内部索引/运行状态.json"

def ensure_state(root: Path) -> dict:
    path = state_path(root)
    if not path.exists():
        raise SystemExit(f"任务未初始化：{path}")
    return load_json(path)

def load_manifest(state: dict) -> dict:
    return load_json(Path(state["skill_root"]) / "workflow-manifest.json")

def find_step(manifest: dict, step_id: str) -> dict:
    for step in manifest["steps"]:
        if step["id"] == step_id:
            return step
    raise SystemExit(f"未知步骤：{step_id}")

def nonspace_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))

def strip_tables_code_and_headers(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    retained = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("|"):
            continue
        if re.fullmatch(r"[-:| ]+", stripped):
            continue
        retained.append(stripped)
    return "\n".join(retained)

def validate_markdown(path: Path, minimum: int) -> list[str]:
    if not path.exists():
        return [f"缺少 Markdown：{path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    errors = []
    if nonspace_length(text) < minimum:
        errors.append(f"Markdown 内容过少（{nonspace_length(text)} < {minimum}）：{path}")
    narrative = strip_tables_code_and_headers(text)
    if nonspace_length(narrative) < max(120, minimum // 4):
        errors.append(f"Markdown 叙述不足，疑似纯表格或空模板：{path}")
    if re.search(r'"(?:flow_id|branch_id|risk_id|case_id)"\s*:', text):
        errors.append(f"Markdown 疑似直接倾倒 JSON：{path}")
    return errors

def section_text(text: str, heading: str, headings: list[str]) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    start += len(heading)
    candidates = [text.find(h, start) for h in headings if text.find(h, start) >= 0]
    end = min(candidates) if candidates else len(text)
    return text[start:end]

def validate_flow(path: Path, manifest: dict) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    errors = []
    headings = manifest["flow_required_headings"]
    for heading in headings:
        if heading not in text:
            errors.append(f"流程讲解缺少章节“{heading}”：{path}")
    if nonspace_length(text) < 2200:
        errors.append(f"流程讲解过短，无法视为完整开发讲解：{path}")
    for heading in manifest["flow_key_narrative_headings"]:
        section = section_text(text, heading, headings)
        if nonspace_length(strip_tables_code_and_headers(section)) < 90:
            errors.append(f"流程关键章节缺少自然语言叙述“{heading}”：{path}")
    return errors

def validate_coverage(path: Path, manifest: dict) -> list[str]:
    errors = validate_markdown(path, 300)
    if not path.exists():
        return errors
    text = path.read_text(encoding="utf-8", errors="replace")
    if "Pass ID" not in text and "分析项" not in text:
        errors.append(f"Coverage Gate 缺少标准表头：{path}")
    if re.search(r"\bskipped\b|跳过", text, flags=re.I):
        errors.append(f"Coverage Gate 使用了禁止状态 skipped/跳过：{path}")
    if not any(status in text for status in manifest["coverage_allowed_outcomes"]):
        errors.append(f"Coverage Gate 未包含合法 Outcome：{path}")
    return errors

def validate_evidence_index(root: Path, manifest: dict) -> list[str]:
    path = root / "内部索引/输入材料索引.json"
    if not path.exists():
        return [f"缺少输入材料内部索引：{path}"]
    try:
        data = load_json(path)
    except Exception as exc:
        return [f"输入材料索引无法解析：{exc}"]
    allowed = set(manifest["evidence_allowed_status"])
    errors = []
    for number, item in enumerate(data.get("items", []), 1):
        identity = item.get("id", str(number))
        status = item.get("status")
        if status not in allowed:
            errors.append(f"材料 {identity} 状态非法：{status}")
        if status in {"parsed", "partially_parsed"}:
            if not item.get("parser"):
                errors.append(f"材料 {identity} 缺少 parser")
            if not item.get("consumed_ranges"):
                errors.append(f"材料 {identity} 缺少 consumed_ranges")
            kind = str(item.get("type", "")).lower()
            if kind in {"xlsx", "xls", "spreadsheet", "coverage_xlsx"}:
                if not item.get("sheets"):
                    errors.append(f"电子表格 {identity} 未记录 sheets")
                rows = item.get("records_parsed")
                if not isinstance(rows, int) or rows <= 0:
                    errors.append(f"电子表格 {identity} 未记录有效 records_parsed")
    return errors

def validate_methodology_selection(root: Path) -> list[str]:
    """Validate the Step 01 receipt against the frozen catalog, never infer IDs."""
    selection_path = root / "内部索引/方法论选择.json"
    catalog_path = root / "inputs/methodologies/catalog.json"
    errors = []
    if not selection_path.exists():
        return [f"缺少方法论选择收据：{selection_path}"]
    try:
        selection = load_json(selection_path)
    except Exception as exc:
        return [f"方法论选择收据无法解析：{exc}"]
    if not isinstance(selection, dict):
        return ["方法论选择收据必须是 JSON 对象"]
    if selection.get("schema_version") != "1.0":
        errors.append("方法论选择收据 schema_version 必须是 1.0")
    if not isinstance(selection.get("selected"), list) or not isinstance(selection.get("excluded"), list):
        return [*errors, "方法论选择收据必须包含 selected/excluded 数组"]
    known = {"codetalks-skill"}
    if catalog_path.is_file():
        try:
            catalog = load_json(catalog_path)
            known.update(item.get("methodology_id") for item in catalog.get("builtin_methodologies", []))
            known.update(item.get("methodology_id") for item in catalog.get("enabled_user_methodologies", []))
        except Exception as exc:
            errors.append(f"冻结方法论目录无法解析：{exc}")
    seen = set()
    for bucket in ("selected", "excluded"):
        for number, item in enumerate(selection[bucket], 1):
            if not isinstance(item, dict):
                errors.append(f"{bucket}[{number}] 必须是对象")
                continue
            identifier = item.get("methodology_id")
            if not isinstance(identifier, str) or not identifier.strip():
                errors.append(f"{bucket}[{number}] 缺少 methodology_id")
                continue
            if identifier not in known:
                errors.append(f"方法论 ID 不在冻结目录中：{identifier}")
            if identifier in seen:
                errors.append(f"方法论不能重复出现在 selected/excluded：{identifier}")
            seen.add(identifier)
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                errors.append(f"{bucket}[{number}] 缺少 reason：{identifier}")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence or any(not isinstance(value, str) or not value.strip() for value in evidence):
                errors.append(f"{bucket}[{number}] 缺少 evidence：{identifier}")
    if "codetalks-skill" not in {
        item.get("methodology_id") for item in selection["selected"] if isinstance(item, dict)
    }:
        errors.append("内置 Codetalks Skill 必须出现在 selected")
    return errors

def validate_workbench_projection_data(data: Any, run_id: str) -> list[str]:
    """Validate only the machine-readable shape and cross-references."""
    if not isinstance(data, dict):
        return ["工作台结构化投影必须是 JSON 对象"]
    errors: list[str] = []
    if data.get("schema_version") != "1.0":
        errors.append("工作台结构化投影 schema_version 必须是 1.0")
    if data.get("run_id") != run_id:
        errors.append("工作台结构化投影 run_id 必须与当前 Run 一致")
    for key in ("business_flows", "risks", "test_cases", "evidence", "review_issues"):
        if not isinstance(data.get(key), list):
            errors.append(f"工作台结构化投影缺少数组：{key}")
    ids: dict[str, set[str]] = {}
    for key, id_key in (("business_flows", "flow_id"), ("risks", "risk_id"),
                        ("test_cases", "test_case_id"), ("evidence", "evidence_id"),
                        ("review_issues", "issue_id")):
        values = data.get(key, [])
        seen: set[str] = set()
        for index, item in enumerate(values, 1):
            if not isinstance(item, dict):
                errors.append(f"{key}[{index}] 必须是对象")
                continue
            identifier = item.get(id_key)
            if not isinstance(identifier, str) or not identifier.strip():
                errors.append(f"{key}[{index}] 缺少 {id_key}")
            elif identifier in seen:
                errors.append(f"{key} 存在重复 ID：{identifier}")
            else:
                seen.add(identifier)
        ids[key] = seen
    risk_ids = ids.get("risks", set())
    case_ids = ids.get("test_cases", set())
    evidence_ids = ids.get("evidence", set())
    for index, item in enumerate(data.get("risks", []), 1):
        if not isinstance(item, dict):
            continue
        for linked in item.get("linked_test_case_ids", []):
            if linked not in case_ids:
                errors.append(f"risks[{index}] 引用了不存在的 test_case_id：{linked}")
        for linked in item.get("evidence_ids", []):
            if linked not in evidence_ids:
                errors.append(f"risks[{index}] 引用了不存在的 evidence_id：{linked}")
    for index, item in enumerate(data.get("test_cases", []), 1):
        if not isinstance(item, dict):
            continue
        for linked in item.get("linked_risk_ids", []):
            if linked not in risk_ids:
                errors.append(f"test_cases[{index}] 引用了不存在的 risk_id：{linked}")
    for index, item in enumerate(data.get("evidence", []), 1):
        if not isinstance(item, dict):
            continue
        location = item.get("location") or item.get("path")
        if isinstance(location, str) and (Path(location).is_absolute() or ".." in Path(location).parts):
            errors.append(f"evidence[{index}] location 越过受控边界：{location}")
    return errors


def validate_workbench_projection(root: Path) -> list[str]:
    path = root / "内部索引/工作台投影.json"
    if not path.exists():
        return [f"缺少工作台结构化投影：{path}"]
    try:
        data = load_json(path)
    except Exception as exc:
        return [f"工作台结构化投影无法解析：{exc}"]
    return validate_workbench_projection_data(data, root.name)

def validation_records(errors: list[str], *, command: str, step: str | None = None) -> list[dict[str, str]]:
    records = []
    for message in errors:
        lowered = message.lower()
        if "缺少" in message or "不存在" in message:
            code = "missing_artifact"
        elif "无法解析" in message or "json" in lowered:
            code = "invalid_layout"
        elif "过短" in message or "叙述不足" in message:
            code = "minimum_content_not_met"
        elif "投影" in message:
            code = "projection_invalid"
        elif "审查" in message:
            code = "judge_incomplete"
        else:
            code = "validation_error"
        records.append({"code": code, "step": step or "", "message": message, "command": command})
    return records

def save_validation(state: dict, errors: list[str], *, command: str, step: str | None = None) -> None:
    state["validation"] = {
        "status": "failed" if errors else "passed",
        "checked_at": now(),
        "command": command,
        "step": step,
        "error_count": len(errors),
        "errors": validation_records(errors, command=command, step=step),
    }
    state["updated_at"] = now()

def validate_layout(root: Path, *, final_phase: bool = False) -> list[str]:
    errors = []

    # Three sibling directories must exist directly under the run root.
    for name in ["活文档", "内部索引", "正式输出"]:
        path = root / name
        if not path.exists() or not path.is_dir():
            errors.append(f"缺少顶层同级目录：{path}")

    # Reject nested copies and misplaced sibling directories.
    for relative in FORBIDDEN_NESTED:
        path = root / relative
        if path.exists():
            errors.append(f"检测到禁止的嵌套目录：{path}")

    # Reject numbered process documents scattered at root level.
    for path in root.iterdir() if root.exists() else []:
        if path.is_file() and STEP_FILE_RE.match(path.name):
            errors.append(f"步骤文档不得散落在运行根目录：{path}")

    # Internal index must never be an empty placeholder.
    index_dir = root / "内部索引"
    required_index = [
        index_dir / "运行状态.json",
        index_dir / "运行计划.json",
        index_dir / "输入材料索引.json",
    ]
    for path in required_index:
        if not path.exists():
            errors.append(f"内部索引缺少必要文件：{path}")

    # Formal output contains final deliverables only; no numbered step files or subdirectories.
    formal_dir = root / "正式输出"
    if formal_dir.exists():
        for path in formal_dir.iterdir():
            if path.is_dir():
                errors.append(f"正式输出目录不得包含子目录：{path}")
            elif FORMAL_STEP_FILE_RE.match(path.name):
                errors.append(f"步骤编号文件不得放入正式输出目录：{path}")
        if not final_phase and any(formal_dir.iterdir()):
            errors.append("Step 01–08 期间 `正式输出/` 必须保持为空；正式交付只在 Step 09 生成。")

    return errors

def validate_step(root: Path, step: dict, manifest: dict, *, final_phase: bool | None = None) -> list[str]:
    if final_phase is None:
        final_phase = step["id"] == "09"
    errors = validate_layout(root, final_phase=final_phase)
    minimum = int(step.get("markdown_min_chars", 300))

    for relative in step.get("required", []):
        path = root / relative
        if not path.exists() or (path.is_file() and path.stat().st_size == 0):
            errors.append(f"缺少或为空：{relative}")
            continue
        if path.suffix.lower() == ".md":
            if "覆盖门禁" in relative:
                errors.extend(validate_coverage(path, manifest))
            else:
                errors.extend(validate_markdown(path, minimum))
        elif relative == "内部索引/工作台投影.json":
            errors.extend(validate_workbench_projection(root))

    for pattern in step.get("requires_glob", []):
        matches = list(root.glob(pattern))
        if not matches:
            errors.append(f"未找到必需工件：{pattern}")
        elif step.get("flow_narrative_validation"):
            for path in matches:
                errors.extend(validate_flow(path, manifest))

    if step["id"] == "02":
        errors.extend(validate_evidence_index(root, manifest))

    if "内部索引/方法论选择.json" in step.get("required", []):
        errors.extend(validate_methodology_selection(root))

    if step["id"] == "08":
        judge_path = root / "内部索引/独立审查状态.json"
        if judge_path.exists():
            try:
                judge = load_json(judge_path)
                if judge.get("independent") is not True:
                    errors.append("独立审查状态未声明 independent=true")
                if not judge.get("checked_artifacts"):
                    errors.append("独立审查未记录 checked_artifacts")
            except Exception as exc:
                errors.append(f"独立审查状态无法解析：{exc}")

    return errors


def validate_completed_steps(root: Path, state: dict, manifest: dict) -> list[str]:
    completed_steps = state.get("completed_steps", [])
    final_phase = "09" in completed_steps
    errors = []
    for step_id in completed_steps:
        errors.extend(validate_step(
            root,
            find_step(manifest, step_id),
            manifest,
            final_phase=final_phase,
        ))
    return errors

def require_core_rules(state: dict, manifest: dict) -> None:
    missing = [
        rule for rule in manifest["required_core_rules"]
        if rule not in state.get("core_rules_ack", {})
    ]
    if missing:
        raise SystemExit("核心规则未 ACK：" + ", ".join(missing))


def _publication_record(state: dict) -> dict:
    current = state.get("publication")
    if not isinstance(current, dict):
        current = {"state": "pending", "revision": 0, "step_id": None}
    try:
        revision = max(0, int(current.get("revision", 0)))
    except (TypeError, ValueError):
        revision = 0
    return {
        "state": current.get("state") if current.get("state") in {"pending", "draft", "final", "broken"} else "pending",
        "revision": revision,
        "step_id": current.get("step_id") if isinstance(current.get("step_id"), str) else None,
        **({"updated_at": current["updated_at"]} if isinstance(current.get("updated_at"), str) else {}),
    }


def command_publish_stage(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    step_id = str(args.step)
    if step_id not in PUBLICATION_STEPS:
        raise SystemExit("只有 Step 03、04、05、07、08、09 可以发布阶段投影")
    if state.get("current_step") != step_id and step_id not in state.get("completed_steps", []):
        raise SystemExit(f"只能发布当前步骤或已完成步骤的阶段投影：Step {step_id}")

    source = Path(args.projection).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"阶段投影文件不存在：{source}")
    try:
        data = load_json(source)
    except Exception as exc:
        raise SystemExit(f"阶段投影无法解析：{exc}") from exc
    errors = validate_workbench_projection_data(data, root.name)
    if errors:
        save_validation(state, errors, command="publish-stage", step=step_id)
        save_json(state_path(root), state)
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    previous = _publication_record(state)
    revision = previous["revision"] + 1
    publication = {
        "state": "final" if step_id == "09" and step_id in state.get("completed_steps", []) else "draft",
        "revision": revision,
        "step_id": step_id,
        "updated_at": now(),
    }
    published = dict(data)
    published["publication"] = publication
    save_json(root / "内部索引/工作台投影.json", published)
    state["publication"] = publication
    save_validation(state, [], command="publish-stage", step=step_id)
    state["updated_at"] = now()
    save_json(state_path(root), state)
    print(json.dumps({"ok": True, "publication": publication}, ensure_ascii=False))

def command_init(args) -> None:
    root = resolve_run_root(args.workspace)
    root.mkdir(parents=True, exist_ok=True)
    resume = bool(getattr(args, "resume", False))
    existing_state_path = state_path(root)
    if resume:
        if not existing_state_path.is_file():
            raise SystemExit(f"无法继续：运行状态不存在：{existing_state_path}")
        state = load_json(existing_state_path)
        if state.get("status") == "complete":
            raise SystemExit("已经完成的 Skill Run 不能继续")
        for relative in [
            "活文档/流程讲解",
            "活文档/覆盖门禁",
            "内部索引",
            "正式输出",
        ]:
            (root / relative).mkdir(parents=True, exist_ok=True)
        state["resume_count"] = int(state.get("resume_count", 0)) + 1
        state["last_resumed_at"] = now()
        state["status"] = "in_progress"
        state["updated_at"] = now()
        save_json(existing_state_path, state)
        for relative, default in [
            ("内部索引/运行计划.json", {"version": "1.3.0", "passes": []}),
            ("内部索引/输入材料索引.json", {"version": "1.3.0", "items": []}),
        ]:
            path = root / relative
            if not path.is_file():
                save_json(path, default)
        layout_errors = validate_layout(root, final_phase=False)
        if layout_errors:
            raise SystemExit("\n".join(layout_errors))
        print(json.dumps({
            "ok": True,
            "resumed": True,
            "run_root": str(root),
            "current_step": state.get("current_step"),
            "completed_steps": state.get("completed_steps", []),
            "resume_count": state["resume_count"],
        }, ensure_ascii=False))
        return
    if existing_state_path.is_file():
        raise SystemExit(f"运行已初始化；如需继续请使用 `init --resume`：{existing_state_path}")
    skill_root = Path(args.skill_root).expanduser().resolve()
    manifest = load_json(skill_root / "workflow-manifest.json")

    for relative in [
        "活文档/流程讲解",
        "活文档/覆盖门禁",
        "内部索引",
        "正式输出",
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)

    state = {
        "version": "1.3.0",
        "created_at": now(),
        "updated_at": now(),
        "skill_root": str(skill_root),
        "run_root": str(root),
        "source_raw": args.source_raw,
        "source_verified": args.source_verified,
        "scenario": args.scenario,
        "mode": args.mode,
        "status": "initialized",
        "current_step": None,
        "completed_steps": [],
        "core_rules_ack": {},
        "judge": {
            "required": args.scenario == "module-analysis" and args.mode == "depth",
            "status": "pending",
        },
        "publication": {
            "state": "pending",
            "revision": 0,
            "step_id": None,
            "updated_at": now(),
        },
        "performance": {
            "version": 1,
            "steps": {},
            "progress_updates": 0,
            "updated_at": now(),
        },
        "verdict": None,
        "step_progress": None,
        "validation": {"status": "not_checked", "error_count": 0, "errors": []},
    }
    save_json(state_path(root), state)
    save_json(root / "内部索引/运行计划.json", {"version": "1.3.0", "passes": []})
    save_json(root / "内部索引/输入材料索引.json", {"version": "1.3.0", "items": []})

    layout_errors = validate_layout(root, final_phase=False)
    if layout_errors:
        raise SystemExit("\n".join(layout_errors))

    print(json.dumps({
        "ok": True,
        "run_root": str(root),
        "created_sibling_directories": [
            str(root / "活文档"),
            str(root / "内部索引"),
            str(root / "正式输出"),
        ],
        "note": "Step 01–08 只写活文档和内部索引；正式输出仅在 Step 09 生成。"
    }, ensure_ascii=False))

def command_ack(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    required = manifest["required_core_rules"]
    if args.rule not in required:
        raise SystemExit(f"未知核心规则：{args.rule}")
    expected = (Path(state["skill_root"]) / required[args.rule]).resolve()
    actual = Path(args.file).expanduser().resolve()
    if expected != actual or not actual.exists():
        raise SystemExit(f"核心规则文件不匹配：期望 {expected}，实际 {actual}")
    state["core_rules_ack"][args.rule] = {
        "file": str(actual),
        "sha256": sha256(actual),
        "ack_at": now(),
    }
    state["updated_at"] = now()
    save_json(state_path(root), state)
    print(json.dumps({"ok": True, "ack": args.rule}, ensure_ascii=False))

def command_start(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    require_core_rules(state, manifest)
    layout_errors = validate_layout(root, final_phase=args.step == "09")
    if layout_errors:
        raise SystemExit("\n".join(layout_errors))

    step = find_step(manifest, args.step)
    ids = [item["id"] for item in manifest["steps"]]
    previous = ids[:ids.index(args.step)]
    missing = [item for item in previous if item not in state["completed_steps"]]
    if missing:
        raise SystemExit("前置步骤未完成：" + ", ".join(missing))
    if state["current_step"] not in {None, args.step}:
        raise SystemExit(f"当前仍在 Step {state['current_step']}")

    started_at = now()
    performance = performance_state(state)
    previous_timing = performance["steps"].get(args.step)
    attempt = int(previous_timing.get("attempt", 0)) + 1 if isinstance(previous_timing, dict) else 1
    start_metrics = artifact_metrics(root)
    performance["steps"][args.step] = {
        "step": args.step,
        "attempt": attempt,
        "started_at": started_at,
        "ended_at": None,
        "duration_ms": None,
        "progress_updates": 0,
        "artifact_count_start": start_metrics["artifact_count"],
        "artifact_bytes_start": start_metrics["artifact_bytes"],
    }
    performance["updated_at"] = started_at
    state["performance"] = performance
    state["current_step"] = args.step
    state["status"] = "in_progress"
    state["step_progress"] = {
        "version": 1,
        "step": args.step,
        "status": "running",
        "unit_label": "步骤",
        "total": None,
        "completed": 0,
        "current": None,
        "started_at": started_at,
        "updated_at": started_at,
    }
    state["validation"] = {"status": "not_checked", "error_count": 0, "errors": []}
    state["updated_at"] = now()
    save_json(state_path(root), state)
    print(json.dumps({
        "ok": True,
        "step": args.step,
        "only_load_step_file": str(Path(state["skill_root"]) / step["file"]),
        "write_scope": "正式输出/" if args.step == "09" else "活文档/ 与 内部索引/"
    }, ensure_ascii=False))

def command_complete(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    if state.get("current_step") != args.step:
        raise SystemExit(f"当前步骤为 {state.get('current_step')}，不是 {args.step}")
    errors = validate_step(root, find_step(manifest, args.step), manifest)
    if errors:
        save_validation(state, errors, command="complete-step", step=args.step)
        save_json(state_path(root), state)
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    if args.step not in state["completed_steps"]:
        state["completed_steps"].append(args.step)
    finish_step_timing(state, root, args.step)
    state["current_step"] = None
    if isinstance(state.get("step_progress"), dict):
        state["step_progress"]["status"] = "completed"
        state["step_progress"]["completed"] = state["step_progress"].get("total") or state["step_progress"].get("completed", 0)
        state["step_progress"]["updated_at"] = now()
    save_validation(state, [], command="complete-step", step=args.step)
    state["updated_at"] = now()
    if args.step == "08":
        state["judge"]["status"] = "complete"
    save_json(state_path(root), state)
    print(json.dumps({"ok": True, "completed_step": args.step}, ensure_ascii=False))

def command_validate(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    require_core_rules(state, manifest)
    errors = validate_completed_steps(root, state, manifest)
    save_validation(state, errors, command="validate")
    save_json(state_path(root), state)
    print(json.dumps({
        "ok": not errors,
        "errors": errors,
        "completed_steps": state.get("completed_steps", []),
        "current_step": state.get("current_step"),
    }, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(2)

def command_progress(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    if state.get("current_step") != args.step:
        raise SystemExit(f"当前步骤为 {state.get('current_step')}，不是 {args.step}")
    progress = state.get("step_progress")
    if not isinstance(progress, dict):
        progress = {"version": 1, "step": args.step, "status": "running", "completed": 0, "current": None}
    if args.total is not None:
        if args.total < 0:
            raise SystemExit("total 不能小于 0")
        progress["total"] = args.total
    if args.completed is not None:
        previous = int(progress.get("completed") or 0)
        if args.completed < previous:
            raise SystemExit("completed 不能倒退")
        total = progress.get("total")
        if isinstance(total, int) and args.completed > total:
            raise SystemExit("completed 不能超过 total")
        progress["completed"] = args.completed
    if args.unit_label:
        progress["unit_label"] = args.unit_label
    if args.item_id or args.item_title:
        if not args.item_id or not args.item_title:
            raise SystemExit("item-id 和 item-title 必须同时提供")
        progress["current"] = {"id": args.item_id, "title": args.item_title}
    if args.status:
        progress["status"] = args.status
    progress["step"] = args.step
    progress.setdefault("version", 1)
    progress.setdefault("started_at", now())
    progress["updated_at"] = now()
    state["step_progress"] = progress
    performance = performance_state(state)
    performance["progress_updates"] = int(performance.get("progress_updates", 0)) + 1
    timing = performance["steps"].get(args.step)
    if isinstance(timing, dict):
        timing["progress_updates"] = int(timing.get("progress_updates", 0)) + 1
    performance["updated_at"] = progress["updated_at"]
    state["performance"] = performance
    state["updated_at"] = now()
    save_json(state_path(root), state)
    print(json.dumps({"ok": True, "step_progress": progress}, ensure_ascii=False))

def command_handoff(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    completed = set(state.get("completed_steps", []))
    remaining = [step for step in manifest["steps"] if step["id"] not in completed]
    next_step = remaining[0] if remaining else None

    lines = [
        "# 任务交接",
        "",
        f"- 更新时间：{now()}",
        f"- 运行根目录：`{root}`",
        f"- 场景：{state['scenario']}",
        f"- 模式：{state['mode']}",
        f"- 当前步骤：{state.get('current_step') or '无'}",
        f"- 已完成步骤：{', '.join(state.get('completed_steps', [])) or '无'}",
        "",
        "## 当前活文档",
    ]
    documents = sorted(str(path.relative_to(root)) for path in (root / "活文档").rglob("*.md"))
    lines.extend([f"- `{item}`" for item in documents] or ["- 暂无"])

    lines += ["", "## 动态下一步"]
    if next_step:
        lines += [
            f"1. 启动 Step {next_step['id']}。",
            f"2. 只读取 `{next_step['file']}`。",
            "3. 按目录契约写入对应目录。",
        ]
    else:
        lines.append("所有步骤完成，执行 validate 和 finalize。")

    path = root / "活文档/任务交接.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "handoff": str(path)}, ensure_ascii=False))

def command_finalize(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    errors = validate_completed_steps(root, state, manifest)
    for step in manifest["steps"]:
        if step["id"] not in state.get("completed_steps", []):
            errors.append(f"未完成步骤：{step['id']}")
    if state.get("judge", {}).get("required") and state.get("judge", {}).get("status") != "complete":
        errors.append("深度型模块全量分析未完成独立审查")

    if errors:
        state["status"] = "validation_failed"
        state["verdict"] = "PARTIAL"
        save_validation(state, errors, command="finalize")
        save_json(state_path(root), state)
        print(json.dumps({"ok": False, "verdict": "PARTIAL", "errors": errors},
                         ensure_ascii=False, indent=2))
        raise SystemExit(2)

    state["status"] = "complete"
    state["verdict"] = "READY"
    projection_path = root / "内部索引/工作台投影.json"
    if projection_path.is_file():
        try:
            projection = load_json(projection_path)
            publication = _publication_record(state)
            publication = {
                "state": "final",
                "revision": publication["revision"] + 1,
                "step_id": "09",
                "updated_at": now(),
            }
            projection["publication"] = publication
            save_json(projection_path, projection)
            state["publication"] = publication
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # validate_step already checked the projection; leave its exact
            # validation result intact if a final metadata write fails.
            pass
    save_validation(state, [], command="finalize")
    state["updated_at"] = now()
    save_json(state_path(root), state)
    print(json.dumps({
        "ok": True,
        "verdict": "READY",
        "formal_output": str(root / "正式输出"),
    }, ensure_ascii=False))

def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--skill-root", required=True)
    init.add_argument("--workspace", required=True,
                      help="运行根目录；不得直接填写活文档/、内部索引/或正式输出/")
    init.add_argument("--source-raw", required=True)
    init.add_argument("--source-verified", required=True)
    init.add_argument("--output", required=False,
                      help="兼容旧调用参数；1.1.0 正式输出固定为 <workspace>/正式输出/")
    init.add_argument("--scenario", required=True,
                      choices=["module-analysis", "issue-regression", "root-cause",
                               "special-risk", "custom"])
    init.add_argument("--mode", required=True, choices=["speed", "depth"])
    init.add_argument("--resume", action="store_true",
                      help="保留已有运行状态，从最近检查点继续")
    init.set_defaults(function=command_init)

    ack = commands.add_parser("ack-core")
    ack.add_argument("--workspace", required=True)
    ack.add_argument("--rule", required=True)
    ack.add_argument("--file", required=True)
    ack.set_defaults(function=command_ack)

    start = commands.add_parser("start-step")
    start.add_argument("--workspace", required=True)
    start.add_argument("--step", required=True)
    start.set_defaults(function=command_start)

    complete = commands.add_parser("complete-step")
    complete.add_argument("--workspace", required=True)
    complete.add_argument("--step", required=True)
    complete.set_defaults(function=command_complete)

    publish = commands.add_parser("publish-stage")
    publish.add_argument("--workspace", required=True)
    publish.add_argument("--step", required=True, choices=sorted(PUBLICATION_STEPS))
    publish.add_argument("--projection", required=True,
                         help="由 Agent 生成的、待校验的工作台投影 JSON 文件")
    publish.set_defaults(function=command_publish_stage)

    validate = commands.add_parser("validate")
    validate.add_argument("--workspace", required=True)
    validate.set_defaults(function=command_validate)

    progress = commands.add_parser("progress")
    progress.add_argument("--workspace", required=True)
    progress.add_argument("--step", required=True)
    progress.add_argument("--total", type=int)
    progress.add_argument("--completed", type=int)
    progress.add_argument("--unit-label")
    progress.add_argument("--item-id")
    progress.add_argument("--item-title")
    progress.add_argument("--status", choices=("running", "completed", "waiting"))
    progress.set_defaults(function=command_progress)

    handoff = commands.add_parser("handoff")
    handoff.add_argument("--workspace", required=True)
    handoff.set_defaults(function=command_handoff)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--workspace", required=True)
    finalize.set_defaults(function=command_finalize)

    args = parser.parse_args()
    args.function(args)

if __name__ == "__main__":
    main()
