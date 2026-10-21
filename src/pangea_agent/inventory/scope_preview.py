"""Read-only projection of the exact explicit source selection used by source-first."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .languages import C_CPP_SUFFIXES, LUA_SUFFIXES, IGNORED_PARTS, detect_analysis_language
from .on_demand import requested_files
from pangea_agent.repositories.resolver import resolve_repositories_from_contract

RULES_REVISION = hashlib.sha256(json.dumps({
    "policy": "on-demand-v1", "c_cpp": sorted(C_CPP_SUFFIXES),
    "lua": sorted(LUA_SUFFIXES), "ignored": sorted(IGNORED_PARTS),
}, sort_keys=True).encode()).hexdigest()


def scope_revision(repository: str, scopes: list[str], paths: list[str]) -> str:
    value = {"repository": repository, "source_scope": scopes,
             "rules_revision": RULES_REVISION, "files": sorted(set(paths))}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def preview_scope(data_root: str, repository: str, source_scope: list[str]) -> dict:
    if not isinstance(source_scope, list) or not source_scope:
        raise ValueError("请选择源码范围；分析整个仓库需要明确选择根目录")
    if any(not isinstance(item, str) or not item.strip() or "\0" in item for item in source_scope):
        raise ValueError("源码范围必须是非空相对路径")
    scopes = sorted(set(item.strip().replace("\\", "/") for item in source_scope))
    repositories = resolve_repositories_from_contract({"repository": repository}, data_root)
    for repo in repositories:
        root = Path(repo["source_root"]).resolve()
        for scope in scopes:
            candidate = (root / scope).resolve()
            candidate.relative_to(root)
            if not candidate.exists():
                raise ValueError(f"源码范围不存在：{scope}")
            if candidate.is_dir():
                # pathlib.rglob may omit unreadable subtrees. A preview must fail
                # explicitly rather than presenting a partial file count as final.
                def unreadable(error):
                    raise error
                for _directory, directories, _files in os.walk(candidate, onerror=unreadable):
                    directories[:] = [name for name in directories if name not in IGNORED_PARTS]
    try:
        language = detect_analysis_language(repositories, scopes)
    except ValueError as exc:
        if str(exc) == "用户指定范围没有可分析的 C/C++ 或 Lua 源码":
            return {"preview_state": "complete", "normalized_scope": scopes,
                    "matched_file_count": 0, "files": [], "rules_revision": RULES_REVISION,
                    "scope_revision": scope_revision(repository, scopes, [])}
        raise
    expansion = requested_files(repositories, scopes, language)
    roots = {repo["repo_id"]: Path(repo["source_root"]) for repo in repositories}
    files = []
    for group in expansion["groups"]:
        for relative in group["code_paths"]:
            file = roots[group["repo_id"]] / relative
            # Validate accessibility now; never turn a read fault into zero hits.
            with file.open("rb") as source:
                source.read(1)
            files.append({"relative_path": relative, "size_bytes": file.stat().st_size})
    return {"preview_state": "complete", "normalized_scope": scopes,
            "analysis_language": language, "matched_file_count": len(files), "files": files,
            "rules_revision": RULES_REVISION,
            "scope_revision": scope_revision(repository, scopes, [item["relative_path"] for item in files])}


def assert_scope_revision(contract: dict, data_root: str) -> None:
    expected = contract.get("expected_scope_revision")
    if expected:
        current = preview_scope(data_root, contract["repository"], contract["source_scope"])
        if current["scope_revision"] != expected:
            raise ValueError("SOURCE_SCOPE_CHANGED: 源码文件集合已变化，请重新预览并确认范围")
