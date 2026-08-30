from __future__ import annotations

from pathlib import Path
from typing import Any


class TreeSitterLuaUnavailableError(RuntimeError):
    def __init__(self, packages: list[str]) -> None:
        self.packages = packages
        super().__init__(f"Lua structural parsing requires: {', '.join(packages)}")


def _load_parser():
    try:
        from tree_sitter import Language, Parser
    except ImportError as exc:
        raise TreeSitterLuaUnavailableError(
            ["tree-sitter", "tree-sitter-lua"]
        ) from exc
    try:
        import tree_sitter_lua as grammar
    except ImportError as exc:
        raise TreeSitterLuaUnavailableError(["tree-sitter-lua"]) from exc
    return Parser(Language(grammar.language())), "tree-sitter-lua"


def _walk(root) -> Any:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )


def _inside_function(node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {"function_declaration", "function_definition"}:
            return True
        parent = parent.parent
    return False


def _arguments(node):
    arguments = node.child_by_field_name("arguments")
    return list(arguments.named_children) if arguments is not None else []


def _string_value(node, source: bytes) -> str | None:
    if node is None or node.type != "string":
        return None
    content = node.child_by_field_name("content")
    if content is not None:
        return _text(content, source)
    raw = _text(node, source)
    return raw[1:-1] if len(raw) >= 2 else ""


def _state_writes(node, source: bytes) -> list[dict]:
    if node.parent is not None and node.parent.type == "variable_declaration":
        return []
    variable_list = next(
        (child for child in node.named_children if child.type == "variable_list"),
        None,
    )
    if variable_list is None:
        return []
    scope = "function" if _inside_function(node) else "module"
    writes = []
    for target in variable_list.named_children:
        target_text = _text(target, source)
        writes.append(
            {
                "line": node.start_point[0] + 1,
                "target": target_text,
                "kind": (
                    "table_field"
                    if target.type in {"dot_index_expression", "bracket_index_expression"}
                    else "nonlocal_assignment"
                ),
                "scope": scope,
            }
        )
    return writes


def _function_symbol(node, source: bytes) -> str:
    name = node.child_by_field_name("name")
    if name is not None:
        return _text(name, source)

    parent = node.parent
    for _ in range(4):
        if parent is None:
            break
        if parent.type == "assignment_statement":
            variable_list = next(
                (
                    child
                    for child in parent.named_children
                    if child.type == "variable_list"
                ),
                None,
            )
            if variable_list is not None:
                return _text(variable_list, source).split(",", 1)[0].strip()
        parent = parent.parent
    return "<anonymous>"


def parse_lua_file(path: Path) -> dict:
    source = path.read_bytes()
    parser, grammar_package = _load_parser()
    tree = parser.parse(source)
    functions: list[dict] = []
    branches: list[dict] = []
    calls: list[dict] = []
    requires: list[dict] = []
    module_exports: list[dict] = []
    state_writes: list[dict] = []
    protected_calls: list[dict] = []
    coroutine_calls: list[dict] = []
    parse_errors: list[dict] = []
    branch_kinds = {
        "if_statement": "if",
        "elseif_statement": "elseif",
        "else_statement": "else",
        "for_statement": "for",
        "while_statement": "while",
        "repeat_statement": "repeat",
    }

    for node in _walk(tree.root_node):
        line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        if node.type in {"function_declaration", "function_definition"}:
            functions.append(
                {
                    "line": line,
                    "end_line": end_line,
                    "symbol": _function_symbol(node, source),
                    "parser": "tree_sitter",
                }
            )
        if node.type == "function_call":
            name = node.child_by_field_name("name")
            symbol = _text(name, source) if name is not None else None
            if symbol:
                calls.append({"line": line, "symbol": symbol})
                arguments = _arguments(node)
                if symbol == "require":
                    module = _string_value(arguments[0], source) if arguments else None
                    requires.append(
                        {
                            "line": line,
                            "module": module,
                            "expression": (
                                _text(arguments[0], source)[:200]
                                if arguments else "<missing>"
                            ),
                            "literal": module is not None,
                        }
                    )
                if symbol in {"pcall", "xpcall"} or symbol.endswith(
                    (".pcall", ":pcall", ".xpcall", ":xpcall")
                ):
                    protected_calls.append(
                        {
                            "line": line,
                            "call": symbol,
                            "target": (
                                _text(arguments[0], source)[:200]
                                if arguments else "<missing>"
                            ),
                        }
                    )
                if symbol.startswith(("coroutine.", "coroutine:")):
                    coroutine_calls.append({"line": line, "call": symbol})
        if node.type == "assignment_statement":
            state_writes.extend(_state_writes(node, source))
        if node.type == "return_statement" and not _inside_function(node):
            expression_list = next(
                (
                    child
                    for child in node.named_children
                    if child.type == "expression_list"
                ),
                None,
            )
            if expression_list is not None and expression_list.named_children:
                value = expression_list.named_children[0]
                module_exports.append(
                    {
                        "line": line,
                        "expression": _text(value, source)[:500],
                        "kind": value.type,
                    }
                )
        if node.type in branch_kinds:
            branches.append(
                {
                    "line": line,
                    "end_line": end_line,
                    "kind": branch_kinds[node.type],
                    "parser": "tree_sitter",
                }
            )
        if node.type == "ERROR" or node.is_missing:
            parse_errors.append(
                {
                    "line": line,
                    "end_line": end_line,
                    "kind": "missing" if node.is_missing else "syntax_error",
                    "text": _text(node, source)[:200],
                }
            )

    return {
        "parser": "tree_sitter",
        "grammar_package": grammar_package,
        "has_error": tree.root_node.has_error,
        "functions": functions,
        "branches": branches,
        "preprocessor": [],
        "types": [],
        "calls": calls,
        "requires": requires,
        "module_exports": module_exports,
        "state_writes": state_writes,
        "protected_calls": protected_calls,
        "coroutine_calls": coroutine_calls,
        "parse_errors": parse_errors,
    }
