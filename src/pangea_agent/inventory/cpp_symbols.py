from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_FUNCTION_RE = re.compile(
    r"^\s*(?P<static>static\s+)?(?:inline\s+)?[A-Za-z_][\w\s\*]+\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*\{"
)
_CALLABLE_FALLBACK_RE = re.compile(
    r"(?m)^[ \t]*(?P<static>static[ \t]+)?(?:inline[ \t]+)?"
    r"(?P<ret>[A-Za-z_][A-Za-z0-9_]*(?:[ \t*]+[A-Za-z_][A-Za-z0-9_]*)*[ \t*]*)"
    r"(?:\n[ \t]*|[ \t]+)"
    r"(?P<name>[A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t\n]*\{"
)
_CPP_SUFFIXES = {".cc", ".cpp", ".cxx", ".hpp", ".hh"}


class TreeSitterUnavailableError(RuntimeError):
    def __init__(self, packages: list[str]) -> None:
        self.packages = packages
        super().__init__(f"C/C++ structural parsing requires: {', '.join(packages)}")


def extract_functions(lines: list[str]) -> list[dict]:
    """Regex fallback retained for files that tree-sitter cannot parse."""
    results = []
    for idx, line in enumerate(lines, 1):
        match = _FUNCTION_RE.match(line)
        if match:
            results.append(
                {
                    "line": idx,
                    "symbol": match.group("name"),
                    "is_static": bool(match.group("static")),
                    "parser": "regex_fallback",
                }
            )
    return results


def externally_callable_definitions(path: Path) -> set[str]:
    """Return non-static function definitions using the same parser as inventory."""
    try:
        functions = parse_cpp_file(path)["functions"]
        return {
            str(item["symbol"])
            for item in functions
            if item.get("symbol") and item.get("symbol") != "<unknown>"
            and not item.get("is_static", False)
        }
    except TreeSitterUnavailableError:
        text = path.read_text(encoding="utf-8", errors="replace")
        return {
            match.group("name")
            for match in _CALLABLE_FALLBACK_RE.finditer(text)
            if not match.group("static")
        }


def _load_parser(path: Path):
    missing: list[str] = []
    try:
        from tree_sitter import Language, Parser
    except ImportError:
        raise TreeSitterUnavailableError(["tree-sitter", "tree-sitter-c", "tree-sitter-cpp"])
    try:
        if path.suffix.lower() in _CPP_SUFFIXES:
            import tree_sitter_cpp as grammar
            package = "tree-sitter-cpp"
        else:
            import tree_sitter_c as grammar
            package = "tree-sitter-c"
    except ImportError:
        missing.append("tree-sitter-cpp" if path.suffix.lower() in _CPP_SUFFIXES else "tree-sitter-c")
        raise TreeSitterUnavailableError(missing)
    language = Language(grammar.language())
    return Parser(language), package


def _walk(root) -> Any:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _first_identifier(node, source: bytes) -> str | None:
    for child in _walk(node):
        if child.type in {"identifier", "field_identifier", "operator_name", "destructor_name"}:
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return None


def _last_identifier(node, source: bytes) -> str | None:
    identifiers = [
        child
        for child in _walk(node)
        if child.type in {
            "identifier",
            "field_identifier",
            "operator_name",
            "destructor_name",
        }
    ]
    if not identifiers:
        return None
    child = identifiers[-1]
    return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")


def _function_name(node, source: bytes) -> str | None:
    for child in _walk(node):
        if child.type == "function_declarator":
            name = child.child_by_field_name("declarator")
            if name is not None:
                identifiers = [
                    part for part in _walk(name)
                    if part.type in {"identifier", "field_identifier", "operator_name", "destructor_name"}
                ]
                if identifiers:
                    value = identifiers[-1]
                    return source[value.start_byte:value.end_byte].decode("utf-8", errors="replace")
    return _first_identifier(node, source)


def _function_is_static(node, source: bytes) -> bool:
    for child in node.children:
        if child.type != "storage_class_specifier":
            continue
        value = source[child.start_byte:child.end_byte].decode(
            "utf-8", errors="replace"
        )
        if value.strip() == "static":
            return True
    return False


def parse_cpp_file(path: Path) -> dict:
    """Parse a C/C++ file and retain syntax and preprocessor evidence."""
    source = path.read_bytes()
    parser, grammar_package = _load_parser(path)
    tree = parser.parse(source)
    functions: list[dict] = []
    branches: list[dict] = []
    preprocessor: list[dict] = []
    parse_errors: list[dict] = []
    types: list[dict] = []
    calls: list[dict] = []
    branch_kinds = {
        "if_statement": "if",
        "switch_statement": "switch",
        "case_statement": "case",
        "conditional_expression": "conditional",
        "for_statement": "for",
        "while_statement": "while",
        "do_statement": "do",
    }
    type_kinds = {"struct_specifier": "struct", "union_specifier": "union", "enum_specifier": "enum", "class_specifier": "class"}
    for node in _walk(tree.root_node):
        line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        if node.type == "function_definition":
            declarator = node.child_by_field_name("declarator") or node
            functions.append({
                "line": line,
                "end_line": end_line,
                "symbol": _function_name(declarator, source) or "<unknown>",
                "is_static": _function_is_static(node, source),
                "parser": "tree_sitter",
            })
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            symbol = _last_identifier(function, source) if function is not None else None
            if symbol:
                calls.append({"line": line, "symbol": symbol})
        if node.type in branch_kinds:
            branches.append({"line": line, "end_line": end_line, "kind": branch_kinds[node.type], "parser": "tree_sitter"})
        if node.type.startswith("preproc_if") or node.type in {"preproc_elif", "preproc_else", "preproc_def", "preproc_function_def"}:
            condition = source[node.start_byte:node.end_byte].splitlines()[0].decode("utf-8", errors="replace").strip()
            preprocessor.append({
                "line": line,
                "end_line": end_line,
                "kind": node.type,
                "condition": condition[:500],
            })
        if node.type in type_kinds:
            name = node.child_by_field_name("name")
            types.append({
                "line": line,
                "end_line": end_line,
                "kind": type_kinds[node.type],
                "symbol": (
                    source[name.start_byte:name.end_byte].decode("utf-8", errors="replace")
                    if name is not None else "<anonymous>"
                ),
            })
        if node.type == "ERROR" or node.is_missing:
            parse_errors.append({
                "line": line,
                "end_line": end_line,
                "kind": "missing" if node.is_missing else "syntax_error",
                "text": source[node.start_byte:min(node.end_byte, node.start_byte + 200)].decode("utf-8", errors="replace"),
            })
    return {
        "parser": "tree_sitter",
        "grammar_package": grammar_package,
        "has_error": tree.root_node.has_error,
        "functions": functions,
        "branches": branches,
        "preprocessor": preprocessor,
        "types": types,
        "calls": calls,
        "parse_errors": parse_errors,
    }
