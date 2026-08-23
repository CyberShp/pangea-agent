from __future__ import annotations

from pathlib import Path
from typing import Any


class LuaParserUnavailableError(RuntimeError):
    packages = ["tree-sitter", "tree-sitter-lua"]

    def __init__(self) -> None:
        super().__init__("Lua structural parsing requires: tree-sitter, tree-sitter-lua")


def _load_parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_lua as grammar
    except ImportError as exc:
        raise LuaParserUnavailableError() from exc
    return Parser(Language(grammar.language())), "tree-sitter-lua"


def _walk(root) -> Any:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _named_children(node) -> list:
    return [child for child in node.children if child.is_named]


def _field_or_named_child(node, field: str, excluded: set[str] | None = None):
    child = node.child_by_field_name(field)
    if child is not None:
        return child
    excluded = excluded or set()
    return next((item for item in _named_children(node) if item.type not in excluded), None)


def _assignment_target(node, source: bytes) -> str | None:
    values = node.parent
    if values is None or values.type != "expression_list":
        return None
    assignment = values.parent
    if assignment is None or assignment.type != "assignment_statement":
        return None
    variables = next(
        (child for child in _named_children(assignment) if child.type == "variable_list"),
        None,
    )
    if variables is None:
        return None
    direct_values = _named_children(values)
    direct_variables = _named_children(variables)
    value_index = next(
        (
            index
            for index, value in enumerate(direct_values)
            if value.start_byte == node.start_byte and value.end_byte == node.end_byte
        ),
        None,
    )
    if value_index is None or value_index >= len(direct_variables):
        return None
    return _text(direct_variables[value_index], source).strip() or None


def _function_symbol(node, source: bytes) -> str:
    name = node.child_by_field_name("name")
    if name is None and node.type == "function_declaration":
        name = _field_or_named_child(
            node,
            "name",
            {"parameters", "body", "block", "function_definition"},
        )
    if name is not None:
        value = _text(name, source).strip()
        if value:
            return value
    if node.type == "function_definition" and node.parent is not None and node.parent.type == "field":
        field_name = node.parent.child_by_field_name("name")
        table = node.parent.parent
        if field_name is not None and table is not None and table.type == "table_constructor":
            table_name = _assignment_target(table, source)
            if table_name:
                return f"{table_name}.{_text(field_name, source).strip()}"
    assigned = _assignment_target(node, source)
    if assigned:
        return assigned
    return f"<anonymous@{node.start_point[0] + 1}>"


def _call_name(node, source: bytes) -> str:
    name = (
        node.child_by_field_name("name")
        or node.child_by_field_name("function")
        or node.child_by_field_name("prefix")
    )
    if name is None:
        name = _field_or_named_child(node, "name", {"arguments", "argument_list"})
    return _text(name, source).strip() if name is not None else ""


def _call_arguments(node):
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        arguments = next(
            (child for child in node.children if child.type in {"arguments", "argument_list"}),
            None,
        )
    return _named_children(arguments) if arguments is not None else []


def _literal_string(node, source: bytes) -> str | None:
    if node.type not in {"string", "string_literal"}:
        return None
    value = _text(node, source).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value.startswith("[[") and value.endswith("]]"):
        return value[2:-2]
    return None


def _receiver_and_method(callee: str) -> tuple[str, str]:
    for separator in (":", "."):
        if separator in callee:
            receiver, method = callee.rsplit(separator, 1)
            return receiver, method
    return "", callee


def _enclosing_function_symbols(node, source: bytes) -> tuple[str | None, str | None]:
    immediate = None
    named = None
    parent = node.parent
    while parent is not None:
        if parent.type in {"function_declaration", "function_definition"}:
            symbol = _function_symbol(parent, source)
            if immediate is None:
                immediate = symbol
            if not symbol.startswith("<anonymous@"):
                named = symbol
                break
        parent = parent.parent
    return immediate, named


def _normalize_signal_receiver(receiver: str, function_symbol: str | None) -> str:
    if not receiver.startswith("self.") or not function_symbol:
        return receiver
    owner, _ = _receiver_and_method(function_symbol)
    if not owner:
        return receiver
    return f"{owner}.{receiver.removeprefix('self.')}"


def parse_lua_file(path: Path) -> dict:
    source = path.read_bytes()
    parser, grammar_package = _load_parser()
    tree = parser.parse(source)
    functions: list[dict] = []
    branches: list[dict] = []
    imports: list[dict] = []
    calls: list[dict] = []
    returns: list[dict] = []
    parse_errors: list[dict] = []
    class_symbols: set[str] = set()
    signal_scopes: dict[str, str | None] = {}
    lifecycle_candidates: list[dict] = []
    framework_signals: list[dict] = []
    branch_kinds = {
        "if_statement": "if",
        "elseif_statement": "elseif",
        "elseif_clause": "elseif",
        "else_statement": "else",
        "else_clause": "else",
        "for_statement": "for",
        "numeric_for_statement": "for",
        "generic_for_statement": "for",
        "while_statement": "while",
        "repeat_statement": "repeat",
    }

    nodes = list(_walk(tree.root_node))
    for node in nodes:
        line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        if node.type in {"function_declaration", "function_definition"}:
            symbol = _function_symbol(node, source)
            functions.append({
                "line": line,
                "end_line": end_line,
                "symbol": symbol,
                "parser": "tree_sitter",
            })
            _, method = _receiver_and_method(symbol)
            if method in {"ctor", "pre_init", "init"}:
                lifecycle_candidates.append({
                    "kind": "class_lifecycle",
                    "line": line,
                    "end_line": end_line,
                    "symbol": symbol,
                    "related_lines": [line],
                })
        if node.type in branch_kinds:
            branches.append({
                "line": line,
                "end_line": end_line,
                "kind": branch_kinds[node.type],
                "parser": "tree_sitter",
            })
        if node.type == "function_call":
            callee = _call_name(node, source)
            arguments = _call_arguments(node)
            assigned_to = _assignment_target(node, source)
            call = {
                "line": line,
                "end_line": end_line,
                "callee": callee,
            }
            function_symbol, owner_function_symbol = _enclosing_function_symbols(node, source)
            if function_symbol:
                call["function_symbol"] = function_symbol
            if owner_function_symbol and owner_function_symbol != function_symbol:
                call["owner_function_symbol"] = owner_function_symbol
            if assigned_to:
                call["assigned_to"] = assigned_to
            calls.append(call)
            if callee == "require" and arguments:
                module = _literal_string(arguments[0], source)
                if module is not None:
                    imports.append({"line": line, "module": module, "resolved_path": None})
                else:
                    imports.append({
                        "line": line,
                        "module": None,
                        "expression": _text(arguments[0], source)[:200],
                        "resolved_path": None,
                    })
            if callee == "mc.class":
                symbol = assigned_to or f"<class@{line}>"
                class_symbols.add(symbol)
                framework_signals.append({
                    "kind": "class_declaration",
                    "line": line,
                    "end_line": end_line,
                    "symbol": symbol,
                    "related_lines": [line],
                })
            elif callee == "mc.signal":
                symbol = assigned_to or f"<signal@{line}>"
                signal_scopes[symbol] = function_symbol
                framework_signals.append({
                    "kind": "signal_declaration",
                    "line": line,
                    "end_line": end_line,
                    "symbol": symbol,
                    "related_lines": [line],
                })
        if node.type == "return_statement":
            function_symbol, owner_function_symbol = _enclosing_function_symbols(node, source)
            returned = {
                "line": line,
                "end_line": end_line,
                "statement": _text(node, source).strip(),
            }
            ancestor = node.parent
            while ancestor is not None and ancestor.type not in {
                "function_declaration", "function_definition"
            }:
                if ancestor.type in {"if_statement", "elseif_statement", "elseif_clause"}:
                    condition = ancestor.child_by_field_name("condition")
                    if condition is not None:
                        returned["guard"] = _text(condition, source).strip()
                    break
                ancestor = ancestor.parent
            if function_symbol:
                returned["function_symbol"] = function_symbol
            if owner_function_symbol and owner_function_symbol != function_symbol:
                returned["owner_function_symbol"] = owner_function_symbol
            returns.append(returned)

    for candidate in lifecycle_candidates:
        receiver, _ = _receiver_and_method(candidate["symbol"])
        if receiver in class_symbols:
            framework_signals.append(candidate)

    for call in calls:
        receiver, method = _receiver_and_method(call["callee"])
        framework_owner = call.get("owner_function_symbol") or call.get("function_symbol")
        receiver = _normalize_signal_receiver(receiver, framework_owner)
        signal_scope = signal_scopes.get(receiver, "<missing>")
        signal_matches = (
            signal_scope != "<missing>"
            and (
                "." in receiver
                or signal_scope is None
                or signal_scope == framework_owner
            )
        )
        if method == "emit" and (signal_matches or receiver.startswith("mc.")):
            framework_signals.append({
                "kind": "signal_emit",
                "line": call["line"],
                "end_line": call["end_line"],
                "symbol": receiver or call["callee"],
                "related_lines": [call["line"]],
            })
        elif method in {"connect", "subscribe", "register"} and signal_matches:
            framework_signals.append({
                "kind": "signal_callback",
                "line": call["line"],
                "end_line": call["end_line"],
                "symbol": receiver,
                "related_lines": [call["line"]],
            })

    for node in nodes:
        if node.type == "ERROR" or node.is_missing:
            parse_errors.append({
                "line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "kind": "missing" if node.is_missing else "syntax_error",
                "text": _text(node, source)[:200],
            })

    frameworks = ["openubmc"] if class_symbols or signal_scopes else []
    return {
        "parser": "tree_sitter",
        "grammar_package": grammar_package,
        "has_error": tree.root_node.has_error,
        "functions": functions,
        "branches": branches,
        "preprocessor": [],
        "types": [],
        "imports": imports,
        "calls": calls,
        "returns": returns,
        "frameworks": frameworks,
        "framework_signals": framework_signals,
        "parse_errors": parse_errors,
    }
