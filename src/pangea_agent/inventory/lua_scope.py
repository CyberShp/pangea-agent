from __future__ import annotations

from pathlib import Path, PurePosixPath

from .lua_symbols import LuaParserUnavailableError, parse_lua_file
from .source_languages import LUA_SUFFIXES


def _module_names(relative: str) -> set[str]:
    path = PurePosixPath(relative)
    if path.suffix.lower() not in LUA_SUFFIXES:
        return set()
    module_path = path.with_suffix("")
    if module_path.name == "init":
        module_path = module_path.parent
    slash_name = module_path.as_posix().strip("./")
    if not slash_name:
        return set()
    return {slash_name, slash_name.replace("/", ".")}


def _normalized_module(module: str) -> str:
    return module.strip().replace(".", "/").strip("/")


def expand_lua_context(
    root: Path,
    code_paths: dict[Path, str],
    groups: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    lua_paths = {
        path: relative
        for path, relative in code_paths.items()
        if path.suffix.lower() in LUA_SUFFIXES
    }
    if not lua_paths:
        return [], [], []
    scoped_lua_paths = {
        relative
        for group in groups
        for relative in group["code_paths"]
        if PurePosixPath(relative).suffix.lower() in LUA_SUFFIXES
    }
    if not scoped_lua_paths:
        return [], [], []

    module_index: dict[str, list[str]] = {}
    for relative in lua_paths.values():
        for module in _module_names(relative):
            module_index.setdefault(_normalized_module(module), []).append(relative)

    imports_by_path: dict[str, list[dict]] = {}
    framework_paths: set[str] = set()
    unresolved: list[dict] = []
    for path, relative in lua_paths.items():
        try:
            parsed = parse_lua_file(path)
        except LuaParserUnavailableError as exc:
            unresolved.append({
                "path": relative,
                "module": None,
                "reason": "parser_unavailable",
                "packages": exc.packages,
            })
            continue
        imports_by_path[relative] = parsed["imports"]
        if parsed["framework_signals"]:
            framework_paths.add(relative)

    context_files: list[dict] = []
    resolved_dependencies: list[dict] = []
    for group in groups:
        owned = set(group["code_paths"])
        source_lua = sorted(path for path in owned if PurePosixPath(path).suffix.lower() in LUA_SUFFIXES)
        source_modules = {
            _normalized_module(module)
            for relative in source_lua
            for module in _module_names(relative)
        }

        frontier = source_lua
        for depth in range(2):
            next_frontier: list[str] = []
            for relative in frontier:
                for item in imports_by_path.get(relative, []):
                    module = item["module"]
                    if depth == 1 and (
                        module is None or _normalized_module(module) != "mc"
                    ):
                        continue
                    if module is None:
                        unresolved.append({
                            "path": relative,
                            "line": item["line"],
                            "module": None,
                            "reason": "dynamic_require",
                            "expression": item.get("expression", ""),
                        })
                        continue
                    candidates = sorted(set(module_index.get(_normalized_module(module), [])))
                    if len(candidates) != 1:
                        unresolved.append({
                            "path": relative,
                            "line": item["line"],
                            "module": module,
                            "reason": "not_found" if not candidates else "ambiguous",
                            "candidates": candidates,
                        })
                        continue
                    dependency = candidates[0]
                    item["resolved_path"] = dependency
                    resolved_dependencies.append({
                        "repo_id": group["repo_id"],
                        "path": relative,
                        "line": item["line"],
                        "module": module,
                        "resolved_path": dependency,
                    })
                    if dependency in owned:
                        continue
                    group["context_paths"].append(dependency)
                    context_files.append({
                        "repo_id": group["repo_id"],
                        "path": dependency,
                        "reason": (
                            f"lua_require:{module}"
                            if depth == 0
                            else f"lua_framework_require:{module}"
                        ),
                    })
                    if depth == 0 and dependency in framework_paths:
                        next_frontier.append(dependency)
            frontier = sorted(set(next_frontier))

        if source_modules:
            for relative, imports in imports_by_path.items():
                if relative in owned:
                    continue
                matched = sorted({
                    item["module"]
                    for item in imports
                    if item["module"] is not None
                    and _normalized_module(item["module"]) in source_modules
                })
                if not matched:
                    continue
                group["context_paths"].append(relative)
                context_files.append({
                    "repo_id": group["repo_id"],
                    "path": relative,
                    "reason": f"lua_direct_requirer:{','.join(matched[:5])}",
                })

        group["context_paths"] = sorted(dict.fromkeys(group["context_paths"]))

    unique_unresolved = []
    seen_unresolved: set[tuple] = set()
    for item in unresolved:
        key = (
            item.get("path"),
            item.get("line"),
            item.get("module"),
            item.get("reason"),
            tuple(item.get("candidates", [])),
        )
        if key not in seen_unresolved:
            seen_unresolved.add(key)
            unique_unresolved.append(item)
    return context_files, unique_unresolved, resolved_dependencies
