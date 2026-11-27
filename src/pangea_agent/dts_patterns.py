"""DTS real-case feedback registry, offline/Git ingestion, and per-run freezing."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import threading
from contextlib import contextmanager
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

VALID_TRACKS = ("error_rollback", "concurrency_timing", "resource_lifecycle", "memory_corruption")

_TRACK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "memory_corruption": (
        "踩内存", "内存踩踏", "内存破坏", "踩坏", "越界", "溢出", "野指针", "悬空", "释放后使用",
        "释放后访问", "释放后写", "已释放", "重分配", "槽位", "共享内存", "未置空", "未初始化",
        "空指针", "段错误", "asan", "addresssanitizer", "segfault", "sigsegv", "null pointer",
        "nullptr", "coredump", "core dump",
        "uaf", "use-after-free", "use after free", "dangling", "stale pointer", "stale handle",
        "wild pointer", "stomp", "slot", " = null", "memory corruption", "heap corruption", "stack corruption",
        "out-of-bounds", "out of bounds", "oob", "buffer overflow", "heap-buffer-overflow",
        "memcpy", "memmove", "memset", "strcpy", "sprintf", "uninitialized",
    ),
    "concurrency_timing": (
        "race", "deadlock", "lock", "mutex", "spinlock", "atomic", "yield", "coroutine",
        "timer", "callback", "reentr", "async", "cancel", "并发", "竞态", "死锁", "时序",
        "重入", "回调", "超时", "乱序", "挂起", "解锁", "持锁",
    ),
    "resource_lifecycle": (
        "lsan", "leaksanitizer", "direct leak", "indirect leak", "memory leak", "valgrind",
        "leak", "double free", "double-free", "refcount", "refcnt",
        "fd", "socket", "handle", "pool", "quota", "unregister", "malloc", "calloc", "strdup",
        "内存泄漏", "内存泄露", "泄漏", "泄露", "未free", "漏free",
        "二次释放", "重复释放", "引用计数", "句柄", "耗尽", "未释放", "未注销",
    ),
    "error_rollback": (
        "rollback", "error path", "early return", "goto", "partial", "cleanup", "errno",
        "swallow", "override", "recover", "retry", "pcall", "divide by zero", "division by zero",
        "infinite loop", "unchecked return", "除零", "死循环", "返回值未检查", "逻辑错误",
        "异常", "回滚", "错误处理", "错误码", "吞掉", "覆盖", "半成品", "残留", "恢复失败", "清理", "提前返回",
    ),
}

_NON_CODE_NOISE_PATTERNS = re.compile(
    r"(?:文案|错别字|翻译|图标|样式|颜色|配色|文档描述|手册|注释拼写|界面排版|typo|translation|css|ui\s*label)",
    re.IGNORECASE,
)

_DOC_ONLY_EXTS = (".md", ".markdown", ".txt", ".rst", ".adoc", ".html", ".css")
_REGISTRY_LOCKS: dict[str, threading.RLock] = {}
_REGISTRY_LOCKS_GUARD = threading.Lock()
_HELD_REGISTRY_LOCKS = threading.local()


def dts_registry_path(data_root: str | Path) -> Path:
    return Path(data_root) / "dts-patterns" / "registry.json"


@contextmanager
def _dts_registry_lock(data_root: str | Path):
    """Serialize DTS registry read-modify-write across processes and threads."""
    lock_path = dts_registry_path(data_root).parent / ".registry.lock"
    key = str(lock_path.resolve())
    with _REGISTRY_LOCKS_GUARD:
        process_lock = _REGISTRY_LOCKS.setdefault(key, threading.RLock())
    with process_lock:
        held = getattr(_HELD_REGISTRY_LOCKS, "paths", set())
        if key in held:
            yield
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            locked = False
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    handle.write(b"0")
                    handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
                _HELD_REGISTRY_LOCKS.paths = held | {key}
                yield
            finally:
                _HELD_REGISTRY_LOCKS.paths = held
                if locked:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_registry(data_root: str | Path) -> DTSPatternRegistry:
    path = dts_registry_path(data_root)
    if not path.is_file():
        return DTSPatternRegistry()
    return DTSPatternRegistry.model_validate(read_json(path))


def _write_registry(data_root: str | Path, registry: DTSPatternRegistry) -> Path:
    path = dts_registry_path(data_root)
    write_json(path, registry.model_dump(mode="json"))
    return path


def _keyword_matches(keyword: str, lowered: str) -> bool:
    if keyword.isascii() and keyword[0].isalnum():
        if len(keyword) <= 3:
            return re.search(rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])", lowered) is not None
        return re.search(rf"(?<![a-z0-9_]){re.escape(keyword)}", lowered) is not None
    return keyword in lowered


def infer_dts_track(text: str) -> str | None:
    """Classify DTS prose into one of the four deep tracks, or None if noise."""
    lowered = text.lower()
    if _NON_CODE_NOISE_PATTERNS.search(lowered) and not any(
        _keyword_matches(kw, lowered) for words in _TRACK_KEYWORDS.values() for kw in words
    ):
        return None
    scores = {
        track: sum(1 for kw in words if _keyword_matches(kw, lowered))
        for track, words in _TRACK_KEYWORDS.items()
    }
    best_track, best_score = max(scores.items(), key=lambda pair: pair[1])
    return best_track if best_score > 0 else None


_IMPL_EXTS = (".c", ".cc", ".cpp", ".cxx", ".lua", ".py", ".sh")
_C_LIKE_EXTS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".js", ".mjs", ".ts", ".tsx", ".go", ".rs", ".java")
_C_PREPROCESSOR_RE = re.compile(
    r"^#\s*(?:include|define|undef|if|ifdef|ifndef|elif|else|endif|pragma|error|line|warning)\b"
)


def _normalize_diff_path(raw: str) -> str:
    token = raw.strip().split("\t", 1)[0].strip()
    if not token or token == "/dev/null":
        return ""
    if token.startswith(("a/", "b/")):
        return token[2:]
    return token


def _parse_diff_sections(raw_diff: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse both git diffs and plain unified diffs into per-file sections and per-hunk blocks."""
    lines = raw_diff.splitlines()
    header_lines: list[str] = []
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_hunk: dict[str, Any] | None = None

    def _start_section(first_line: str) -> dict[str, Any]:
        sec: dict[str, Any] = {
            "lines": [first_line],
            "preamble_lines": [first_line],
            "hunks": [],
            "paths": [],
            "removed": [],
            "added": [],
            "has_git_header": first_line.startswith("diff --git "),
            "seen_hunk": False,
        }
        sections.append(sec)
        return sec

    def _add_path(sec: dict[str, Any], raw_path: str) -> None:
        cleaned = _normalize_diff_path(raw_path)
        if cleaned and cleaned not in sec["paths"]:
            sec["paths"].append(cleaned)

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            current = _start_section(line)
            current_hunk = None
            match = re.match(r"^diff --git\s+(\S+)\s+(\S+)", line)
            if match:
                _add_path(current, match.group(1))
                _add_path(current, match.group(2))
            i += 1
            continue
        if (
            line.startswith("--- ")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("+++ ")
            and (current is None or current["seen_hunk"] or not current["has_git_header"])
        ):
            current = _start_section(line)
            current_hunk = None
            _add_path(current, line[4:])
            i += 1
            continue
        if current is None:
            header_lines.append(line)
            i += 1
            continue
        current["lines"].append(line)
        if line.startswith("@@"):
            current["seen_hunk"] = True
            current_hunk = {"header": line, "lines": [line], "removed": [], "added": []}
            current["hunks"].append(current_hunk)
        elif current_hunk is None:
            current["preamble_lines"].append(line)
            if line.startswith("--- "):
                _add_path(current, line[4:])
            elif line.startswith("+++ "):
                _add_path(current, line[4:])
        else:
            current_hunk["lines"].append(line)
            if line.startswith("-") and not line.startswith("--- "):
                content = line[1:].rstrip()
                if content.strip():
                    current["removed"].append(content)
                    current_hunk["removed"].append(content)
            elif line.startswith("+") and not line.startswith("+++ "):
                content = line[1:].rstrip()
                if content.strip():
                    current["added"].append(content)
                    current_hunk["added"].append(content)
        i += 1

    for sec in sections:
        paths = sec["paths"]
        sec["text"] = "\n".join(sec["lines"]).rstrip()
        sec["preamble"] = "\n".join(sec["preamble_lines"]).rstrip()
        for hunk in sec["hunks"]:
            hunk["text"] = "\n".join(hunk["lines"]).rstrip()
        sec["label"] = paths[-1] if paths else ""
        sec["is_impl"] = any(p.lower().endswith(_IMPL_EXTS) for p in paths)
        sec["is_doc_only"] = bool(paths) and all(p.lower().endswith(_DOC_ONLY_EXTS) for p in paths)

    return "\n".join(header_lines).strip(), sections


def _is_doc_only_diff(diff_text: str) -> bool:
    if not diff_text:
        return False
    _, sections = _parse_diff_sections(diff_text)
    if not sections:
        return False
    return all(sec["is_doc_only"] for sec in sections)


def _is_comment_line(line: str, paths: list[str]) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith(("//", "/*", "*/", "--")):
        return True
    if stripped == "*" or stripped.startswith("* "):
        return True
    if stripped.startswith("#"):
        is_c_like = any(p.lower().endswith(_C_LIKE_EXTS) for p in paths)
        if is_c_like or _C_PREPROCESSOR_RE.match(stripped):
            return False
        return True
    return False


def _strip_inline_comment(line: str, paths: list[str]) -> str:
    if _is_comment_line(line, paths):
        return ""
    stripped = line.strip()
    if any(p.lower().endswith(_C_LIKE_EXTS) for p in paths):
        stripped = re.split(r"(?<!:)//", stripped, maxsplit=1)[0].rstrip()
    elif any(p.lower().endswith(".lua") for p in paths):
        stripped = stripped.split("--", 1)[0].rstrip()
    elif any(p.lower().endswith((".py", ".sh")) for p in paths):
        stripped = re.split(r"\s+#", stripped, maxsplit=1)[0].rstrip()
    return stripped


def _is_comment_only_diff(diff_text: str) -> bool:
    if not diff_text:
        return False
    _, sections = _parse_diff_sections(diff_text)
    code_sections = [sec for sec in sections if not sec["is_doc_only"]]
    if not code_sections:
        return False
    has_any_change = False
    for sec in code_sections:
        paths = sec["paths"]
        if not sec["removed"] and not sec["added"]:
            continue
        has_any_change = True
        rem_code = [c for c in (_strip_inline_comment(line, paths) for line in sec["removed"]) if c]
        add_code = [c for c in (_strip_inline_comment(line, paths) for line in sec["added"]) if c]
        if rem_code != add_code:
            return False
    return has_any_change


def _git_diff_raw(repo_path: Path, commit_ref: str) -> str:
    """Read-only commit diff lookup; always skips LFS smudge and never mutates repo."""
    if not commit_ref or not re.fullmatch(r"[0-9A-Fa-f]{7,40}", commit_ref.strip()):
        return ""
    if not (repo_path / ".git").exists():
        return ""
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
    try:
        proc = subprocess.run(
            ["git", "show", "--no-color", "--unified=3", "--stat", "--patch", commit_ref.strip()],
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
    return proc.stdout.strip()


def _allocate_text_blocks(blocks: list[str], budget: int, notice: str) -> list[str]:
    """Allocate character budget fairly across blocks so short blocks are preserved verbatim."""
    if not blocks or budget <= 0:
        return []
    allocated: dict[int, str] = {}
    remaining_indices = list(range(len(blocks)))
    remaining_budget = budget

    while remaining_indices and remaining_budget > 0:
        share = max(1, remaining_budget // len(remaining_indices))
        fitted = [idx for idx in remaining_indices if len(blocks[idx]) + 1 <= share]
        if not fitted:
            break
        for idx in fitted:
            allocated[idx] = blocks[idx]
            remaining_budget -= len(blocks[idx]) + 1
        remaining_indices = [idx for idx in remaining_indices if idx not in allocated]

    if remaining_indices and remaining_budget > 0:
        share = remaining_budget // len(remaining_indices)
        for idx in remaining_indices:
            if share <= len(notice) + 16:
                break
            cap = max(16, share - len(notice) - 1)
            piece = blocks[idx][:cap].rstrip() + notice
            allocated[idx] = piece
            remaining_budget -= len(piece) + 1

    return [allocated[idx] for idx in range(len(blocks)) if idx in allocated]


def _compress_section_by_hunks(section: dict[str, Any], budget: int) -> str:
    file_notice = "\n... [file diff truncated]"
    hunks = section.get("hunks") or []
    preamble = section.get("preamble") or ""
    if len(hunks) > 1 and budget > len(preamble) + 96:
        hunk_budget = budget - (len(preamble) + 1 if preamble else 0)
        pieces = _allocate_text_blocks(
            [h["text"] for h in hunks],
            hunk_budget,
            "\n... [hunk truncated]",
        )
        if pieces:
            body = "\n".join(pieces)
            return f"{preamble}\n{body}".strip() if preamble else body
    cap = max(16, budget - len(file_notice) - 1)
    return section["text"][:cap].rstrip() + file_notice


def _allocate_section_excerpts(
    sections: list[dict[str, Any]],
    budget: int,
) -> tuple[list[str], int]:
    """Allocate character budget across sections fairly (and across hunks within oversized files)."""
    if not sections or budget <= 0:
        return [], 0
    indexed = list(enumerate(sections))
    allocated: dict[int, str] = {}
    remaining_indices = [idx for idx, _ in indexed]
    remaining_budget = budget

    while remaining_indices and remaining_budget > 0:
        share = max(1, remaining_budget // len(remaining_indices))
        fitted = [
            idx for idx in remaining_indices
            if len(sections[idx]["text"]) + 1 <= share
        ]
        if not fitted:
            break
        for idx in fitted:
            allocated[idx] = sections[idx]["text"]
            remaining_budget -= len(sections[idx]["text"]) + 1
        remaining_indices = [idx for idx in remaining_indices if idx not in allocated]

    if remaining_indices and remaining_budget > 0:
        share = remaining_budget // len(remaining_indices)
        for idx in remaining_indices:
            if share <= 48:
                break
            piece = _compress_section_by_hunks(sections[idx], share)
            allocated[idx] = piece
            remaining_budget -= len(piece) + 1

    ordered_chunks = [allocated[idx] for idx, _ in indexed if idx in allocated]
    used = sum(len(c) + 1 for c in ordered_chunks)
    return ordered_chunks, used


def _extract_diff_excerpt(raw_diff: str, *, max_chars: int = 8000) -> tuple[str, bool]:
    """Compress long diffs while preserving representative hunks across implementation files."""
    text = raw_diff.strip()
    if len(text) <= max_chars:
        return text, False
    notice = f"\n... [diff truncated: original {len(text)} chars, representative hunks per file preserved]"
    budget = max(256, max_chars - len(notice))
    header, sections = _parse_diff_sections(text)
    if not sections:
        return text[:budget] + notice, True

    impl_sections = [s for s in sections if s["is_impl"]]
    other_code_sections = [s for s in sections if not s["is_impl"] and not s["is_doc_only"]]
    doc_sections = [s for s in sections if s["is_doc_only"]]

    chunks: list[str] = []
    used = 0
    if header:
        header_cap = min(len(header), 800, budget // 6)
        clipped_header = header[:header_cap].rstrip()
        if clipped_header:
            chunks.append(clipped_header)
            used += len(clipped_header) + 1

    for group in (impl_sections, other_code_sections, doc_sections):
        if not group:
            continue
        remaining = budget - used
        if remaining <= 64:
            break
        group_chunks, group_used = _allocate_section_excerpts(group, remaining)
        chunks.extend(group_chunks)
        used += group_used

    return "\n".join(chunks).rstrip() + notice, True


def _git_diff_excerpt(repo_path: Path, commit_ref: str, *, max_chars: int = 8000) -> str:
    raw = _git_diff_raw(repo_path, commit_ref)
    if not raw:
        return ""
    excerpt, _ = _extract_diff_excerpt(raw, max_chars=max_chars)
    return excerpt


def _select_balanced_lines(
    groups: list[tuple[str, list[str]]],
    max_lines: int,
) -> tuple[list[tuple[str, str]], bool]:
    """Select lines across files/hunks with fair per-group quotas so later hunks are never starved."""
    non_empty = [(label, lines) for label, lines in groups if lines]
    total_lines = sum(len(lines) for _, lines in non_empty)
    if total_lines <= max_lines:
        return [(label, line) for label, lines in non_empty for line in lines], False

    quotas: dict[int, int] = {}
    remaining_indices = list(range(len(non_empty)))
    remaining_budget = max_lines

    while remaining_indices and remaining_budget > 0:
        share = max(1, remaining_budget // len(remaining_indices))
        fitted = [idx for idx in remaining_indices if len(non_empty[idx][1]) <= share]
        if not fitted:
            break
        for idx in fitted:
            quotas[idx] = len(non_empty[idx][1])
            remaining_budget -= quotas[idx]
        remaining_indices = [idx for idx in remaining_indices if idx not in quotas]

    if remaining_indices and remaining_budget > 0:
        base = remaining_budget // len(remaining_indices)
        extra = remaining_budget % len(remaining_indices)
        for pos, idx in enumerate(remaining_indices):
            quotas[idx] = base + (1 if pos < extra else 0)

    selected: list[tuple[str, str]] = []
    for idx, (label, lines) in enumerate(non_empty):
        take = quotas.get(idx, 0)
        for line in lines[:take]:
            selected.append((label, line))
    return selected, True


def _split_diff_skeleton_details(diff_text: str, *, max_lines: int = 40) -> dict[str, Any]:
    """Extract balanced removed/added lines per file and per hunk, prioritizing implementation files."""
    _, sections = _parse_diff_sections(diff_text)
    if not sections:
        return {"removed": "", "added": "", "added_summary": "", "truncated": False}

    impl_sections = [s for s in sections if s["is_impl"]]
    other_code_sections = [s for s in sections if not s["is_impl"] and not s["is_doc_only"]]
    doc_sections = [s for s in sections if s["is_doc_only"]]
    ordered = [*impl_sections, *other_code_sections] or doc_sections

    removed_groups: list[tuple[str, list[str]]] = []
    added_groups: list[tuple[str, list[str]]] = []
    for sec in ordered:
        hunks = sec.get("hunks") or []
        if len(hunks) > 1:
            for h_idx, hunk in enumerate(hunks, 1):
                tag = f"{Path(sec['label']).name or sec['label']}#h{h_idx}"
                if hunk["removed"]:
                    removed_groups.append((tag, hunk["removed"]))
                if hunk["added"]:
                    added_groups.append((tag, hunk["added"]))
        else:
            tag = Path(sec["label"]).name if sec["label"] else ""
            if sec["removed"]:
                removed_groups.append((tag, sec["removed"]))
            if sec["added"]:
                added_groups.append((tag, sec["added"]))

    selected_removed, rem_trunc = _select_balanced_lines(removed_groups, max_lines)
    selected_added, add_trunc = _select_balanced_lines(added_groups, max_lines)

    # Build a compact multi-file/multi-hunk summary (<= 200 chars) so every representative hunk appears in already_fixed_check.
    added_by_group: dict[str, list[str]] = {}
    for label, line in selected_added:
        added_by_group.setdefault(label, []).append(line.strip())
    if len(added_by_group) <= 1:
        summary_text = "\n".join(line for _, line in selected_added).strip()[:200]
    else:
        summary_parts: list[str] = []
        per_group = max(36, 190 // len(added_by_group))
        for label, flines in added_by_group.items():
            prefix = f"{label}: " if label else ""
            snippet = " ".join(flines)[: max(16, per_group - len(prefix))].strip()
            summary_parts.append(f"{prefix}{snippet}")
        summary_text = " | ".join(summary_parts)[:200]

    return {
        "removed": "\n".join(line for _, line in selected_removed).strip(),
        "added": "\n".join(line for _, line in selected_added).strip(),
        "added_summary": summary_text,
        "truncated": rem_trunc or add_trunc,
    }


def _split_diff_skeleton(diff_text: str) -> tuple[str, str]:
    """Extract removed/added code lines, prioritizing implementation files over headers/comments."""
    details = _split_diff_skeleton_details(diff_text)
    return details["removed"], details["added"]


def _extract_commit_sha(raw: str) -> str:
    text = str(raw or "").strip()
    if re.fullmatch(r"[0-9A-Fa-f]{7,40}", text):
        return text
    url_match = re.search(r"/(?:commit|commits)/([0-9A-Fa-f]{7,40})\b", text)
    return url_match.group(1) if url_match else ""


def _lookup_commit_diff(data_root: str | Path, commit_ref: str) -> tuple[str, str]:
    sha = _extract_commit_sha(commit_ref)
    repos_root = Path(data_root) / "repositories"
    if not sha or not repos_root.is_dir():
        return "", ""
    for repo_dir in sorted(repos_root.iterdir()):
        if not repo_dir.is_dir():
            continue
        raw_diff = _git_diff_raw(repo_dir, sha)
        if raw_diff:
            return repo_dir.name, raw_diff
    return "", ""


_CARD_OUTPUT_FIELDS = ("format_version", "status", "created_at", "updated_at")
_DTS_ID_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(DTS(?:[0-9]{4,}[0-9A-Za-z]*|(?:[-_][A-Za-z][A-Za-z0-9]*)?[-_][0-9]+))(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def _normalize_pattern_import_row(raw: dict[str, Any]) -> tuple[dict[str, Any], DTSPatternStatus | None]:
    """Convert exported dts-pattern-v1 card dicts back into DTSPatternInput payload + optional status."""
    if "format_version" in raw and raw.get("format_version") != "dts-pattern-v1":
        raise ValueError(f"不支持的标准 DTS 卡版本：{raw.get('format_version')}")
    if raw.get("format_version") == "dts-pattern-v1" and "created_at" in raw and "updated_at" in raw:
        card = DTSPatternCard.model_validate(raw)
        return card.model_dump(exclude=set(_CARD_OUTPUT_FIELDS)), card.status
    if any(field in raw for field in _CARD_OUTPUT_FIELDS):
        raw_status = raw.get("status")
        if raw_status is not None and raw_status not in ("enabled", "disabled"):
            raise ValueError(f"无效 status：{raw_status}")
        cleaned = {k: v for k, v in raw.items() if k not in _CARD_OUTPUT_FIELDS}
        return cleaned, raw_status
    return raw, None


def import_dts_patterns(
    data_root: str | Path,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Import structured DTS topology cards; drop non-four-track noise and default to enabled."""
    with _dts_registry_lock(data_root):
        registry = _read_registry(data_root)
        existing = {item.pattern_id: item for item in registry.patterns}
        imported, updated, filtered_out = [], [], []
        now = utc_now()

        for idx, raw in enumerate(items):
            if not isinstance(raw, dict):
                filtered_out.append({"index": idx, "reason": "条目不是 JSON 对象"})
                continue
            try:
                normalized_raw, imported_status = _normalize_pattern_import_row(raw)
            except Exception as exc:
                filtered_out.append({
                    "index": idx,
                    "pattern_id": raw.get("pattern_id") or f"row-{idx}",
                    "reason": f"标准 DTS 卡格式无效：{exc}",
                })
                continue
            track = normalized_raw.get("track")
            if track not in VALID_TRACKS:
                probe_text = " ".join(
                    str(normalized_raw.get(k, ""))
                    for k in ("title", "defect_mechanism", "buggy_skeleton", "root_cause", "symptom")
                )
                inferred = infer_dts_track(probe_text)
                if inferred is None:
                    filtered_out.append({
                        "index": idx,
                        "pattern_id": normalized_raw.get("pattern_id") or normalized_raw.get("dts_id") or f"row-{idx}",
                        "reason": "非异常回滚/时序并发/资源生命周期/踩内存悬空访问四轨代码缺陷，已自动滤除",
                    })
                    continue
                normalized_raw = {**normalized_raw, "track": inferred}
            try:
                parsed = DTSPatternInput.model_validate(normalized_raw)
            except Exception as exc:
                filtered_out.append({
                    "index": idx,
                    "pattern_id": normalized_raw.get("pattern_id") or f"row-{idx}",
                    "reason": f"缺少必要因果拓扑或免疫判据：{exc}",
                })
                continue
            prev = existing.get(parsed.pattern_id)
            if prev is None:
                card = DTSPatternCard(
                    **parsed.model_dump(),
                    status=imported_status or "enabled",
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


def _extract_json_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        # Unwrap standard CLI success envelopes (e.g. `pangea dts-patterns get/list ... > card.json`).
        if payload.get("ok") is True and "result" in payload:
            return _extract_json_rows(payload["result"])
        for key in ("patterns", "items", "records", "dts", "issues"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    return []


def _rows_from_offline_file(source: Path) -> list[dict[str, Any]]:
    suffix = source.suffix.lower()
    if suffix == ".json":
        return _extract_json_rows(read_json(source))
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
    if suffix in {".diff", ".patch"}:
        text = source.read_text(encoding="utf-8", errors="replace")
        header, sections = _parse_diff_sections(text)
        dts_match = _DTS_ID_TOKEN_RE.search(f"{header}\n{source.stem}")
        subj_match = re.search(r"(?m)^Subject:\s*(?:\[[^\]]*\]\s*)?(.+)$", header) or re.search(
            r"(?m)^    ([^\s].+)$", header
        )
        code_paths: list[str] = []
        doc_paths: list[str] = []
        for sec in sections:
            target_list = doc_paths if sec["is_doc_only"] else code_paths
            for p in sec["paths"]:
                if p not in target_list:
                    target_list.append(p)
        mod_paths = (code_paths or doc_paths)[:6]
        title = (subj_match.group(1).strip() if subj_match else "") or f"补丁特征 {source.stem}"
        return [{
            "dts_id": dts_match.group(1) if dts_match else source.stem,
            "title": title,
            "module": ",".join(mod_paths),
            "diff_excerpt": text,
        }]
    if suffix in {".md", ".txt"}:
        text = source.read_text(encoding="utf-8", errors="replace")
        sections = [part.strip() for part in re.split(r"(?m)^#{1,3}\s+", text) if part.strip()]
        rows = []
        for idx, section in enumerate(sections, 1):
            lines = section.splitlines()
            title = lines[0].strip() if lines else f"{source.stem}-{idx}"
            body = "\n".join(lines[1:]).strip() or title
            commit_match = re.search(r"\b(?:commit|sha)[:\s]+([0-9a-f]{7,40})\b", body, re.IGNORECASE)
            dts_match = _DTS_ID_TOKEN_RE.search(section)
            rows.append({
                "dts_id": dts_match.group(1) if dts_match else f"{source.stem}-{idx}",
                "title": title,
                "root_cause": body[:1600],
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
    """Ingest offline DTS exports (.json/.csv/.xlsx/.md/.txt/.diff/.patch) and enrich with local git diff if available."""
    source = Path(source_path)
    if not source.is_file():
        raise ValueError(f"DTS 文件不存在：{source}")
    raw_rows = _rows_from_offline_file(source)
    prepared: list[dict[str, Any]] = []
    filtered_out: list[dict[str, Any]] = []

    for idx, row in enumerate(raw_rows):
        if "format_version" in row or (
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
        commit_ref = _pick(row, "commit", "commit_ref", "commit_sha", "mr_commit", "提交号") or _extract_commit_sha(mr_url)
        inline_diff = str(row.get("diff_excerpt") or "").strip()
        repo_id, git_diff = ("", inline_diff) if inline_diff else _lookup_commit_diff(data_root, commit_ref)
        raw_diff = inline_diff or git_diff
        skeleton = _split_diff_skeleton_details(raw_diff) if raw_diff else {
            "removed": "", "added": "", "added_summary": "", "truncated": False
        }
        buggy_diff = skeleton["removed"]
        fixed_diff = skeleton["added"]
        skeleton_truncated = bool(skeleton["truncated"])
        diff_excerpt, diff_truncated = _extract_diff_excerpt(raw_diff, max_chars=8000) if raw_diff else ("", False)
        explicit_track = _pick(row, "track", "轨道")
        combined = " ".join(part for part in (title, symptom, root_cause, fix_desc, buggy_diff, fixed_diff) if part)
        is_doc_only = _is_doc_only_diff(raw_diff)
        is_comment_only = _is_comment_only_diff(raw_diff)
        if explicit_track in VALID_TRACKS:
            track = explicit_track
        elif is_doc_only or is_comment_only:
            track = None
        else:
            is_non_code_noise = bool(_NON_CODE_NOISE_PATTERNS.search(combined.lower()))
            has_code_evidence = bool(diff_excerpt or mr_url or commit_ref)
            track = infer_dts_track(combined) or (
                "error_rollback" if has_code_evidence and not is_non_code_noise else None
            )
        if not title or track is None:
            filtered_out.append({
                "index": idx,
                "dts_id": dts_id,
                "reason": "缺少有效标题或属于非代码文案/纯注释杂单，已自动滤除",
            })
            continue
        has_diff = bool(diff_excerpt)
        effective_source = source_kind or ("offline_file_with_git" if has_diff else "offline_file_text")
        pattern_id = re.sub(r"[^A-Za-z0-9._-]+", "-", dts_id).strip("-") or f"dts-{idx+1:03d}"
        fixed_guard_text = fixed_diff or fix_desc or "核对当前源码是否存在对应防御、回滚、置空摘链或同步保护"
        source_refs = [f"{source.name}#row-{idx+1}"]
        truncation_notes: list[str] = []
        if diff_truncated:
            source_refs.append(f"diff-truncated:original-{len(raw_diff)}-chars")
            truncation_notes.append(f"原始补丁较长（{len(raw_diff)} 字符）已按文件均衡截断")
        if skeleton_truncated:
            source_refs.append("skeleton-truncated:max-40-lines")
            truncation_notes.append("代码骨架超过 40 行上限已按文件均衡抽取")
        truncation_suffix = ""
        if truncation_notes:
            note_body = "；".join(truncation_notes)
            fixed_guard_text = f"{fixed_guard_text}\n[注：{note_body}]"
            truncation_suffix = f"（注：{note_body}）"
        fixed_summary = skeleton["added_summary"] or fix_desc[:200] or "完整释放/回滚/置空摘链/加锁校验"
        prepared.append({
            "pattern_id": pattern_id,
            "title": title,
            "track": track,
            "dts_id": dts_id,
            "module_hints": [m.strip() for m in re.split(r"[,;\s]+", module_hint) if m.strip()],
            "context_triggers": [part for part in (symptom or title, module_hint) if part][:3],
            "buggy_skeleton": buggy_diff or root_cause or title,
            "fixed_guard": fixed_guard_text,
            "defect_mechanism": root_cause or symptom or title,
            "already_fixed_check": (
                f"若当前源码已包含等价修复保护（{fixed_summary}），"
                f"则视为已修复免疫，严禁重报老单，仅横向排查相邻兄弟命令/对象/回调路径是否漏改{truncation_suffix}"
            ),
            "repro_instrumentation_recipe": _pick(row, "repro", "repro_instrumentation_recipe", "复现方法", "打桩方案"),
            "has_diff_evidence": has_diff,
            "mr_url": mr_url or None,
            "commit_ref": f"{repo_id}:{commit_ref}" if repo_id and commit_ref else (commit_ref or None),
            "source_kind": effective_source,
            "source_refs": source_refs,
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
            or (item.dts_id and needle in item.dts_id.lower())
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
    with _dts_registry_lock(data_root):
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


def _extract_id_signals(text: str, clean_dts_ids: list[str]) -> tuple[list[str], list[str]]:
    matched_req_ids: list[str] = []
    for req_id in clean_dts_ids:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(req_id)}(?![A-Za-z0-9_])", text, re.IGNORECASE):
            if req_id not in matched_req_ids:
                matched_req_ids.append(req_id)
    req_upper = {req_id.upper() for req_id in clean_dts_ids}
    found_tokens: list[str] = []
    for m in _DTS_ID_TOKEN_RE.findall(text):
        if m.upper() not in {t.upper() for t in found_tokens}:
            found_tokens.append(m)
    conflicting = [tok for tok in found_tokens if tok.upper() not in req_upper]
    return matched_req_ids, conflicting


def _resolve_patch_dts_id(
    raw_diff: str,
    mr_link: str,
    clean_dts_ids: list[str],
    total_mr_urls: int,
    idx: int,
    warnings: list[str],
) -> str:
    """Deterministically bind a patch/MR using header/Subject first, then path/filename fallback (never diff body comments)."""
    fallback_id = f"MR-DIFF-{idx+1:02d}"
    header, _ = _parse_diff_sections(raw_diff)

    # Prefer explicit patch header / Subject metadata over filename slugs.
    for source_label, scope_text in (("标题", header), ("路径", mr_link)):
        if not scope_text:
            continue
        matched_req_ids, conflicting_dts = _extract_id_signals(scope_text, clean_dts_ids)
        if conflicting_dts:
            warnings.append(
                f"补丁/MR（{mr_link}）{source_label}声明单号（{', '.join(conflicting_dts)}）与指定单号"
                f"（{', '.join(clean_dts_ids) or '无'}）冲突，已作为未关联材料 {fallback_id} 保存"
            )
            return fallback_id
        if len(matched_req_ids) == 1:
            return matched_req_ids[0]
        if len(matched_req_ids) > 1:
            warnings.append(
                f"补丁/MR（{mr_link}）{source_label}同时提及多个指定单号（{', '.join(matched_req_ids)}），无法唯一归属，已作为未关联材料 {fallback_id} 保存"
            )
            return fallback_id

    if len(clean_dts_ids) == 1 and total_mr_urls == 1:
        return clean_dts_ids[0]
    if clean_dts_ids:
        warnings.append(
            f"补丁/MR（{mr_link}）标题或路径未唯一匹配指定单号（{', '.join(clean_dts_ids)}），已作为未关联材料 {fallback_id} 保存"
        )
    return fallback_id


def freeze_dts_context(
    data_root: str | Path,
    run_dir: Path,
    *,
    run_id: str,
    scope_paths: list[str],
    requested_dts_ids: list[str] | None = None,
    requested_mr_urls: list[str] | None = None,
    homologous_items: list[dict[str, Any]] | None = None,
    selected_defect_items: list[dict[str, Any]] | None = None,
    mcp_status: dict[str, Any] | None = None,
    max_patterns: int = 24,
) -> dict[str, Any]:
    """Freeze matched enabled DTS topology cards, explicit user DTS IDs/MR URLs, local commit/patch diffs, and selected defect assets into inputs/dts-context.json."""
    target = run_dir / "inputs" / "dts-context.json"
    if target.is_file():
        return read_json(target)

    clean_dts_ids = [
        str(item).strip()
        for item in (requested_dts_ids or [])
        if isinstance(item, str) and item.strip()
    ]
    clean_mr_urls = [
        str(item).strip()
        for item in (requested_mr_urls or [])
        if isinstance(item, str) and item.strip()
    ]
    scope_tokens = {
        tok.lower()
        for path in scope_paths
        for tok in re.split(r"[/\\._-]+", path)
        if len(tok) >= 3
    }
    all_cards = _read_registry(data_root).patterns
    warnings: list[str] = []

    by_exact: dict[str, list[DTSPatternCard]] = {}
    by_lower: dict[str, list[DTSPatternCard]] = {}
    for card in all_cards:
        keys = [card.pattern_id]
        if card.dts_id and card.dts_id != card.pattern_id:
            keys.append(card.dts_id)
        for key in keys:
            by_exact.setdefault(key, []).append(card)
            by_lower.setdefault(key.lower(), []).append(card)

    explicit_matched: list[DTSPatternCard] = []
    explicit_pattern_ids: set[str] = set()
    missing_requested_ids: list[str] = []
    for req_id in clean_dts_ids:
        candidates = by_exact.get(req_id) or by_lower.get(req_id.lower()) or []
        if not candidates:
            missing_requested_ids.append(req_id)
            continue
        enabled_hits = [card for card in candidates if card.status == "enabled"]
        if enabled_hits:
            for card in enabled_hits:
                if card.pattern_id not in explicit_pattern_ids:
                    explicit_matched.append(card)
                    explicit_pattern_ids.add(card.pattern_id)
        else:
            disabled_ids = ", ".join(card.pattern_id for card in candidates)
            warnings.append(f"显式指定的本地 DTS 拓扑卡 {req_id} 当前已禁用（{disabled_ids}），未自动启用")

    def hint_hits(card: DTSPatternCard) -> int:
        return sum(
            1 for hint in card.module_hints
            if any(hint.lower() in path.lower() or hint.lower() in scope_tokens for path in scope_paths)
        )

    def score(card: DTSPatternCard) -> tuple[int, int, int, str]:
        hits = hint_hits(card)
        return (-hits, -int(bool(card.module_hints)), -int(card.has_diff_evidence), card.pattern_id)

    generic_recommended: list[DTSPatternCard] = []
    if scope_paths:
        remaining_candidates = [
            card for card in all_cards
            if card.status == "enabled"
            and card.pattern_id not in explicit_pattern_ids
            and hint_hits(card) > 0
        ]
        generic_recommended = sorted(
            (
                card for card in all_cards
                if card.status == "enabled"
                and card.pattern_id not in explicit_pattern_ids
                and not card.module_hints
            ),
            key=score,
        )
    else:
        remaining_candidates = [
            card for card in all_cards
            if card.status == "enabled"
            and card.pattern_id not in explicit_pattern_ids
        ]
    remaining_budget = max(0, max_patterns - len(explicit_matched))
    matched = [*explicit_matched, *sorted(remaining_candidates, key=score)[:remaining_budget]]

    if generic_recommended:
        preview = "、".join(f"{c.pattern_id}（{c.title}）" for c in generic_recommended[:8])
        more = f" 等共 {len(generic_recommended)} 张" if len(generic_recommended) > 8 else ""
        warnings.append(f"存在无模块匹配依据的通用推荐卡（未计入已匹配模块卡）：{preview}{more}")

    homologous: list[FrozenHomologousDTS] = []
    seen_dts_ids: set[str] = set()
    seen_mr_sources: set[tuple[str, str, str]] = set()

    for item in homologous_items or []:
        try:
            parsed = FrozenHomologousDTS.model_validate(item)
            homologous.append(parsed)
            seen_dts_ids.add(parsed.dts_id)
            if parsed.mr_url or parsed.commit_ref:
                seen_mr_sources.add((parsed.dts_id, parsed.mr_url, parsed.commit_ref))
        except Exception as exc:
            warnings.append(f"忽略格式不完整的同源 DTS 条目：{exc}")

    # Deterministically extract local commit diffs or patch files when present in requested_mr_urls.
    for idx, entry in enumerate(clean_mr_urls):
        raw_diff = ""
        commit_ref = ""
        mr_link = entry
        candidate_path = Path(entry)
        if not entry.startswith(("http://", "https://")) and candidate_path.suffix.lower() in {".diff", ".patch"} and candidate_path.is_file():
            raw_diff = candidate_path.read_text(encoding="utf-8", errors="replace")
            mr_link = str(candidate_path)
        else:
            repo_id, git_diff = _lookup_commit_diff(data_root, entry)
            if git_diff:
                raw_diff = git_diff
                sha = _extract_commit_sha(entry)
                commit_ref = f"{repo_id}:{sha}" if repo_id else sha
        if raw_diff:
            paired_dts = _resolve_patch_dts_id(raw_diff, mr_link, clean_dts_ids, len(clean_mr_urls), idx, warnings)
            source_key = (paired_dts, mr_link, commit_ref)
            if source_key in seen_mr_sources:
                continue
            diff_excerpt, diff_truncated = _extract_diff_excerpt(raw_diff, max_chars=8000)
            skeleton = _split_diff_skeleton_details(raw_diff)
            buggy_diff = skeleton["removed"]
            fixed_diff = skeleton["added"]
            if diff_truncated or skeleton["truncated"]:
                warnings.append(
                    f"补丁/MR（{paired_dts}，来源 {mr_link}）原始长度为 {len(raw_diff)} 字符，已按实现文件均衡摘录并截断展示"
                )
            header, _ = _parse_diff_sections(raw_diff)
            subj_match = re.search(r"(?m)^Subject:\s*(?:\[[^\]]*\]\s*)?(.+)$", header) or re.search(
                r"(?m)^    ([^\s].+)$", header
            )
            patch_title = subj_match.group(1).strip() if subj_match else f"历史修复补丁特征 ({paired_dts})"
            inferred_track = (
                None
                if _is_doc_only_diff(raw_diff) or _is_comment_only_diff(raw_diff)
                else infer_dts_track(f"{patch_title} {buggy_diff} {fixed_diff}")
            )
            fixed_summary = skeleton["added_summary"] or "见 diff_excerpt 新增防御"
            homologous.append(FrozenHomologousDTS(
                dts_id=paired_dts,
                title=patch_title,
                track=inferred_track if inferred_track in VALID_TRACKS else None,
                root_cause=buggy_diff[:1200],
                mr_url=mr_link,
                commit_ref=commit_ref,
                diff_excerpt=diff_excerpt,
                already_fixed_check=(
                    f"若当前 6.1.x 源码已包含补丁保护（{fixed_summary}），"
                    "严禁重报该已修复点，必须横向深挖相邻兄弟路径、同类回调或同类资源的未修同源隐患"
                ),
                source_kind="offline_file_with_git",
            ))
            seen_dts_ids.add(paired_dts)
            seen_mr_sources.add(source_key)

    for req_id in missing_requested_ids:
        if req_id not in seen_dts_ids and req_id.lower() not in {s.lower() for s in seen_dts_ids}:
            warnings.append(f"显式指定的 DTS {req_id} 在本地故障拓扑库中未找到，离线模式下无对应本地证据")

    # Bridge user-selected approved historical_defect assets directly into homologous_dts.
    for defect in selected_defect_items or []:
        if not isinstance(defect, dict) or defect.get("item_type") != "historical_defect":
            continue
        cid = str(defect.get("candidate_id") or defect.get("item_id") or "").strip()
        title = str(defect.get("title") or "").strip()
        if not cid or not title or cid in seen_dts_ids:
            continue
        symptom = str(defect.get("symptom") or "").strip()
        root_cause = str(defect.get("root_cause") or "").strip()
        mechanism = str(defect.get("defect_mechanism") or "").strip()
        exclusions = "; ".join(str(x).strip() for x in (defect.get("exclusion_conditions") or []) if str(x).strip())
        refs = defect.get("source_references") or []
        mod_path = ", ".join(
            f"{r.get('path')}:{r.get('location')}"
            for r in refs if isinstance(r, dict) and r.get("path")
        )
        inferred = infer_dts_track(f"{title} {symptom} {root_cause} {mechanism}")
        homologous.append(FrozenHomologousDTS(
            dts_id=cid,
            title=title,
            track=inferred if inferred in VALID_TRACKS else None,
            module_path=mod_path,
            symptom=symptom,
            root_cause=f"{root_cause}\n缺陷机制：{mechanism}".strip() if mechanism else root_cause,
            already_fixed_check=(
                exclusions
                or "若当前源码在原故障点已具备针对该缺陷机制的完整防御保护，视为已修复免疫，横向排查同模块兄弟路径与同类对象"
            ),
            source_kind="historical_defect_asset",
        ))
        seen_dts_ids.add(cid)

    body_for_hash = {
        "requested_dts_ids": clean_dts_ids,
        "requested_mr_urls": clean_mr_urls,
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
        requested_mr_urls=clean_mr_urls,
        mcp_status=mcp_status or {
            "dts_mcp_detected": False,
            "codehub_mcp_detected": False,
            "mode": "mcp_runtime_if_configured" if (clean_dts_ids or clean_mr_urls) else "offline_registry",
        },
        homologous_dts=homologous,
        matched_patterns=matched,
        warnings=warnings,
    )
    payload = snapshot.model_dump(mode="json")
    write_json(target, payload)
    return payload
