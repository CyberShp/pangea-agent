from __future__ import annotations

from pathlib import Path

from .branch_ids import assign_branch_ids
from .languages import IGNORED_PARTS, LUA_SUFFIXES
from .lua_resources import extract_lua_resource_signals
from .lua_symbols import TreeSitterLuaUnavailableError, parse_lua_file


def build_lua_inventory(repositories: list[dict], module_scope: list[str]) -> dict:
    files = []
    missing_dependencies: set[str] = set()
    parse_failures: list[dict] = []
    for repository in repositories:
        repo_id = repository["repo_id"]
        root = Path(repository["source_root"])
        seen_paths: set[Path] = set()
        for scope in module_scope or ["."]:
            scoped_root = root / scope
            if not scoped_root.exists():
                continue
            candidates = (
                [scoped_root] if scoped_root.is_file() else scoped_root.rglob("*")
            )
            for path in candidates:
                if (
                    not path.is_file()
                    or path.suffix.lower() not in LUA_SUFFIXES
                    or any(
                        part in IGNORED_PARTS
                        for part in path.relative_to(root).parts
                    )
                    or path in seen_paths
                ):
                    continue
                seen_paths.add(path)
                relative_path = path.relative_to(root).as_posix()
                lines = path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                structural_complete = True
                try:
                    parsed = parse_lua_file(path)
                except TreeSitterLuaUnavailableError as exc:
                    missing_dependencies.update(exc.packages)
                    structural_complete = False
                    parsed = _empty_parse("missing_dependency")
                except (OSError, ValueError, RuntimeError) as exc:
                    structural_complete = False
                    parse_failures.append(
                        {"repo_id": repo_id, "path": relative_path, "error": str(exc)}
                    )
                    parsed = _empty_parse(str(exc))

                if parsed["has_error"]:
                    structural_complete = False
                    parse_failures.append(
                        {
                            "repo_id": repo_id,
                            "path": relative_path,
                            "error": "tree-sitter reported Lua syntax errors",
                            "locations": parsed["parse_errors"],
                        }
                    )
                branches = assign_branch_ids(
                    repo_id,
                    relative_path,
                    parsed["branches"],
                )
                files.append(
                    {
                        "repo_id": repo_id,
                        "path": relative_path,
                        "line_count": len(lines),
                        "parser": parsed["parser"],
                        "grammar_package": parsed["grammar_package"],
                        "parse_complete": structural_complete,
                        "fallback_analysis": (
                            None if structural_complete else "raw_text"
                        ),
                        "parse_errors": parsed["parse_errors"],
                        "functions": parsed["functions"],
                        "branches": branches,
                        "preprocessor": [],
                        "types": [],
                        "calls": parsed["calls"],
                        "requires": parsed["requires"],
                        "module_exports": parsed["module_exports"],
                        "state_writes": parsed["state_writes"],
                        "protected_calls": parsed["protected_calls"],
                        "coroutine_calls": parsed["coroutine_calls"],
                        "resource_signals": extract_lua_resource_signals(lines),
                    }
                )
    return {
        "files": files,
        "file_count": len(files),
        "missing_dependencies": sorted(missing_dependencies),
        "parse_failures": parse_failures,
        "structural_parse_complete": not missing_dependencies and not parse_failures,
    }


def _empty_parse(error: str) -> dict:
    return {
        "parser": "raw_text",
        "grammar_package": None,
        "has_error": error != "missing_dependency",
        "functions": [],
        "branches": [],
        "calls": [],
        "requires": [],
        "module_exports": [],
        "state_writes": [],
        "protected_calls": [],
        "coroutine_calls": [],
        "parse_errors": (
            []
            if error == "missing_dependency"
            else [{"line": None, "kind": "parser_failure", "text": error}]
        ),
    }
