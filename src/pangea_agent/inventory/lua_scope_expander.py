from __future__ import annotations

from pathlib import Path, PurePosixPath

from .languages import IGNORED_PARTS, LUA_SUFFIXES
from .lua_symbols import parse_lua_file


def expand_lua_analysis_scope(
    repositories: list[dict], requested_scopes: list[str]
) -> dict:
    normalized_scopes = [_normalize(value) for value in requested_scopes or ["."]]
    groups: list[dict] = []
    context_files: list[dict] = []
    require_dependencies: list[dict] = []

    for repository in repositories:
        repo_id = repository["repo_id"]
        root = Path(repository["source_root"])
        code_paths = {
            _relative(path, root): path for path in _iter_lua_files(root)
        }
        repo_groups = []
        for scope in normalized_scopes:
            paths = {
                relative
                for relative in code_paths
                if _inside_scope(relative, scope)
            }
            context_paths: set[str] = set()
            for relative in sorted(paths):
                for requirement in _required_modules(code_paths[relative]):
                    module = requirement.get("module")
                    record = {
                        "repo_id": repo_id,
                        "source_path": relative,
                        "line": requirement["line"],
                        "module": module,
                        "expression": requirement["expression"],
                    }
                    if module is None:
                        require_dependencies.append(
                            {**record, "status": "dynamic", "candidates": []}
                        )
                        continue
                    matches = _resolve_module(module, code_paths)
                    if not matches:
                        require_dependencies.append(
                            {**record, "status": "external", "candidates": []}
                        )
                        continue
                    if len(matches) > 1:
                        require_dependencies.append(
                            {
                                **record,
                                "status": "ambiguous",
                                "candidates": matches,
                            }
                        )
                        continue
                    dependency = matches[0]
                    status = "source" if dependency in paths else "context"
                    require_dependencies.append(
                        {
                            **record,
                            "status": status,
                            "path": dependency,
                            "candidates": matches,
                        }
                    )
                    if dependency in paths:
                        continue
                    context_paths.add(dependency)
                    context_files.append(
                        {
                            "repo_id": repo_id,
                            "path": dependency,
                            "reason": f"direct_require:{module}",
                        }
                    )
            repo_groups.append(
                {
                    "repo_id": repo_id,
                    "requested_scope": [scope],
                    "code_paths": sorted(paths),
                    "context_paths": sorted(context_paths),
                }
            )
        groups.extend(_merge_overlapping_groups(repo_groups))

    return {
        "requested_scope": normalized_scopes,
        "groups": groups,
        "context_files": _unique_records(context_files),
        "require_dependencies": sorted(
            require_dependencies,
            key=lambda item: (
                item["repo_id"],
                item["source_path"],
                item["line"],
            ),
        ),
        "added_files": [],
        "boundary": (
            "source_scope = explicit Lua scope; "
            "context_scope = directly required repository-local Lua modules"
        ),
    }


def _iter_lua_files(root: Path):
    resolved_root = root.resolve()
    for path in root.rglob("*"):
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"源码范围越过仓库边界：{path}") from exc
        if (
            path.is_file()
            and path.suffix.lower() in LUA_SUFFIXES
            and not any(
                part in IGNORED_PARTS for part in path.relative_to(root).parts
            )
        ):
            yield path


def _required_modules(path: Path) -> list[dict]:
    return parse_lua_file(path)["requires"]


def _resolve_module(module: str, code_paths: dict[str, Path]) -> list[str]:
    stem = module.replace(".", "/").strip("/")
    candidates = (f"{stem}.lua", f"{stem}/init.lua")
    matches = sorted(
        {
            relative
            for relative in code_paths
            for candidate in candidates
            if relative == candidate or relative.endswith(f"/{candidate}")
        }
    )
    return matches


def _merge_overlapping_groups(groups: list[dict]) -> list[dict]:
    merged = [
        {
            **group,
            "requested_scope": list(group["requested_scope"]),
            "code_paths": list(group["code_paths"]),
            "context_paths": list(group["context_paths"]),
        }
        for group in groups
    ]
    changed = True
    while changed:
        changed = False
        for left in range(len(merged)):
            left_paths = set(merged[left]["code_paths"])
            for right in range(left + 1, len(merged)):
                if not left_paths.intersection(merged[right]["code_paths"]):
                    continue
                for key in ("requested_scope", "code_paths", "context_paths"):
                    merged[left][key] = sorted(
                        dict.fromkeys(merged[left][key] + merged[right][key])
                    )
                merged.pop(right)
                changed = True
                break
            if changed:
                break
    return merged


def _normalize(value: str) -> str:
    return value.replace("\\", "/").strip("/") or "."


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _inside_scope(path: str, scope: str) -> bool:
    scope_path = PurePosixPath(scope)
    if scope_path.suffix.lower() in LUA_SUFFIXES:
        return path == scope
    return scope == "." or path == scope or path.startswith(f"{scope}/")


def _unique_records(records: list[dict]) -> list[dict]:
    unique = {
        (record["repo_id"], record["path"]): record for record in records
    }
    return sorted(unique.values(), key=lambda item: (item["repo_id"], item["path"]))
