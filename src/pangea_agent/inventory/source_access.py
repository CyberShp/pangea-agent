"""Read-only access to the frozen source view.

The worker receives this module through a client wrapper.  Every operation
resolves its run/action/task binding before opening a file and only accepts
paths present in the task's frozen source scope.  No repository discovery,
history lookup, or fallback to a live working tree is performed here.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from pangea_agent.agent_io import read_json
from pangea_agent.graph.workflow_store import load_progress
from pangea_agent.inventory.source_regions import build_source_index
from pangea_agent.models.source_first import (
    SourceBinding,
    SourceFileIndex,
    SourceIndexPage,
    SourceReadResult,
    SourceSearchHit,
    SourceSearchResult,
)


class SourceAccessError(ValueError):
    """A deterministic binding or frozen-source boundary error."""


def _safe_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise SourceAccessError(f"{label} 必须是非空标识")
    if any(char in value for char in "/\\"):
        raise SourceAccessError(f"{label} 不能包含路径分隔符")
    return value


def _run_state(data_root: str, run_id: str) -> dict[str, str]:
    return {
        "data_root": str(Path(data_root)),
        "run_id": _safe_identifier(run_id, "run_id"),
    }


def _run_dir(data_root: str, run_id: str) -> Path:
    root = Path(data_root).resolve()
    directory = (root / "runs" / _safe_identifier(run_id, "run_id")).resolve()
    try:
        directory.relative_to(root / "runs")
    except ValueError as exc:
        raise SourceAccessError("Run 路径越过 data_root/runs 边界") from exc
    if not directory.is_dir():
        raise SourceAccessError(f"Run 不存在：{run_id}")
    return directory


def _task_payload(run_dir: Path, action_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    progress_payload = read_json(run_dir / "progress.json")
    actions = progress_payload.get("actions", {}) if isinstance(progress_payload, dict) else {}
    action = actions.get(action_id)
    if not isinstance(action, dict):
        raise SourceAccessError(f"Action 不存在：{action_id}")
    task_path = action.get("task_path")
    if not isinstance(task_path, str) or not task_path:
        raise SourceAccessError(f"Action 缺少 task_path：{action_id}")
    task_file = Path(task_path).resolve()
    try:
        task_file.relative_to(run_dir)
    except ValueError as exc:
        raise SourceAccessError("task_path 越出当前 Run 数据边界") from exc
    if not task_file.is_file():
        raise SourceAccessError(f"Action task 不存在：{task_file}")
    task = read_json(task_file)
    if not isinstance(task, dict):
        raise SourceAccessError("Action task 必须是 JSON object")
    return action, task


def resolve_binding(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
) -> tuple[SourceBinding, Path, dict[str, Any], dict[str, Any]]:
    """Resolve and verify the exact binding supplied by the host."""

    run_id = _safe_identifier(run_id, "run_id")
    if not isinstance(action_id, str) or not action_id:
        raise SourceAccessError("action_id 不能为空")
    if not isinstance(task_id, str) or not task_id:
        raise SourceAccessError("task_id 不能为空")
    run_dir = _run_dir(data_root, run_id)
    action, task = _task_payload(run_dir, action_id)
    bound_task_id = action.get("task_id")
    if bound_task_id != task_id:
        raise SourceAccessError(
            "task_id 与 Graph 绑定不一致："
            f"expected={bound_task_id!r} actual={task_id!r}"
        )
    if action.get("status") not in {"dispatched", "settled", "accepted"}:
        raise SourceAccessError(
            f"Action 当前不可执行源码操作：status={action.get('status')!r}"
        )
    task_run_id = task.get("run_id")
    if task_run_id is not None and task_run_id != run_id:
        raise SourceAccessError("task.run_id 与当前 Run 不一致")
    task_action_id = task.get("action_id")
    if task_action_id is not None and task_action_id != action_id:
        raise SourceAccessError("task.action_id 与当前 Action 不一致")
    binding = SourceBinding(
        data_root=str(Path(data_root).resolve()),
        run_id=run_id,
        action_id=action_id,
        task_id=task_id,
    )
    return binding, run_dir, action, task


def _source_manifest(run_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    manifest_path = task.get("source_manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        candidate = run_dir / "inputs" / "source-manifest.json"
    else:
        candidate = Path(manifest_path).resolve()
        try:
            candidate.relative_to(run_dir)
        except ValueError as exc:
            raise SourceAccessError("source_manifest_path 越出当前 Run 数据边界") from exc
    if not candidate.is_file():
        raise SourceAccessError(f"冻结 source manifest 不存在：{candidate}")
    manifest = read_json(candidate)
    if not isinstance(manifest, dict):
        raise SourceAccessError("冻结 source manifest 必须是 JSON object")
    return manifest


def _normal_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceAccessError("源码路径不能为空")
    value = value.strip().replace("\\", "/")
    if value.startswith("/") or "\x00" in value:
        raise SourceAccessError(f"源码路径必须是安全的相对路径：{value!r}")
    parts = PurePosixPath(value).parts
    if ".." in parts:
        raise SourceAccessError(f"源码路径越过仓库边界：{value}")
    return str(PurePosixPath(value))


def _allowed_paths(task: dict[str, Any], manifest: dict[str, Any]) -> set[tuple[str, str]]:
    explicit = task.get("allowed_paths")
    result: set[tuple[str, str]] = set()
    if isinstance(explicit, list):
        for item in explicit:
            if isinstance(item, dict) and item.get("repo_id") and item.get("path"):
                result.add((str(item["repo_id"]), _normal_path(str(item["path"]))))
            elif isinstance(item, str) and ":" in item:
                repo_id, path = item.split(":", 1)
                result.add((repo_id, _normal_path(path)))
    if explicit is not None:
        return result
    source_scope = manifest.get("source_scope", [])
    context = manifest.get("scope_expansion", {}).get("context_files", [])
    repositories = manifest.get("repositories", [])
    repo_ids = [item.get("repo_id") for item in repositories if isinstance(item, dict)]
    for item in source_scope if isinstance(source_scope, list) else []:
        # source_scope in a manifest is path-only; use the unambiguous repo when
        # there is one.  Per-unit tasks always carry explicit allowed_paths.
        if len(repo_ids) == 1:
            result.add((str(repo_ids[0]), _normal_path(str(item))))
    if isinstance(context, list):
        for item in context:
            if isinstance(item, dict) and item.get("repo_id") and item.get("path"):
                result.add((str(item["repo_id"]), _normal_path(str(item["path"]))))
    for group in manifest.get("scope_expansion", {}).get("groups", []):
        if not isinstance(group, dict) or not group.get("repo_id"):
            continue
        repo_id = str(group["repo_id"])
        for key in ("code_paths", "context_paths"):
            for item in group.get(key, []) if isinstance(group.get(key, []), list) else []:
                result.add((repo_id, _normal_path(str(item))))
    return result


def _repositories(run_dir: Path, manifest: dict[str, Any]) -> dict[str, tuple[Path, dict[str, Any]]]:
    roots: dict[str, tuple[Path, dict[str, Any]]] = {}
    frozen_root = (run_dir / "inputs" / "source").resolve()
    for item in manifest.get("repositories", []):
        if not isinstance(item, dict) or not item.get("repo_id") or not item.get("source_root"):
            continue
        repo_id = str(item["repo_id"])
        root = Path(str(item["source_root"])).resolve()
        try:
            root.relative_to(frozen_root)
        except ValueError as exc:
            raise SourceAccessError(
                f"冻结 repository source_root 越出 source 边界：{repo_id}:{root}"
            ) from exc
        roots[repo_id] = (root, item)
    if not roots:
        raise SourceAccessError("冻结 source manifest 没有可读 repository")
    return roots


def _inventory_index(run_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    index_path = run_dir / "inputs" / "source-index.json"
    if index_path.is_file():
        value = read_json(index_path)
        if isinstance(value, dict) and value.get("format_version") == "pangea-source-index-v1":
            return value
    inventory_path = task.get("inventory_path") or str(run_dir / "inputs" / "inventory.json")
    path = Path(str(inventory_path)).resolve()
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise SourceAccessError("inventory_path 越出当前 Run 数据边界") from exc
    return build_source_index(read_json(path))


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = base64.urlsafe_b64decode(cursor.encode("ascii") + b"===").decode("ascii")
        offset = int(value)
    except (ValueError, UnicodeError, base64.binascii.Error) as exc:
        raise SourceAccessError("无效的源码读取 cursor") from exc
    if offset < 0:
        raise SourceAccessError("源码读取 cursor 不能为负数")
    return offset


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def _file_allowed(repo_id: str, path: str, allowed: set[tuple[str, str]]) -> bool:
    return (repo_id, _normal_path(path)) in allowed


def _evidence_handle(run_id: str, repo_id: str, path: str, start: int, end: int) -> str:
    value = f"{run_id}|{repo_id}|{_normal_path(path)}|{start}|{end}"
    return "e-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def source_index(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    cursor: str | None = None,
    page_size: int = 64,
) -> dict[str, Any]:
    binding, run_dir, _, task = resolve_binding(data_root, run_id, action_id, task_id)
    if page_size < 1 or page_size > 512:
        raise SourceAccessError("page_size 必须在 1 到 512 之间")
    manifest = _source_manifest(run_dir, task)
    allowed = _allowed_paths(task, manifest)
    index = _inventory_index(run_dir, task)
    files = [
        SourceFileIndex.model_validate(item)
        for item in index.get("files", [])
        if isinstance(item, dict)
        and _file_allowed(str(item.get("repo_id", "")), str(item.get("path", "")), allowed)
    ]
    files.sort(key=lambda item: (item.repo_id, item.path))
    offset = _decode_cursor(cursor)
    page = files[offset : offset + page_size]
    next_cursor = _encode_cursor(offset + page_size) if offset + page_size < len(files) else None
    return SourceIndexPage(
        binding=binding,
        files=page,
        next_cursor=next_cursor,
        total_files=len(files),
    ).model_dump(mode="json")


def _read_frozen_file(
    repositories: dict[str, tuple[Path, dict[str, Any]]],
    repo_id: str,
    path: str,
) -> tuple[Path, list[str]]:
    path = _normal_path(path)
    repository = repositories.get(repo_id)
    if repository is None:
        raise SourceAccessError(f"未知冻结 repository：{repo_id}")
    root = repository[0]
    source = (root / path).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise SourceAccessError(f"源码路径越过冻结 repository 边界：{repo_id}:{path}") from exc
    if not source.is_file():
        raise SourceAccessError(f"冻结源码文件不存在：{repo_id}:{path}")
    try:
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise SourceAccessError(f"冻结源码不可读取：{source}") from exc
    return source, lines


def source_read(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    repo_id: str,
    path: str | None = None,
    region_id: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    cursor: str | None = None,
    max_lines: int = 400,
) -> dict[str, Any]:
    binding, run_dir, _, task = resolve_binding(data_root, run_id, action_id, task_id)
    if max_lines < 1 or max_lines > 2000:
        raise SourceAccessError("max_lines 必须在 1 到 2000 之间")
    manifest = _source_manifest(run_dir, task)
    allowed = _allowed_paths(task, manifest)
    index = _inventory_index(run_dir, task)
    candidate_region = None
    if region_id:
        for item in index.get("files", []):
            for region in item.get("regions", []) if isinstance(item, dict) else []:
                if isinstance(region, dict) and region.get("region_id") == region_id:
                    candidate_region = region
                    break
            if candidate_region is not None:
                break
        if candidate_region is None:
            raise SourceAccessError(f"未知 source region：{region_id}")
        repo_id = str(candidate_region.get("repo_id"))
        path = str(candidate_region.get("path"))
        line_start = int(candidate_region.get("line_start", 1))
        line_end = int(candidate_region.get("line_end", line_start))
    if not path:
        raise SourceAccessError("source_read 需要 path 或 region_id")
    path = _normal_path(path)
    if not _file_allowed(repo_id, path, allowed):
        raise SourceAccessError(f"源码读取越出当前 task scope：{repo_id}:{path}")
    _, lines = _read_frozen_file(_repositories(run_dir, manifest), repo_id, path)
    start = 1 if line_start is None else int(line_start)
    end = len(lines) if line_end is None else int(line_end)
    if start < 1 or end < start:
        raise SourceAccessError("source_read 行号范围无效")
    if start > len(lines) + 1:
        raise SourceAccessError("source_read 起始行超出文件范围")
    end = min(end, len(lines))
    offset = _decode_cursor(cursor)
    start += offset
    if start > end:
        raise SourceAccessError("source_read cursor 已超出指定范围")
    page_end = min(end, start + max_lines - 1)
    text = "\n".join(
        f"{number}: {lines[number - 1]}"
        for number in range(start, page_end + 1)
    )
    next_cursor = (
        _encode_cursor(page_end - (1 if line_start is None else int(line_start)) + 1)
        if page_end < end
        else None
    )
    return SourceReadResult(
        binding=binding,
        repo_id=repo_id,
        path=path,
        line_start=start,
        line_end=page_end,
        text=text,
        evidence_handle=_evidence_handle(binding.run_id, repo_id, path, start, page_end),
        next_cursor=next_cursor,
    ).model_dump(mode="json")


def source_search(
    data_root: str,
    run_id: str,
    action_id: str,
    task_id: str,
    *,
    query: str,
    repo_id: str | None = None,
    path: str | None = None,
    cursor: str | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    binding, run_dir, _, task = resolve_binding(data_root, run_id, action_id, task_id)
    if not isinstance(query, str) or not query:
        raise SourceAccessError("source_search query 不能为空")
    if page_size < 1 or page_size > 500:
        raise SourceAccessError("page_size 必须在 1 到 500 之间")
    manifest = _source_manifest(run_dir, task)
    allowed = _allowed_paths(task, manifest)
    if repo_id and repo_id not in {item[0] for item in allowed}:
        raise SourceAccessError(f"source_search repository 不在 task scope：{repo_id}")
    normalized_path = _normal_path(path) if path else None
    repositories = _repositories(run_dir, manifest)
    hits: list[SourceSearchHit] = []
    for current_repo, current_path in sorted(allowed):
        if repo_id and current_repo != repo_id:
            continue
        if normalized_path and current_path != normalized_path:
            continue
        _, lines = _read_frozen_file(repositories, current_repo, current_path)
        for line_number, line in enumerate(lines, 1):
            if query not in line:
                continue
            hits.append(SourceSearchHit(
                repo_id=current_repo,
                path=current_path,
                line=line_number,
                text=line,
                evidence_handle=_evidence_handle(
                    binding.run_id, current_repo, current_path, line_number, line_number
                ),
            ))
    offset = _decode_cursor(cursor)
    page = hits[offset : offset + page_size]
    next_cursor = _encode_cursor(offset + page_size) if offset + page_size < len(hits) else None
    return SourceSearchResult(
        binding=binding,
        query=query,
        hits=page,
        next_cursor=next_cursor,
    ).model_dump(mode="json")
