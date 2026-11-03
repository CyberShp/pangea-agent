"""Explicit text references: one resolver for preview and frozen read boundaries."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import TypeAdapter

from pangea_agent.models.contract import TestToolSource
from pangea_agent.repositories.registry import list_registered_repositories
from pangea_agent.repositories.resolver import resolve_repositories_from_contract


def _relative(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if (not value or "\0" in value or PureWindowsPath(value).drive
            or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts):
        raise ValueError(f"TEST_TOOL_PATH_INVALID: 工具路径必须是仓库内相对路径：{value}")
    return str(PurePosixPath(value))


def preview_test_tools(data_root: str, selections: list[dict], *, require_directories: bool = True) -> dict:
    selected = TypeAdapter(list[TestToolSource]).validate_python(selections)
    if require_directories:
        from .selected_directories import validate_selected_directories
        for item in selected:
            validate_selected_directories(data_root, item.repo_id, item.paths)
    registered = set(list_registered_repositories(data_root))
    grouped: dict[str, set[str]] = {}
    for item in selected:
        if item.repo_id not in registered or any(c in item.repo_id for c in "/\\") or item.repo_id in {".", ".."}:
            raise ValueError(f"TEST_TOOL_REPOSITORY_UNKNOWN: 工具仓库未登记：{item.repo_id}")
        grouped.setdefault(item.repo_id, set()).update(_relative(path) for path in item.paths)
    files = {}
    excluded = {}
    normalized = []
    for repo_id, paths in sorted(grouped.items()):
        normalized.append({"repo_id": repo_id, "paths": sorted(paths)})
        repository = resolve_repositories_from_contract({"repository": repo_id}, data_root)[0]
        root = Path(repository["source_root"]).resolve()
        def checked(path):
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"TEST_TOOL_PATH_INVALID: 工具路径越过仓库边界：{repo_id}:{path.relative_to(root)}")
            return path
        def unreadable(error):
            raise error
        for relative in sorted(paths):
            candidate = checked(root / relative)
            if not candidate.exists():
                raise ValueError(f"TEST_TOOL_FILE_MISSING: 工具路径不存在：{repo_id}:{relative}")
            if not candidate.is_file() and not candidate.is_dir():
                raise ValueError(f"TEST_TOOL_NOT_TEXT: 工具路径不是普通文件或目录：{repo_id}:{relative}")
            candidates = [candidate] if candidate.is_file() else []
            if candidate.is_dir():
                for directory, directories, names in os.walk(candidate, onerror=unreadable):
                    # Do not silently omit selected symlink trees from the preview.
                    for name in list(directories):
                        if name in {".git", ".hg", ".svn", "__pycache__"}:
                            key = (repo_id, (Path(directory) / name).relative_to(root).as_posix())
                            excluded[key] = {"repo_id": repo_id, "relative_path": key[1], "reason": "excluded_directory"}
                            directories.remove(name)
                            continue
                        child = checked(Path(directory) / name)
                        if child.is_symlink():
                            raise ValueError(f"TEST_TOOL_PATH_INVALID: 请直接选择实际目录，不支持符号链接目录：{repo_id}:{child.relative_to(root)}")
                    candidates.extend(checked(Path(directory) / name) for name in names)
            for file in candidates:
                if not checked(file).is_file():
                    key = (repo_id, file.relative_to(root).as_posix())
                    excluded[key] = {"repo_id": repo_id, "relative_path": key[1], "reason": "not_regular_file"}
                    continue
                content = file.read_bytes()
                try:
                    content.decode("utf-8-sig")
                    if b"\0" in content:
                        raise UnicodeError("NUL byte")
                except UnicodeError as exc:
                    if candidate.is_dir():
                        key = (repo_id, file.relative_to(root).as_posix())
                        excluded[key] = {"repo_id": repo_id, "relative_path": key[1], "reason": "not_utf8_text"}
                        continue
                    raise ValueError(f"TEST_TOOL_NOT_TEXT: 工具参考必须是 UTF-8 文本，不能读取二进制：{repo_id}:{file.relative_to(root)}") from exc
                path = file.relative_to(root).as_posix()
                files[repo_id, path] = {"repo_id": repo_id, "relative_path": path, "size_bytes": len(content), "role": "reference"}
    file_list = [files[key] for key in sorted(files)]
    revision = hashlib.sha256(json.dumps({"sources": normalized, "files": sorted(files)},
                                         ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"preview_state": "complete", "normalized_sources": normalized,
            "files": file_list, "matched_file_count": len(file_list), "scope_revision": revision,
            "excluded_files": [excluded[key] for key in sorted(excluded)], "excluded_file_count": len(excluded)}


def add_test_tool_references(data_root: str, contract: dict, repositories: list[dict], expansion: dict) -> dict:
    preview = preview_test_tools(data_root, contract.get("test_tool_sources") or [], require_directories=False)
    assert_test_tool_revision(contract.get("expected_test_tool_revision"), preview)
    groups = {g["repo_id"]: g for g in expansion["groups"]}
    known_repos = {r["repo_id"] for r in repositories}
    for file in preview["files"]:
        repo, path = file["repo_id"], file["relative_path"]
        if repo not in known_repos:
            repositories.extend(resolve_repositories_from_contract({"repository": repo}, data_root))
            known_repos.add(repo)
        if repo not in groups:
            groups[repo] = {"repo_id": repo, "code_paths": [], "context_paths": []}
            expansion["groups"].append(groups[repo])
        group = groups[repo]
        if path in group["code_paths"]:
            raise ValueError(f"TEST_TOOL_SOURCE_OVERLAP: 工具文件已在被测范围，请缩小被测范围或从工具选择中移除：{repo}:{path}")
        if path not in group["context_paths"]:
            group["context_paths"].append(path)
        expansion.setdefault("context_files", []).append({"repo_id": repo, "path": path, "reason": "test_tool_reference"})
    return preview


def assert_test_tool_revision(expected: str | None, preview: dict) -> None:
    if expected and expected != preview["scope_revision"]:
        raise ValueError("TEST_TOOL_SCOPE_CHANGED: 工具文件集合已变化，请重新预览并确认范围")


def inherit_test_tool_references(parent: dict, repositories: list[dict], expansion: dict) -> dict:
    """Derived runs retain the parent's tool files, even with live product changes."""
    parent_repos = {repo["repo_id"]: repo for repo in parent["repositories"]}
    known_repos = {repo["repo_id"] for repo in repositories}
    groups = {group["repo_id"]: group for group in expansion["groups"]}
    roots = {}
    for item in parent["scope_expansion"].get("context_files", []):
        if item.get("reason") != "test_tool_reference":
            continue
        repo, path = item["repo_id"], item["path"]
        if repo not in known_repos:
            repositories.append(parent_repos[repo])
            known_repos.add(repo)
        if repo not in groups:
            groups[repo] = {"repo_id": repo, "code_paths": [], "context_paths": []}
            expansion["groups"].append(groups[repo])
        if path in groups[repo]["code_paths"]:
            raise ValueError(f"TEST_TOOL_SOURCE_OVERLAP: 工具文件已在被测范围，请缩小被测范围或从工具选择中移除：{repo}:{path}")
        if path not in groups[repo]["context_paths"]:
            groups[repo]["context_paths"].append(path)
        expansion.setdefault("context_files", []).append(dict(item))
        roots[repo, path] = parent_repos[repo]["source_root"]
    return roots
