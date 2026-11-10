"""Structural navigation for Python and sh/Bash; never executes target source.

Calls are syntax observations, not resolved cross-language edges. Dynamic
commands, imports and FFI targets remain for the Agent to interpret.
"""
from __future__ import annotations

import ast
from pathlib import Path
import tokenize

from .branch_ids import assign_branch_ids
from .languages import source_files


def _empty(parser: str) -> dict:
    return {"parser": parser, "functions": [], "types": [], "branches": [],
            "calls": [], "imports": [], "parse_errors": [], "parse_complete": True}


def parse_python_file(path: Path) -> dict:
    result = _empty("python-ast")
    # Honor PEP 263 encoding declarations without importing the target module.
    with tokenize.open(path) as stream:
        source = stream.read()
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        location = {"line": getattr(node, "lineno", 1),
                    "end_line": getattr(node, "end_lineno", 1)}
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append({**location, "symbol": node.name})
        elif isinstance(node, ast.ClassDef):
            result["types"].append({**location, "symbol": node.name})
        elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
                               ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Match, ast.IfExp)):
            result["branches"].append({**location, "kind": type(node).__name__,
                                       "condition": ast.get_source_segment(source, getattr(node, "test", node))})
        elif isinstance(node, ast.Call):
            result["calls"].append({**location, "callee": ast.unparse(node.func),
                                    "text": ast.get_source_segment(source, node)})
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            result["imports"].append({**location, "text": ast.get_source_segment(source, node)})
    return result


def parse_shell_file(path: Path) -> dict:
    from tree_sitter import Language, Parser
    import tree_sitter_bash

    result = _empty("tree-sitter-bash")
    source = path.read_bytes()
    root = Parser(Language(tree_sitter_bash.language())).parse(source).root_node
    pending = [root]
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.named_children))
        location = {"line": node.start_point.row + 1,
                    "end_line": node.end_point.row + (1 if node.end_point.column else 0)}
        location["end_line"] = max(location["line"], location["end_line"])
        text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if node.type == "function_definition":
            name = node.child_by_field_name("name")
            result["functions"].append({**location, "symbol": name.text.decode("utf-8", errors="replace") if name else "<anonymous>"})
        elif node.type in {"if_statement", "elif_clause", "else_clause", "case_statement",
                           "case_item", "for_statement", "while_statement", "c_style_for_statement"}:
            result["branches"].append({**location, "kind": node.type, "condition": text})
        elif node.type in {"command", "declaration_command", "unset_command"}:
            name = node.child_by_field_name("name")
            result["calls"].append({**location, "callee": name.text.decode("utf-8", errors="replace") if name else node.type,
                                    "text": text})
        if node.type == "ERROR" or node.is_missing:
            result["parse_errors"].append({**location, "kind": "syntax_error", "text": text})
    result["parse_complete"] = not root.has_error
    return result


def build_script_inventory(repositories: list[dict], scopes: list[str]) -> dict:
    files, failures, missing = [], [], set()
    for repository, path, language in source_files(repositories, scopes):
        if language not in {"python", "shell"}:
            continue
        relative = path.relative_to(Path(repository["source_root"]).resolve()).as_posix()
        try:
            parsed = parse_python_file(path) if language == "python" else parse_shell_file(path)
        except (ImportError, SyntaxError, UnicodeError, ValueError, RuntimeError, RecursionError) as exc:
            parsed = _empty("raw_text")
            parsed["parse_complete"] = False
            parsed["parse_errors"] = [{"line": getattr(exc, "lineno", None), "kind": "parser_failure", "text": str(exc)}]
            if isinstance(exc, ImportError):
                missing.add("tree-sitter-bash")
        if not parsed["parse_complete"]:
            failures.append({"repo_id": repository["repo_id"], "path": relative,
                             "error": "Structural parsing incomplete; read frozen source",
                             "locations": parsed["parse_errors"]})
        parsed["branches"] = assign_branch_ids(repository["repo_id"], relative, parsed["branches"])
        files.append({**parsed, "repo_id": repository["repo_id"], "path": relative,
                      "language": language, "line_count": len(path.read_bytes().splitlines()),
                      "preprocessor": [], "resource_signals": [],
                      "fallback_analysis": None if parsed["parse_complete"] else "raw_text"})
    return {"files": files, "file_count": len(files), "parse_failures": failures,
            "missing_dependencies": sorted(missing), "structural_parse_complete": not failures}
