from __future__ import annotations

from pathlib import Path

from .cpp_branches import extract_branches
from .cpp_resources import extract_resource_signals
from .cpp_symbols import TreeSitterUnavailableError, extract_functions, parse_cpp_file

CODE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}
IGNORED_PARTS = {".git", "build", "dist", "third_party", "node_modules", "__pycache__"}
KNOWN_EXTENSION_ERRORS = {"__attribute__((unused))", ")"}


def build_lightweight_inventory(repositories: list[dict], module_scope: list[str]) -> dict:
    files = []
    missing_dependencies: set[str] = set()
    parse_failures: list[dict] = []
    for repo in repositories:
        repo_id = repo["repo_id"]
        root = Path(repo["source_root"])
        seen_paths: set[Path] = set()
        for scope in module_scope or ["."]:
            scoped_root = root / scope
            if not scoped_root.exists():
                continue
            candidates = [scoped_root] if scoped_root.is_file() else scoped_root.rglob("*")
            for path in candidates:
                if (
                    not path.is_file()
                    or path.suffix.lower() not in CODE_SUFFIXES
                    or any(part in IGNORED_PARTS for part in path.parts)
                    or path in seen_paths
                ):
                    continue
                seen_paths.add(path)
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                relative_path = path.relative_to(root).as_posix()
                structural_complete = True
                try:
                    parsed = parse_cpp_file(path)
                except TreeSitterUnavailableError as exc:
                    missing_dependencies.update(exc.packages)
                    structural_complete = False
                    parsed = {
                        "parser": "regex_fallback",
                        "grammar_package": None,
                        "has_error": False,
                        "functions": extract_functions(lines),
                        "branches": extract_branches(lines),
                        "preprocessor": _extract_preprocessor(lines),
                        "types": [],
                        "parse_errors": [],
                    }
                except (OSError, ValueError, RuntimeError) as exc:
                    structural_complete = False
                    parse_failures.append({"repo_id": repo_id, "path": relative_path, "error": str(exc)})
                    parsed = {
                        "parser": "regex_fallback",
                        "grammar_package": None,
                        "has_error": True,
                        "functions": extract_functions(lines),
                        "branches": extract_branches(lines),
                        "preprocessor": _extract_preprocessor(lines),
                        "types": [],
                        "parse_errors": [{"line": None, "kind": "parser_failure", "text": str(exc)}],
                    }
                if parsed["has_error"]:
                    function_ranges = [
                        (item["line"], item.get("end_line", item["line"]))
                        for item in parsed["functions"]
                    ]
                    syntax_errors_in_functions = [
                        item for item in parsed["parse_errors"]
                        if item["kind"] == "syntax_error"
                        and item["text"].strip()
                        and item["text"].strip() not in KNOWN_EXTENSION_ERRORS
                        and not _known_macro_parse_artifact(item, lines)
                        and any(start <= item["line"] <= end for start, end in function_ranges)
                    ]
                    material_errors = syntax_errors_in_functions
                    structural_complete = structural_complete and not material_errors
                    if material_errors:
                        parse_failures.append({
                            "repo_id": repo_id,
                            "path": relative_path,
                            "error": "tree-sitter reported syntax errors inside a function",
                            "locations": material_errors,
                        })
                files.append({
                    "repo_id": repo_id,
                    "path": relative_path,
                    "line_count": len(lines),
                    "parser": parsed["parser"],
                    "grammar_package": parsed["grammar_package"],
                    "parse_complete": structural_complete,
                    "fallback_analysis": None if structural_complete else "raw_text",
                    "parse_errors": parsed["parse_errors"],
                    "functions": parsed["functions"],
                    "branches": parsed["branches"],
                    "preprocessor": parsed["preprocessor"],
                    "types": parsed["types"],
                    "resource_signals": extract_resource_signals(lines),
                })
    return {
        "files": files,
        "file_count": len(files),
        "missing_dependencies": sorted(missing_dependencies),
        "parse_failures": parse_failures,
        "structural_parse_complete": not missing_dependencies and not parse_failures,
    }


def _extract_preprocessor(lines: list[str]) -> list[dict]:
    directives = []
    for line_number, raw in enumerate(lines, 1):
        text = raw.strip()
        if text.startswith(("#if", "#elif", "#else", "#endif", "#define")):
            directives.append({"line": line_number, "end_line": line_number, "kind": "preprocessor", "condition": text[:500]})
    return directives


def _known_macro_parse_artifact(item: dict, lines: list[str]) -> bool:
    """Ignore only recognizable project/compiler macro artifacts near the reported error."""
    line_number = item.get("line")
    if not isinstance(line_number, int) or line_number < 1 or line_number > len(lines):
        return False
    current = lines[line_number - 1]
    previous = lines[line_number - 2] if line_number > 1 else ""
    nearby = f"{previous}\n{current}"
    token = str(item.get("text", "")).strip()
    if "TAILQ_HEAD(" in current:
        return True
    if current.lstrip().startswith("IOBUF_FOREACH_NUMA_ID("):
        return True
    if "SPDK_CONTAINEROF(" in nearby:
        return True
    if "ntt_list_first_entry(" in nearby:
        return True
    if "__attribute__((" in current or "__spdk_nonstring" in current:
        return True
    if token.endswith(".") and "offsetof(" in nearby:
        return True
    if token == "void" and any(
        line.lstrip().startswith("SPDK_LOG_REGISTER_COMPONENT(")
        for line in lines[max(0, line_number - 4):line_number]
    ):
        return True
    return False
