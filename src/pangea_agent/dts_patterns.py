"""DTS real-case feedback registry, offline/Git ingestion, and per-run freezing."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.models.dts_pattern import (
    DTSContextSnapshot,
    DTSPatternCard,
    DTSPatternInput,
    DTSPatternRegistry,
    DTSPatternStatus,
    FrozenHomologousDTS,
    utc_now,
)

VALID_TRACKS = ("error_rollback", "concurrency_timing", "resource_lifecycle")

_TRACK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "concurrency_timing": (
        "race", "deadlock", "lock", "mutex", "spinlock", "atomic", "yield", "coroutine",
        "timer", "callback", "reentr", "async", "cancel", "并发", "竞态", "死锁", "时序",
        "重入", "回调", "超时", "乱序", "挂起", "解锁", "持锁",
    ),
    "resource_lifecycle": (
        "leak", "uaf", "use-after-free", "double free", "double-free", "refcount", "refcnt",
        "fd", "socket", "handle", "pool", "quota", "unregister", "dangling", "泄漏", "泄露",
        "悬空", "释放后使用", "二次释放", "引用计数", "句柄", "耗尽", "未释放", "未注销",
    ),
    "error_rollback": (
        "rollback", "error path", "early return", "goto", "partial", "cleanup", "errno",
        "swallow", "override", "recover", "retry", "pcall", "异常", "回滚", "错误处理",
        "错误码", "吞掉", "覆盖", "半成品", "残留", "恢复失败", "清理", "提前返回",
    ),
}

_NON_CODE_NOISE_PATTERNS = re.compile(
    r"(?:文案|错别字|翻译|图标|样式|颜色|配色|文档描述|手册|注释拼写|界面排版|typo|translation|css|ui\s*label)",
    re.IGNORECASE,
)


def dts_registry_path(data_root: str | Path) -> Path:
    return Path(data_root) / "dts-patterns" / "registry.json"


def _read_registry(data_root: str | Path) -> DTSPatternRegistry:
    path = dts_registry_path(data_root)
    if not path.is_file():
        return DTSPatternRegistry()
    return DTSPatternRegistry.model_validate(read_json(path))


def _write_registry(data_root: str | Path, registry: DTSPatternRegistry) -> Path:
    path = dts_registry_path(data_root)
    write_json(path, registry.model_dump(mode="json"))
    return path


def infer_dts_track(text: str) -> str | None:
    """Classify DTS prose into one of the three deep tracks, or None if noise."""
    lowered = text.lower()
    if _NON_CODE_NOISE_PATTERNS.search(lowered) and not any(
        kw in lowered for words in _TRACK_KEYWORDS.values() for kw in words
    ):
        return None
    scores = {
        track: sum(1 for kw in words if kw in lowered)
        for track, words in _TRACK_KEYWORDS.items()
    }
    best_track, best_score = max(scores.items(), key=lambda pair: pair[1])
    return best_track if best_score > 0 else None


def _git_diff_excerpt(repo_path: Path, commit_ref: str, *, max_chars: int = 2400) -> str:
    """Read-only commit diff lookup; always skips LFS smudge and never mutates repo."""
    if not commit_ref or not re.fullmatch(r"[0-9A-Fa-f]{7,40}", commit_ref.strip()):
        return ""
    if not (repo_path / ".git").exists():
        return ""
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
    try:
        proc = subprocess.run(
            ["git", "show", "--no-color", "--unified=2", "--stat", "--patch", commit_ref.strip()],
            cwd=str(repo_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0 or not proc.stdout.strip():
        return ""
    text = proc.stdout.strip()
    return text[:max_chars] + ("\n... [diff truncated]" if len(text) > max_chars else "")


def _split_diff_skeleton(diff_text: str) -> tuple[str, str]:
    removed, added = [], []
    for line in diff_text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed.append(line[1:].rstrip())
        elif line.startswith("+"):
            added.append(line[1:].rstrip())
    buggy = "\n".join(removed[:12]).strip()
    fixed = "\n".join(added[:12]).strip()
    return buggy, fixed


def _lookup_commit_diff(data_root: str | Path, commit_ref: str) -> tuple[str, str]:
    repos_root = Path(data_root) / "repositories"
    if not commit_ref or not repos_root.is_dir():
        return "", ""
    for repo_dir in sorted(repos_root.iterdir()):
        if not repo_dir.is_dir():
            continue
        excerpt = _git_diff_excerpt(repo_dir, commit_ref)
        if excerpt:
            return repo_dir.name, excerpt
    return "", ""


def import_dts_patterns(
    data_root: str | Path,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Import structured DTS topology cards; drop non-three-track noise and default to enabled."""
    registry = _read_registry(data_root)
    existing = {item.pattern_id: item for item in registry.patterns}
    imported, updated, filtered_out = [], [], []
    now = utc_now()

    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            filtered_out.append({"index": idx, "reason": "条目不是 JSON 对象"})
            continue
        track = raw.get("track")
        if track not in VALID_TRACKS:
            probe_text = " ".join(
                str(raw.get(k, ""))
                for k in ("title", "defect_mechanism", "buggy_skeleton", "root_cause", "symptom")
            )
            inferred = infer_dts_track(probe_text)
            if inferred is None:
                filtered_out.append({
                    "index": idx,
                    "pattern_id": raw.get("pattern_id") or raw.get("dts_id") or f"row-{idx}",
                    "reason": "非异常回滚/时序并发/资源生命周期三轨代码缺陷，已自动滤除",
                })
                continue
            raw = {**raw, "track": inferred}
        try:
            parsed = DTSPatternInput.model_validate(raw)
        except Exception as exc:
            filtered_out.append({
                "index": idx,
                "pattern_id": raw.get("pattern_id") or f"row-{idx}",
                "reason": f"缺少必要因果拓扑或免疫判据：{exc}",
            })
            continue
        prev = existing.get(parsed.pattern_id)
        if prev is None:
            card = DTSPatternCard(
                **parsed.model_dump(),
                status="enabled",
                created_at=now,
                updated_at=now,
            )
            existing[card.pattern_id] = card
            imported.append(card.pattern_id)
        else:
            card = DTSPatternCard(
                **parsed.model_dump(),
                status=prev.status,
                created_at=prev.created_at,
                updated_at=now,
            )
            existing[card.pattern_id] = card
            updated.append(card.pattern_id)

    registry = DTSPatternRegistry(
        patterns=sorted(existing.values(), key=lambda p: (p.track, p.pattern_id))
    )
    reg_path = _write_registry(data_root, registry)
    return {
        "registry_path": str(reg_path),
        "imported": imported,
        "updated": updated,
        "filtered_out": filtered_out,
        "total_enabled": sum(1 for p in registry.patterns if p.status == "enabled"),
        "total": len(registry.patterns),
    }


def _rows_from_offline_file(source: Path) -> list[dict[str, Any]]:
    suffix = source.suffix.lower()
    if suffix == ".json":
        payload = read_json(source)
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("patterns", "items", "records", "dts", "issues"):
                if isinstance(payload.get(key), list):
                    return [row for row in payload[key] if isinstance(row, dict)]
            return [payload]
        return []
    if suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    if suffix == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        rows: list[dict[str, Any]] = []
        try:
            sheet = wb.active
            iterator = sheet.iter_rows(values_only=True)
            headers = [str(cell or "").strip() for cell in next(iterator, ())]
            for values in iterator:
                if not any(values):
                    continue
                rows.append({
                    headers[i]: ("" if values[i] is None else str(values[i]).strip())
                    for i in range(min(len(headers), len(values)))
                    if headers[i]
                })
        finally:
            wb.close()
        return rows
    if suffix in {".md", ".txt"}:
        text = source.read_text(encoding="utf-8", errors="replace")
        sections = [part.strip() for part in re.split(r"(?m)^#{1,3}\s+", text) if part.strip()]
        rows = []
        for idx, section in enumerate(sections, 1):
            lines = section.splitlines()
            title = lines[0].strip() if lines else f"{source.stem}-{idx}"
            body = "\n".join(lines[1:]).strip() or title
            commit_match = re.search(r"\b(?:commit|sha)[:\s]+([0-9a-f]{7,40})\b", body, re.IGNORECASE)
            dts_match = re.search(r"\b(DTS[0-9A-Za-z_-]{4,})\b", section)
            rows.append({
                "dts_id": dts_match.group(1) if dts_match else f"{source.stem}-{idx}",
                "title": title,
                "root_cause": body[:800],
                "commit_ref": commit_match.group(1) if commit_match else "",
            })
        return rows
    raise ValueError(f"不支持的 DTS 离线文件类型：{suffix or '<none>'}")


def _pick(row: dict[str, Any], *keys: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def ingest_dts_file(
    data_root: str | Path,
    source_path: str | Path,
    *,
    source_kind: str | None = None,
) -> dict[str, Any]:
    """Ingest offline DTS exports (.json/.csv/.xlsx/.md/.txt) and enrich with local git diff if available."""
    source = Path(source_path)
    if not source.is_file():
        raise ValueError(f"DTS 文件不存在：{source}")
    raw_rows = _rows_from_offline_file(source)
    prepared: list[dict[str, Any]] = []
    filtered_out: list[dict[str, Any]] = []

    for idx, row in enumerate(raw_rows):
        if row.get("format_version") == "dts-pattern-v1" or (
            row.get("pattern_id") and row.get("defect_mechanism") and row.get("already_fixed_check")
        ):
            prepared.append(row)
            continue
        dts_id = _pick(row, "dts_id", "id", "issue_id", "单号", "问题单号", "缺陷单号") or f"dts-{source.stem}-{idx+1:03d}"
        title = _pick(row, "title", "summary", "标题", "问题简述", "问题描述")
        symptom = _pick(row, "symptom", "现象", "问题现象", "description")
        root_cause = _pick(row, "root_cause", "根因", "根因分析", "原因分析", "analysis")
        fix_desc = _pick(row, "fix", "solution", "fixed_guard", "修改措施", "修复方案", "解决措施")
        module_hint = _pick(row, "module", "module_path", "component", "模块", "文件路径", "path")
        mr_url = _pick(row, "mr_url", "mr_link", "mr", "MR链接", "合入链接")
        commit_ref = _pick(row, "commit", "commit_ref", "commit_sha", "mr_commit", "提交号")
        explicit_track = _pick(row, "track", "轨道")
        combined = " ".join(part for part in (title, symptom, root_cause, fix_desc) if part)
        track = explicit_track if explicit_track in VALID_TRACKS else infer_dts_track(combined)
        if not title or track is None:
            filtered_out.append({
                "index": idx,
                "dts_id": dts_id,
                "reason": "缺少有效标题或不属于异常回滚/时序并发/资源生命周期三轨，已自动滤除",
            })
            continue
        repo_id, diff_excerpt = _lookup_commit_diff(data_root, commit_ref)
        buggy_diff, fixed_diff = _split_diff_skeleton(diff_excerpt)
        has_diff = bool(diff_excerpt)
        effective_source = source_kind or ("offline_file_with_git" if has_diff else "offline_file_text")
        pattern_id = re.sub(r"[^A-Za-z0-9._-]+", "-", dts_id).strip("-") or f"dts-{idx+1:03d}"
        prepared.append({
            "pattern_id": pattern_id,
            "title": title,
            "track": track,
            "dts_id": dts_id,
            "module_hints": [m.strip() for m in re.split(r"[,;\s]+", module_hint) if m.strip()],
            "context_triggers": [part for part in (symptom or title, module_hint) if part][:3],
            "buggy_skeleton": buggy_diff or root_cause or title,
            "fixed_guard": fixed_diff or fix_desc or "核对当前源码是否存在对应防御、回滚或同步保护",
            "defect_mechanism": root_cause or symptom or title,
            "already_fixed_check": (
                f"若当前源码已包含等价修复保护（{fixed_diff[:160] or fix_desc[:160] or '完整释放/回滚/加锁校验'}），"
                "则视为已修复免疫，仅横向排查相邻兄弟路径是否漏改"
            ),
            "repro_instrumentation_recipe": _pick(row, "repro", "repro_instrumentation_recipe", "复现方法", "打桩方案"),
            "has_diff_evidence": has_diff,
            "mr_url": mr_url or None,
            "commit_ref": f"{repo_id}:{commit_ref}" if repo_id and commit_ref else (commit_ref or None),
            "source_kind": effective_source,
            "source_refs": [f"{source.name}#row-{idx+1}"],
        })

    result = import_dts_patterns(data_root, prepared)
    result["filtered_out"] = [*filtered_out, *result["filtered_out"]]
    result["source_file"] = str(source)
    return result


def list_dts_patterns(
    data_root: str | Path,
    *,
    track: str | None = None,
    status: DTSPatternStatus | None = None,
    query: str | None = None,
    cursor: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    if track is not None and track not in VALID_TRACKS:
        raise ValueError(f"无效轨道：{track}")
    if cursor < 0 or not 1 <= limit <= 200:
        raise ValueError("cursor/limit 参数越界")
    items = _read_registry(data_root).patterns
    if track is not None:
        items = [item for item in items if item.track == track]
    if status is not None:
        items = [item for item in items if item.status == status]
    if query:
        needle = query.lower().strip()
        items = [
            item for item in items
            if needle in item.pattern_id.lower()
            or needle in item.title.lower()
            or needle in item.defect_mechanism.lower()
            or any(needle in hint.lower() for hint in item.module_hints)
        ]
    page = items[cursor : cursor + limit]
    return {
        "items": [item.model_dump(mode="json") for item in page],
        "total": len(items),
        "next_cursor": cursor + len(page) if cursor + len(page) < len(items) else None,
        "counts_by_track": {
            t: sum(1 for item in items if item.track == t and item.status == "enabled")
            for t in VALID_TRACKS
        },
    }


def get_dts_pattern(data_root: str | Path, pattern_id: str) -> dict[str, Any]:
    for item in _read_registry(data_root).patterns:
        if item.pattern_id == pattern_id:
            return item.model_dump(mode="json")
    raise ValueError(f"DTS 故障拓扑卡不存在：{pattern_id}")


def set_dts_pattern_status(
    data_root: str | Path,
    pattern_id: str,
    status: DTSPatternStatus,
) -> dict[str, Any]:
    registry = _read_registry(data_root)
    updated_card = None
    patterns = []
    for item in registry.patterns:
        if item.pattern_id == pattern_id:
            updated_card = item.model_copy(update={"status": status, "updated_at": utc_now()})
            patterns.append(updated_card)
        else:
            patterns.append(item)
    if updated_card is None:
        raise ValueError(f"DTS 故障拓扑卡不存在：{pattern_id}")
    _write_registry(data_root, DTSPatternRegistry(patterns=patterns))
    return updated_card.model_dump(mode="json")


def freeze_dts_context(
    data_root: str | Path,
    run_dir: Path,
    *,
    run_id: str,
    scope_paths: list[str],
    requested_dts_ids: list[str] | None = None,
    homologous_items: list[dict[str, Any]] | None = None,
    mcp_status: dict[str, Any] | None = None,
    max_patterns: int = 24,
) -> dict[str, Any]:
    """Freeze matched enabled DTS topology cards, explicit user DTS IDs, and homologous DTS into inputs/dts-context.json."""
    target = run_dir / "inputs" / "dts-context.json"
    if target.is_file():
        return read_json(target)

    clean_dts_ids = [
        str(item).strip()
        for item in (requested_dts_ids or [])
        if isinstance(item, str) and item.strip()
    ]
    scope_tokens = {
        tok.lower()
        for path in scope_paths
        for tok in re.split(r"[/\\._-]+", path)
        if len(tok) >= 3
    }
    enabled_cards = [card for card in _read_registry(data_root).patterns if card.status == "enabled"]

    def score(card: DTSPatternCard) -> tuple[int, int, str]:
        hint_hits = sum(
            1 for hint in card.module_hints
            if any(hint.lower() in path.lower() or hint.lower() in scope_tokens for path in scope_paths)
        )
        return (-hint_hits, -int(card.has_diff_evidence), card.pattern_id)

    matched = sorted(enabled_cards, key=score)[:max_patterns]
    homologous: list[FrozenHomologousDTS] = []
    warnings: list[str] = []
    for item in homologous_items or []:
        try:
            homologous.append(FrozenHomologousDTS.model_validate(item))
        except Exception as exc:
            warnings.append(f"忽略格式不完整的同源 DTS 条目：{exc}")

    body_for_hash = {
        "requested_dts_ids": clean_dts_ids,
        "homologous_dts": [item.model_dump(mode="json") for item in homologous],
        "matched_patterns": [card.model_dump(mode="json") for card in matched],
    }
    digest = hashlib.sha256(
        json.dumps(body_for_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    snapshot = DTSContextSnapshot(
        run_id=run_id,
        frozen_at=utc_now(),
        content_sha256=digest,
        requested_dts_ids=clean_dts_ids,
        mcp_status=mcp_status or {
            "dts_mcp_detected": False,
            "codehub_mcp_detected": False,
            "mode": "offline_registry",
        },
        homologous_dts=homologous,
        matched_patterns=matched,
        warnings=warnings,
    )
    payload = snapshot.model_dump(mode="json")
    write_json(target, payload)
    return payload
