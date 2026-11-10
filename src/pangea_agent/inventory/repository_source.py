"""Opt-in repository-local investigation. Ownership remains in the initial index.

Only roots frozen by input preparation are used. Files returned as evidence are
copied once into the Run; no original repository is modified. Search continuation
checkpoints are immutable, task/query bound and resume without re-scanning prior
files. No model relevance or completeness decisions are made here.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from pangea_agent.agent_io import read_json
from pangea_agent.graph.result_store import _file_lock, compact_items_page

POLICY = "repository-local-v1"
MAX_BYTES = 8 * 1024 * 1024
SCAN_FILES = 100
SCAN_SECONDS = 2.0
SUPPLEMENTAL = "source-supplemental.json"


def enabled(task):
    return task.get("source_investigation_policy") == POLICY


def error(message):
    from .source_access import SourceAccessError
    return SourceAccessError(message)


def relative_path(value):
    value = str(value).replace("\\", "/")
    if (not value or "\x00" in value or PureWindowsPath(value).drive
            or value.startswith("/") or ".." in PurePosixPath(value).parts
            or ":" in value):
        raise error(f"源码路径必须是仓内相对路径：{value!r}")
    return str(PurePosixPath(value))


def inside(root, relative):
    path = (root / relative_path(relative)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise error(f"源码路径越出已选仓库：{relative}") from exc
    return path


def roots(run_dir, task):
    if not enabled(task):
        raise error("当前任务未启用仓内调查")
    payload = read_json(run_dir / "inputs" / "source-investigation.json")
    if payload.get("policy") != POLICY:
        raise error("仓内调查策略文件与任务不一致")
    result = {}
    for item in payload.get("repositories", []):
        repo = str(item["repo_id"])
        if relative_path(repo) != repo or "/" in repo or repo == ".":
            raise error("非法仓库标识")
        root = Path(item["source_root"])
        if not root.is_absolute() or root.resolve() != root:
            raise error(f"已选仓库根发生路径重定向：{repo}")
        result[repo] = root
    if not result:
        raise error("本次分析没有已选代码仓库")
    return result


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".source-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path, payload):
    atomic_bytes(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def saved_path(run_dir, repo, relative):
    base = (run_dir / "inputs" / "source").resolve()
    root = inside(base, repo)
    return inside(root, relative)


def materialize(run_dir, task, repo, relative, action_id):
    selected = roots(run_dir, task)
    if repo not in selected:
        raise error(f"仓库不在本次选择范围：{repo}")
    relative = relative_path(relative)
    target = saved_path(run_dir, repo, relative)
    # All writers use this OS-level lock, including independent CLI processes.
    with _file_lock(run_dir / "inputs" / ".source-supplemental.lock"):
        manifest_path = run_dir / "inputs" / SUPPLEMENTAL
        manifest = read_json(manifest_path) if manifest_path.exists() else {
            "format_version": "pangea-source-supplemental-v1", "files": []}
        entry = next((f for f in manifest["files"] if (f["repo_id"], f["path"]) == (repo, relative)), None)
        from .source_access import _allowed_paths
        initial = read_json(run_dir / "inputs" / "source-manifest.json")
        is_initial = (repo, relative) in _allowed_paths({}, initial)
        if is_initial and not target.is_file():
            raise error(f"初始冻结源码缺失，不允许以现场文件替换：{repo}:{relative}")
        if target.is_file():
            # Verify supplemental integrity, but retain initial snapshots as-is.
            if entry and hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                raise error(f"已保存补充源码损坏：{repo}:{relative}")
            if entry is None:
                if not is_initial:
                    # Recover a copy committed just before a process died while
                    # updating the manifest. Never fabricate a new source read.
                    content = target.read_bytes()
                    manifest["files"].append(record(repo, relative, content, action_id, recovered=True))
                    atomic_json(manifest_path, manifest)
            return target
        if entry:
            raise error(f"补充清单引用的源码文件缺失：{repo}:{relative}")
        source = inside(selected[repo], relative)
        if not source.is_file():
            raise error(f"仓内源码不存在或不是普通文件：{repo}:{relative}")
        # A bounded read avoids holding the cross-process lock for huge files.
        with source.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise error(f"源码超过读取上限 {MAX_BYTES} 字节：{repo}:{relative}")
        if b"\x00" in content:
            raise error(f"源码为二进制内容：{repo}:{relative}")
        atomic_bytes(target, content)
        manifest["files"].append(record(repo, relative, content, action_id))
        atomic_json(manifest_path, manifest)
    return target


def record(repo, relative, content, action_id, **extra):
    return {"repo_id": repo, "path": relative,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
            "action_id": action_id, **extra}


def supplemental_index(run_dir):
    """Read supplemental detail only for source tools, never for ownership."""
    from .on_demand import detail_path
    path = run_dir / "inputs" / SUPPLEMENTAL
    if not path.is_file():
        return []
    result = []
    for item in read_json(path).get("files", []):
        detail = detail_path(run_dir, item["repo_id"], relative_path(item["path"]))
        if detail.is_file():
            result.append(read_json(detail))
    return result


def list_index(binding, run_dir, task, repo_id, path, cursor, page_size, view, page_token, max_chars):
    """Shallow navigation; listing names does not snapshot or parse every file."""
    selected = roots(run_dir, task)
    if repo_id and repo_id not in selected:
        raise error(f"仓库不在本次选择范围：{repo_id}")
    failures, entries = [], []
    if path and not repo_id:
        raise error("按路径查询必须提供 repo_id")
    if repo_id:
        relative = relative_path(path or ".")
        source = inside(selected[repo_id], relative)
        saved = saved_path(run_dir, repo_id, relative)
        if saved.is_file() or source.is_file():
            materialize(run_dir, task, repo_id, relative, binding.action_id)
            from .on_demand import ensure_file_index, detail_path
            ensure_file_index(run_dir, repo_id, relative, run_dir / "inputs" / "source" / repo_id)
            detail = detail_path(run_dir, repo_id, relative)
            if not detail.is_file():
                # Discovery may skip vendor/build paths or unknown languages;
                # an explicit file query must still expose readable raw text.
                from .source_regions import _region_id
                count = len(saved_path(run_dir, repo_id, relative).read_text(encoding="utf-8", errors="replace").splitlines())
                regions = [{"region_id": _region_id(repo_id, relative, "raw", 1, count, None),
                            "repo_id": repo_id, "path": relative, "kind": "raw",
                            "line_start": 1, "line_end": count, "parse_complete": False,
                            "parsing_note": "此文件无结构解析结果，可直接读取原文"}] if count else []
                atomic_json(detail, {"repo_id": repo_id, "path": relative,
                                     "line_count": count, "regions": regions, "parse_complete": False})
            if detail.is_file():
                item = read_json(detail)
                entries = [{"item_type": "region", "value": r} for r in item["regions"]]
            else:
                entries = []
            mode = "regions"
        else:
            mode = "files"
            try:
                for child in sorted(source.iterdir(), key=lambda p: p.name):
                    rel = child.relative_to(selected[repo_id]).as_posix()
                    if child.name == ".git":
                        continue
                    try:
                        resolved = inside(selected[repo_id], rel)
                        kind = "directory" if resolved.is_dir() else "file"
                        entries.append({"item_type": kind, "value": {
                            "repo_id": repo_id, "path": rel, "entry_type": kind,
                            "scope_role": "reference"}})
                    except (OSError, ValueError) as exc:
                        failures.append({"repo_id": repo_id, "path": rel, "error": str(exc)})
            except OSError as exc:
                raise error(f"仓内目录不可读取：{repo_id}:{relative}: {exc}") from exc
    else:
        mode = "files"
        entries = [{"item_type": "directory", "value": {"repo_id": repo, "path": ".",
                    "entry_type": "directory", "scope_role": "reference"}} for repo in selected]
    metadata = {"format_version": "pangea-source-index-compact-v1" if view == "compact" else "pangea-source-index-v1",
                "binding": binding.model_dump(mode="json"), "page_mode": mode,
                "repo_id": repo_id, "path": path, "total_entries": len(entries),
                "failures": failures, "filters": {"excluded_directories": [".git"]},
                "navigation_only": True}
    if view == "compact":
        if cursor:
            raise error("compact source_index 使用 page_token")
        return compact_items_page(metadata=metadata, items=entries,
            token_context={"kind": "repository-index", "binding": binding.model_dump(mode="json"),
                           "repo_id": repo_id, "path": path}, page_token=page_token, max_chars=max_chars)
    from .source_access import _decode_cursor, _encode_cursor
    offset = _decode_cursor(cursor)
    page = entries[offset:offset + page_size]
    return {**metadata, "files": [item["value"] for item in page],
            "next_cursor": _encode_cursor(offset + page_size) if offset + page_size < len(entries) else None}


def search(binding, run_dir, task, query, repo_id, path, cursor, page_size, view, page_token, max_chars):
    if view == "compact" and (not isinstance(max_chars, int) or not 1000 <= max_chars <= 24000):
        raise error("compact max_chars 必须在 1000 到 24000 之间")
    selected = roots(run_dir, task)
    from .source_access import _allowed_paths
    initial_paths = _allowed_paths({}, read_json(run_dir / "inputs/source-manifest.json"))
    if repo_id and repo_id not in selected:
        raise error(f"仓库不在本次选择范围：{repo_id}")
    relative = relative_path(path or ".")
    if view == "compact" and cursor:
        raise error("compact source_search 使用 page_token")
    if view != "compact" and page_token:
        raise error("legacy source_search 使用 cursor")
    token = page_token if view == "compact" else cursor
    context = {"binding": binding.model_dump(mode="json"), "query": query, "repo_id": repo_id,
               "path": relative, "view": view, "page_size": page_size, "max_chars": max_chars}
    cache = run_dir / "inputs" / "source-search-pages"
    cache.mkdir(parents=True, exist_ok=True)
    if token:
        if not re_token(token):
            raise error("无效仓内搜索游标")
        checkpoint = read_json(cache / (token + ".json"))
        if checkpoint["context"] != context:
            raise error("搜索游标不属于当前任务、查询或分页参数")
        state = checkpoint["state"]
        if "batch" in state:
            batch = state.pop("batch")
            return compact_search_page(cache, context, state, batch["metadata"], batch["items"], batch["token"], max_chars)
    else:
        state = {"pending": [[r, relative, 0] for r in reversed(list(selected)) if not repo_id or r == repo_id],
                 "visited": [], "scanned_files": 0, "returned_hits": 0}
    hits, failures, skipped = [], [], []
    start = time.monotonic()
    scanned = 0
    budget = 0
    limit = min(page_size, max(1, (max_chars - 1500) // 700)) if view == "compact" else page_size
    while state["pending"] and len(hits) < limit and scanned < SCAN_FILES and time.monotonic() - start < SCAN_SECONDS:
        repo, rel, line_offset = state["pending"].pop()
        root = selected[repo]
        try:
            live = inside(root, rel)
            frozen = saved_path(run_dir, repo, rel)
            if (repo, rel) in initial_paths and not frozen.is_file():
                raise error("初始冻结源码缺失，不允许以现场文件替换")
            current = frozen if frozen.is_file() else live
            if current.is_dir():
                canonical = f"{repo}:{live}"
                if canonical in state["visited"]:
                    skipped.append({"repo_id": repo, "path": rel, "reason": "directory_already_visited"})
                    continue
                state["visited"].append(canonical)
                names = set()
                # Saved directories contain only evidence already used, not
                # the complete repository. Merge names without replacing saved
                # file contents with live contents.
                for directory in (live, frozen):
                    if directory.is_dir():
                        names.update(child.name for child in directory.iterdir())
                for name in sorted(names, reverse=True):
                    if name == ".git":
                        continue
                    child_rel = (PurePosixPath(rel) / name).as_posix()
                    state["pending"].append([repo, child_rel, 0])
                continue
            if not current.is_file():
                raise error("文件不存在或不是普通文件")
            scanned += 1
            state["scanned_files"] += int(line_offset == 0)
            if current.stat().st_size > MAX_BYTES:
                skipped.append({"repo_id": repo, "path": rel, "reason": "size_limit"})
                continue
            content = current.read_bytes()
            if b"\x00" in content:
                skipped.append({"repo_id": repo, "path": rel, "reason": "binary"})
                continue
            lines = content.decode("utf-8", errors="replace").splitlines()
            # Only matching files are saved; names and non-matches are not evidence.
            if not any(query in line for line in lines[line_offset:]):
                continue
            saved = materialize(run_dir, task, repo, rel, binding.action_id)
            lines = saved.read_text(encoding="utf-8", errors="replace").splitlines()
            for i in range(line_offset, len(lines)):
                line = lines[i]
                if query not in line:
                    continue
                preview = line[:240]
                value = {"repo_id": repo, "path": rel, "line": i + 1, "text": preview,
                         "text_truncated": len(line) > 240,
                         "evidence_handle": f"{repo}:{rel}:{i + 1}-{i + 1}", "region_ids": []}
                hits.append(value)
                budget += len(json.dumps(value, ensure_ascii=False))
                if len(hits) >= limit or (view == "compact" and budget >= max_chars - 1800):
                    if i + 1 < len(lines):
                        state["pending"].append([repo, rel, i + 1])
                    break
        except (OSError, ValueError) as exc:
            failures.append({"repo_id": repo, "path": rel, "error": str(exc)})
        if len(failures) + len(skipped) >= 30:
            break
    state["returned_hits"] += len(hits)
    complete = not state["pending"]
    result = {"format_version": "pangea-source-search-compact-v1" if view == "compact" else "pangea-source-search-v1",
              "binding": binding.model_dump(mode="json"), "query": query,
              "searched_scope": [{"repo_id": r, "path": relative} for r in selected if not repo_id or r == repo_id],
              "filters": {"excluded_directories": [".git"], "binary": "skipped", "max_file_bytes": MAX_BYTES},
              "scan_complete": complete, "scanned_files": state["scanned_files"],
              "failures": failures, "skipped": skipped,
              "total_hits": state["returned_hits"] if complete else None}
    if view == "compact":
        result.pop("failures")
        result.pop("skipped")
        items = ([{"item_type": "hit", "value": hit} for hit in hits]
                 + [{"item_type": "failure", "value": item} for item in failures]
                 + [{"item_type": "skipped", "value": item} for item in skipped])
        return compact_search_page(cache, context, state, result, items, None, max_chars)
    next_token = None
    if not complete:
        next_token = uuid.uuid4().hex
        atomic_json(cache / (next_token + ".json"), {"context": context, "state": state})
    return {**result, "hits": hits, "next_cursor": next_token}


def compact_search_page(cache, context, state, metadata, items, token, max_chars):
    # Reserve room for the externally visible opaque checkpoint. Diagnostics
    # use normal lossless compact items, so large errors cannot blow the budget.
    response = compact_items_page(metadata=metadata, items=items, token_context=context,
                                  page_token=token, max_chars=max_chars)
    internal = response.get("next_page_token")
    if internal:
        state["batch"] = {"metadata": metadata, "items": items, "token": internal}
    if internal or state["pending"]:
        external = uuid.uuid4().hex
        atomic_json(cache / (external + ".json"), {"context": context, "state": state})
        response["next_page_token"] = external
    return response


def re_token(value):
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)
