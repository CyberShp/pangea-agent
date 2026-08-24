from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .lua_scope import expand_lua_context
from .source_languages import (
    CODE_SUFFIXES,
    C_CPP_HEADER_SUFFIXES,
    C_CPP_SOURCE_SUFFIXES,
    C_CPP_SUFFIXES,
    LANGUAGE_CAPABILITIES,
    LUA_SUFFIXES,
)

SOURCE_SUFFIXES = C_CPP_SOURCE_SUFFIXES
HEADER_SUFFIXES = C_CPP_HEADER_SUFFIXES
CONTEXT_SUFFIXES = {".md", ".rst", ".txt", ".sh", ".py", ".json", ".yaml", ".yml"}
HARD_IGNORED_PARTS = {".git", ".pangea", "__pycache__"}
SOFT_IGNORED_PARTS = {"build", "dist", "third_party", "node_modules"}
DEFAULT_IGNORED_PARTS = HARD_IGNORED_PARTS | SOFT_IGNORED_PARTS
COMMON_FUNCTIONS = {"main", "free", "calloc", "malloc", "memcpy", "memset", "strcmp", "strlen"}
GENERIC_TERMS = {"analysis", "feature", "include", "module", "source", "test", "功能", "模块", "分析"}
GENERIC_SOURCE_STEMS = {"common", "helper", "helpers", "stub", "stubs", "util", "utils"}
MAX_TARGET_CONTEXT_PER_GROUP = 8
MAX_DIRECT_CALLERS_PER_GROUP = 12
MAX_DIRECT_CALLER_LINES_PER_FILE = 800
MAX_DIRECT_CALLER_LINES_PER_GROUP = 1600
MAX_DIRECT_CALLEE_LINES_PER_FILE = 800
MAX_DIRECT_CALLEE_LINES_PER_GROUP = 1600
_CALL_RE = re.compile(r"\b([A-Za-z_]\w{5,})\s*\(")
_MEMBER_CALL_RE = re.compile(r"(?:->|\.)\s*([A-Za-z_]\w*)\s*\(")
_FUNCTION_POINTER_RE = re.compile(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\(")
_STRUCT_START_RE = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\s*\{")
_STRUCT_INITIALIZER_RE = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*=\s*\{")
_DESIGNATED_ASSIGNMENT_RE = re.compile(r"\.\s*([A-Za-z_]\w*)\s*=")
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
_INLINE_DEF_RE = re.compile(
    r"\bstatic\s+inline\b[\s\w*]*?\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{",
    re.MULTILINE,
)
_DECL_RE = re.compile(r"^[ \t]*(?:extern[ \t]+)?(?:[A-Za-z_]\w*[ \t*]+)+([A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t]*;", re.MULTILINE)
_DEF_RE = re.compile(
    r"^[ \t]*(?!if\b|for\b|while\b|switch\b|else\b|do\b)"
    r"(?:[A-Za-z_]\w*[ \t\n*]+)+([A-Za-z_]\w*)[ \t]*"
    r"\([^;{}#()]*\)[ \t\n]*\{",
    re.MULTILINE,
)
_SCOPE_EXPANDERS = {"lua": expand_lua_context}


def expand_analysis_scope(repositories: list[dict], requested_scopes: list[str], *, target: str, focus: list[str]) -> dict:
    normalized_scopes = [_normalize(value) for value in requested_scopes or ["."]]
    domain_terms = _domain_terms(target, focus, normalized_scopes)
    groups: list[dict] = []
    context_files: list[dict] = []
    added_files: list[dict] = []
    unresolved_dependencies: list[dict] = []
    resolved_dependencies: list[dict] = []

    for repository in repositories:
        repo_id = repository["repo_id"]
        root = Path(repository["source_root"])
        code_files = list(_iter_files(root, CODE_SUFFIXES))
        code_files.extend(_explicit_soft_scope_files(root, normalized_scopes, CODE_SUFFIXES))
        code_files = sorted(set(code_files))
        code_paths = {path: _relative(path, root) for path in code_files}
        c_cpp_code_paths = {
            path: relative
            for path, relative in code_paths.items()
            if path.suffix.lower() in C_CPP_SUFFIXES
        }
        function_pointer_implementations = _function_pointer_implementation_index(c_cpp_code_paths)
        explicit_paths = {relative for relative in code_paths.values() if any(_inside_scope(relative, scope) for scope in normalized_scopes)}
        requested_groups = _group_requested_scopes(normalized_scopes, code_paths.values())
        declarations_by_group = [_declared_symbols(scopes, root) for scopes in requested_groups]
        wanted_definitions = set().union(*declarations_by_group) if declarations_by_group else set()
        definition_symbols_by_path = _definition_symbols_by_path(wanted_definitions, code_paths)
        definition_paths_by_symbol = _definition_paths_by_symbol(code_paths)
        repo_groups = []
        for group_index, scopes in enumerate(requested_groups):
            paths = {relative for relative in code_paths.values() if any(_inside_scope(relative, scope) for scope in scopes)}
            companions = _companion_paths(scopes, c_cpp_code_paths.values())
            declared = declarations_by_group[group_index]
            definitions: dict[str, list[str]] = {}
            for relative, symbols in definition_symbols_by_path.items():
                matched = sorted(declared & symbols)
                if matched:
                    definitions[relative] = matched
            for relative in sorted(companions - paths):
                if relative not in explicit_paths:
                    added_files.append({"repo_id": repo_id, "path": relative, "reason": "companion_source"})
            for relative, symbols in sorted(definitions.items()):
                if relative not in explicit_paths:
                    added_files.append({"repo_id": repo_id, "path": relative, "reason": f"declared_definition:{','.join(symbols[:5])}"})
                    context_files.append({
                        "repo_id": repo_id,
                        "path": relative,
                        "reason": f"declared_definition:{','.join(symbols[:5])}",
                    })
            companion_context = companions - explicit_paths
            for relative in sorted(companion_context):
                context_files.append({
                    "repo_id": repo_id,
                    "path": relative,
                    "reason": "companion_source",
                })
            paths.update(companions & explicit_paths)
            declared_context = set(definitions) - explicit_paths
            inline_headers = _called_inline_headers(
                (
                    root / relative
                    for relative in paths
                    if PurePosixPath(relative).suffix.lower() in C_CPP_SUFFIXES
                ),
                c_cpp_code_paths,
            )
            for relative, symbols in sorted(inline_headers.items()):
                context_files.append({
                    "repo_id": repo_id,
                    "path": relative,
                    "reason": f"direct_inline_dependency:{','.join(sorted(symbols)[:5])}",
                })
            pointer_implementations = _called_function_pointer_implementations(
                (
                    root / relative
                    for relative in paths
                    if PurePosixPath(relative).suffix.lower() in C_CPP_SUFFIXES
                ),
                function_pointer_implementations,
            )
            for relative, members in sorted(pointer_implementations.items()):
                ordered_members = sorted(
                    members,
                    key=lambda member: ("remove" not in member, not member.startswith("group_impl_"), member),
                )
                context_files.append({
                    "repo_id": repo_id,
                    "path": relative,
                    "reason": f"function_pointer_implementation:{','.join(ordered_members[:5])}",
                })
            direct_callees = _called_direct_callees(
                (root / relative for relative in paths),
                definition_paths_by_symbol,
                set(paths),
                explicit_paths,
            )
            selected_direct_callees: dict[str, set[str]] = {}
            selected_callee_lines = 0
            for relative, symbols in sorted(
                direct_callees.items(),
                key=lambda item: (
                    -len(item[1]),
                    _line_count(root / item[0]),
                    item[0],
                ),
            ):
                line_count = _line_count(root / relative)
                if (
                    line_count > MAX_DIRECT_CALLEE_LINES_PER_FILE
                    or selected_callee_lines + line_count > MAX_DIRECT_CALLEE_LINES_PER_GROUP
                ):
                    unresolved_dependencies.append({
                        "repo_id": repo_id,
                        "path": relative,
                        "reason": "direct_callee_context_too_large",
                        "symbols": sorted(symbols),
                        "line_count": line_count,
                    })
                    continue
                selected_direct_callees[relative] = symbols
                selected_callee_lines += line_count
            direct_callees = selected_direct_callees
            for relative, symbols in sorted(direct_callees.items()):
                context_files.append({
                    "repo_id": repo_id,
                    "path": relative,
                    "reason": f"direct_callee:{','.join(sorted(symbols)[:5])}",
                })
            repo_groups.append({
                "repo_id": repo_id,
                "requested_scope": list(scopes),
                "code_paths": sorted(paths),
                "context_paths": sorted(
                    companion_context
                    | declared_context
                    | set(pointer_implementations)
                    | set(inline_headers)
                    | set(direct_callees)
                ),
            })

        repo_groups = _merge_overlapping_groups(repo_groups)
        owned = {path for group in repo_groups for path in group["code_paths"]}
        symbols_by_group = [
            _exported_symbols(
                (
                    root / path
                    for path in group["code_paths"]
                    if PurePosixPath(path).suffix.lower() in C_CPP_SUFFIXES
                ),
                domain_terms,
            )
            for group in repo_groups
        ]
        all_symbols = set().union(*symbols_by_group) if symbols_by_group else set()
        direct_caller_candidates: list[list[tuple[int, int, str, list[str]]]] = [
            [] for _ in repo_groups
        ]
        target_context_candidates: list[list[tuple[int, str]]] = [[] for _ in repo_groups]

        for path in code_files:
            if path.suffix.lower() not in C_CPP_SUFFIXES:
                continue
            relative = code_paths[path]
            if relative in owned:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            calls = _called_symbols(text) & all_symbols
            if not calls:
                continue
            group_index = _select_group(relative, calls, repo_groups, symbols_by_group)
            direct_caller_candidates[group_index].append(
                (len(calls), len(text.splitlines()), relative, sorted(calls))
            )

        for group_index, candidates in enumerate(direct_caller_candidates):
            selected: list[tuple[int, int, str, list[str]]] = []
            selected_lines = 0
            for candidate in sorted(
                candidates, key=lambda item: (-item[0], item[1], item[2])
            ):
                _, line_count, _, _ = candidate
                if line_count > MAX_DIRECT_CALLER_LINES_PER_FILE:
                    continue
                if selected_lines + line_count > MAX_DIRECT_CALLER_LINES_PER_GROUP:
                    continue
                selected.append(candidate)
                selected_lines += line_count
                if len(selected) == MAX_DIRECT_CALLERS_PER_GROUP:
                    break
            for _, _, relative, calls in selected:
                repo_groups[group_index]["context_paths"].append(relative)
                context_files.append({
                    "repo_id": repo_id,
                    "path": relative,
                    "reason": f"direct_caller:{','.join(calls[:5])}",
                })

        for path in _iter_files(root, CONTEXT_SUFFIXES):
            relative = _relative(path, root)
            text = path.read_text(encoding="utf-8", errors="replace")
            calls = set(_CALL_RE.findall(text)) & all_symbols
            group_index = _select_group(relative, calls, repo_groups, symbols_by_group)
            if calls:
                record = {"repo_id": repo_id, "path": relative, "reason": f"direct_reference:{','.join(sorted(calls)[:5])}" if calls else "target_context"}
                repo_groups[group_index]["context_paths"].append(relative)
                context_files.append(record)
            elif (
                owned
                and not _all_explicit_code_files(repo_groups[group_index]["requested_scope"])
                and _matches_domain(relative, text, domain_terms)
            ):
                score = _target_context_score(relative, text, domain_terms)
                if score >= 10:
                    target_context_candidates[group_index].append((score, relative))

        for capability in LANGUAGE_CAPABILITIES:
            if capability.scope_provider is None:
                continue
            expander = _SCOPE_EXPANDERS.get(capability.scope_provider)
            if expander is None:
                raise ValueError(
                    f"unsupported scope provider: {capability.scope_provider}"
                )
            provider_context, provider_unresolved, provider_resolved = expander(
                root, code_paths, repo_groups
            )
            context_files.extend(provider_context)
            resolved_dependencies.extend(provider_resolved)
            unresolved_dependencies.extend(
                {"repo_id": repo_id, **item}
                for item in provider_unresolved
            )

        for group_index, candidates in enumerate(target_context_candidates):
            selected = sorted(candidates, key=lambda item: (-item[0], item[1]))[:MAX_TARGET_CONTEXT_PER_GROUP]
            for _, relative in selected:
                repo_groups[group_index]["context_paths"].append(relative)
                context_files.append({"repo_id": repo_id, "path": relative, "reason": "target_context"})

        for group in repo_groups:
            group["code_paths"] = sorted(dict.fromkeys(group["code_paths"]))
            group["context_paths"] = sorted(dict.fromkeys(group["context_paths"]))
        repo_groups = _merge_mutually_dependent_lua_groups(repo_groups)
        groups.extend(repo_groups)

    return {
        "requested_scope": normalized_scopes,
        "groups": groups,
        "context_files": _unique_records(context_files),
        "added_files": _unique_records(added_files),
        "unresolved_dependencies": unresolved_dependencies,
        "resolved_dependencies": resolved_dependencies,
        "boundary": "source_scope = code files inside the requested scope; context_scope = companion/declared implementations outside the request + one-hop unique C/C++ direct callees + inline/function-pointer dependencies + bounded direct callers + direct Lua require dependencies/requirers + one framework-implementation require hop + target-related config/docs/tests",
    }


def preflight_source_scopes(repositories: list[dict], requested_scopes: list[str]) -> list[str]:
    normalized_scopes = [_normalize(value) for value in requested_scopes or ["."]]
    missing: list[str] = []
    for original, scope in zip(requested_scopes or ["."], normalized_scopes):
        if not any(_scope_path(repository, scope) is not None for repository in repositories):
            missing.append(original)
    if missing:
        rendered = ", ".join(repr(value) for value in missing)
        raise ValueError(
            "source_scope 在所选仓库中不存在："
            f"{rendered}。路径必须是仓库根目录下使用 / 分隔的相对路径。"
        )
    return normalized_scopes


def _iter_files(root: Path, suffixes: set[str]):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in suffixes and not any(part in DEFAULT_IGNORED_PARTS for part in path.relative_to(root).parts):
            yield path


def _explicit_soft_scope_files(root: Path, scopes: list[str], suffixes: set[str]):
    for scope in scopes:
        parts = PurePosixPath(scope).parts
        soft_index = next(
            (index for index, part in enumerate(parts) if part in SOFT_IGNORED_PARTS),
            None,
        )
        if soft_index is None:
            continue
        explicit_root = root.joinpath(*parts[: soft_index + 1])
        if not explicit_root.is_dir():
            continue
        for path in explicit_root.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in suffixes
                and not any(
                    part in HARD_IGNORED_PARTS
                    for part in path.relative_to(root).parts
                )
            ):
                yield path


def _scope_path(repository: dict, scope: str) -> Path | None:
    root = Path(repository["source_root"]).resolve()
    candidate = root.joinpath(*PurePosixPath(scope).parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def _all_explicit_code_files(scopes: list[str]) -> bool:
    return bool(scopes) and all(
        PurePosixPath(scope).suffix.lower() in CODE_SUFFIXES
        for scope in scopes
    )


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _group_requested_scopes(scopes: list[str], code_paths=()) -> list[list[str]]:
    groups: list[list[str]] = []
    file_groups: dict[tuple[str, str, str], list[str]] = {}

    def add_file(scope: str) -> None:
        path = PurePosixPath(scope)
        family = "c_cpp" if path.suffix.lower() in C_CPP_SUFFIXES else path.suffix.lower()
        key = (str(path.parent), path.stem, family)
        if key not in file_groups:
            file_groups[key] = []
            groups.append(file_groups[key])
        if scope not in file_groups[key]:
            file_groups[key].append(scope)

    available_paths = sorted(set(code_paths))
    for scope in scopes:
        path = PurePosixPath(scope)
        if path.suffix.lower() in CODE_SUFFIXES:
            add_file(scope)
            continue
        expanded = [relative for relative in available_paths if _inside_scope(relative, scope)]
        if not expanded:
            groups.append([scope])
            continue
        for relative in expanded:
            add_file(relative)
    return groups


def _companion_paths(scopes: list[str], code_paths) -> set[str]:
    keys = {(str(path.parent), path.stem) for scope in scopes for path in [PurePosixPath(scope)] if path.suffix.lower() in C_CPP_SUFFIXES}
    if not keys:
        return set()
    return {relative for relative in code_paths for path in [PurePosixPath(relative)] if (str(path.parent), path.stem) in keys and path.suffix.lower() in C_CPP_SUFFIXES}


def _declared_symbols(scopes: list[str], root: Path) -> set[str]:
    declared: set[str] = set()
    for scope in scopes:
        relative = PurePosixPath(scope)
        if relative.suffix.lower() not in HEADER_SUFFIXES:
            continue
        header = root.joinpath(*relative.parts)
        if not header.is_file():
            continue
        text = header.read_text(encoding="utf-8", errors="replace")
        declared.update(name for name in _DECL_RE.findall(text) if name not in COMMON_FUNCTIONS)
    return declared


def _definition_symbols_by_path(wanted: set[str], code_paths: dict[Path, str]) -> dict[str, set[str]]:
    if not wanted:
        return {}
    definitions: dict[str, set[str]] = {}
    for path, relative in code_paths.items():
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        matched = wanted & set(_DEF_RE.findall(text))
        if matched:
            definitions[relative] = matched
    return definitions


def _definition_paths_by_symbol(code_paths: dict[Path, str]) -> dict[str, set[str]]:
    definitions: dict[str, set[str]] = {}
    for path, relative in code_paths.items():
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for symbol in _DEF_RE.findall(text):
            if symbol not in COMMON_FUNCTIONS:
                definitions.setdefault(symbol, set()).add(relative)
    return definitions


def _called_direct_callees(
    source_paths,
    definition_paths_by_symbol: dict[str, set[str]],
    owned_paths: set[str],
    preferred_paths: set[str],
) -> dict[str, set[str]]:
    called: set[str] = set()
    for path in source_paths:
        if path.suffix.lower() not in C_CPP_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        called.update(_called_symbols(text))
    callees: dict[str, set[str]] = {}
    for symbol in called:
        candidates = definition_paths_by_symbol.get(symbol, set())
        preferred = candidates & preferred_paths
        if len(preferred) == 1:
            candidates = preferred
        if len(candidates) != 1:
            continue
        target = next(iter(candidates))
        if target not in owned_paths:
            callees.setdefault(target, set()).add(symbol)
    return callees


def _struct_bodies(text: str):
    for match in _STRUCT_START_RE.finditer(text):
        depth = 1
        index = match.end()
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        if depth == 0:
            yield match.group(1), text[match.end():index - 1]


def _function_pointer_implementation_index(code_paths: dict[Path, str]) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = {}
    texts: dict[Path, str] = {}
    for path in code_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        texts[path] = text
        for struct_name, body in _struct_bodies(text):
            for member in _FUNCTION_POINTER_RE.findall(body):
                owners.setdefault(member, set()).add(struct_name)

    implementations: dict[str, set[str]] = {}
    for path, relative in code_paths.items():
        if path.suffix.lower() not in SOURCE_SUFFIXES or PurePosixPath(relative).parts[0] in {"test", "examples"}:
            continue
        text = texts[path]
        initialized_structs = set(_STRUCT_INITIALIZER_RE.findall(text))
        assigned_members = set(_DESIGNATED_ASSIGNMENT_RE.findall(text))
        for member in assigned_members & owners.keys():
            if len(owners[member]) == 1 and initialized_structs & owners[member]:
                implementations.setdefault(member, set()).add(relative)
    return implementations


def _called_function_pointer_implementations(paths, index: dict[str, set[str]]) -> dict[str, set[str]]:
    members: set[str] = set()
    for path in paths:
        if path.is_file():
            members.update(_MEMBER_CALL_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    implementations: dict[str, set[str]] = {}
    for member in members:
        for relative in index.get(member, set()):
            implementations.setdefault(relative, set()).add(member)
    return implementations


def _called_inline_headers(paths, code_paths: dict[Path, str]) -> dict[str, set[str]]:
    by_relative = {relative: path for path, relative in code_paths.items()}
    dependencies: dict[str, set[str]] = {}
    for source_path in paths:
        if not source_path.is_file():
            continue
        text = source_path.read_text(encoding="utf-8", errors="replace")
        calls = _called_symbols(text)
        source_relative = code_paths.get(source_path)
        if source_relative is None:
            continue
        for include in _INCLUDE_RE.findall(text):
            candidates = {
                include,
                f"include/{include}",
                str(PurePosixPath(source_relative).parent / include),
            }
            matches = [by_relative[candidate] for candidate in candidates if candidate in by_relative]
            if len(matches) != 1:
                continue
            header_path = matches[0]
            header_relative = code_paths[header_path]
            inline_symbols = set(_INLINE_DEF_RE.findall(
                header_path.read_text(encoding="utf-8", errors="replace")
            ))
            used = calls & inline_symbols
            if used:
                dependencies.setdefault(header_relative, set()).update(used)
    return dependencies


def _called_symbols(text: str) -> set[str]:
    return set(_CALL_RE.findall(text)) - set(_DEF_RE.findall(text))


def _merge_overlapping_groups(groups: list[dict]) -> list[dict]:
    merged = [{**group, "requested_scope": list(group["requested_scope"]), "code_paths": list(group["code_paths"]), "context_paths": list(group["context_paths"])} for group in groups]
    changed = True
    while changed:
        changed = False
        for left in range(len(merged)):
            left_paths = set(merged[left]["code_paths"])
            for right in range(left + 1, len(merged)):
                if not left_paths.intersection(merged[right]["code_paths"]):
                    continue
                merged[left]["requested_scope"] = sorted(dict.fromkeys(merged[left]["requested_scope"] + merged[right]["requested_scope"]))
                merged[left]["code_paths"] = sorted(dict.fromkeys(merged[left]["code_paths"] + merged[right]["code_paths"]))
                merged[left]["context_paths"] = sorted(dict.fromkeys(merged[left]["context_paths"] + merged[right]["context_paths"]))
                merged.pop(right)
                changed = True
                break
            if changed:
                break
    return merged


def _merge_mutually_dependent_lua_groups(groups: list[dict]) -> list[dict]:
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
            left_lua = {
                path for path in merged[left]["code_paths"]
                if PurePosixPath(path).suffix.lower() in LUA_SUFFIXES
            }
            for right in range(left + 1, len(merged)):
                right_lua = {
                    path for path in merged[right]["code_paths"]
                    if PurePosixPath(path).suffix.lower() in LUA_SUFFIXES
                }
                if not left_lua or not right_lua:
                    continue
                if not (
                    left_lua.intersection(merged[right]["context_paths"])
                    and right_lua.intersection(merged[left]["context_paths"])
                ):
                    continue
                code_paths = sorted(dict.fromkeys(
                    merged[left]["code_paths"] + merged[right]["code_paths"]
                ))
                merged[left]["requested_scope"] = sorted(dict.fromkeys(
                    merged[left]["requested_scope"] + merged[right]["requested_scope"]
                ))
                merged[left]["code_paths"] = code_paths
                merged[left]["context_paths"] = sorted(
                    set(merged[left]["context_paths"] + merged[right]["context_paths"])
                    - set(code_paths)
                )
                merged.pop(right)
                changed = True
                break
            if changed:
                break
    return merged


def _exported_symbols(paths, domain_terms: tuple[str, ...]) -> set[str]:
    symbols: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _DEF_RE.finditer(text):
            name = match.group(1)
            declaration = match.group(0).split("(", 1)[0]
            if name not in COMMON_FUNCTIONS and not re.search(r"\bstatic\b", declaration):
                symbols.add(name)
        if path.suffix.lower() in HEADER_SUFFIXES:
            for match in _DECL_RE.finditer(text):
                name = match.group(1)
                declaration = match.group(0).lower()
                if name not in COMMON_FUNCTIONS and (_matches_domain(name, declaration, domain_terms) or _matches_domain(str(path), declaration, domain_terms)):
                    symbols.add(name)
    return symbols


def _select_group(relative: str, calls: set[str], groups: list[dict], symbols_by_group: list[set[str]]) -> int:
    scores = [len(calls & symbols) for symbols in symbols_by_group]
    if max(scores, default=0) > 0:
        return scores.index(max(scores))
    path_parts = relative.split("/")
    proximity = []
    for group in groups:
        scope_scores = []
        for scope in group["requested_scope"]:
            scope_parts = scope.split("/")
            scope_scores.append(sum(a == b for a, b in zip(path_parts, scope_parts)))
        proximity.append(max(scope_scores, default=0))
    return proximity.index(max(proximity)) if proximity else 0


def _domain_terms(target: str, focus: list[str], requested_scopes: list[str] | None = None) -> tuple[str, ...]:
    scope_terms: set[str] = set()
    for scope in requested_scopes or []:
        path = PurePosixPath(scope)
        if path.suffix.lower() not in CODE_SUFFIXES:
            continue
        stem = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
        parent = re.sub(r"[^a-z0-9]+", "_", path.parent.name.lower()).strip("_")
        if stem in {"tcp", "conn", "qpair", "transport"} and parent:
            stem = f"{parent}_{stem}"
        elif stem in GENERIC_SOURCE_STEMS and parent:
            stem = parent
        if stem:
            scope_terms.add(stem)
    if scope_terms:
        variants = set(scope_terms)
        variants.update(term.replace("_", "-") for term in scope_terms if "_" in term)
        variants.update(term.replace("_", " ") for term in scope_terms if "_" in term)
        if "chap" in target.lower() or any("chap" in value.lower() for value in focus):
            variants.update({"dhchap", "dh-hmac-chap", "dh_hmac_chap"})
        return tuple(sorted(variants))

    source = " ".join([target, *focus]).lower()
    terms = {value for value in re.findall(r"[a-z0-9_+-]{3,}|[\u4e00-\u9fff]{2,}", source) if value not in GENERIC_TERMS}
    if any("chap" in value for value in terms):
        terms.update({"dhchap", "dh-hmac-chap", "dh_hmac_chap"})
    compact_target = re.sub(r"[^a-z0-9]+", "_", target.lower()).strip("_")
    if compact_target:
        terms.add(compact_target)
    specific = {term for term in terms if term not in {"nvme", "tcp", "iscsi"}}
    return tuple(sorted(specific or terms))


def _matches_domain(relative: str, text: str, terms: tuple[str, ...]) -> bool:
    sample = f"{relative}\n{text}".lower()
    dhchap_terms = tuple(term for term in terms if term in {"dhchap", "dh-hmac-chap", "dh_hmac_chap"})
    candidates = dhchap_terms or terms
    return bool(candidates) and any(term in sample for term in candidates)


def _target_context_score(relative: str, text: str, terms: tuple[str, ...]) -> int:
    path = relative.lower().replace("-", "_")
    sample = text[:20000].lower()
    score = 0
    for term in terms:
        normalized = term.lower().replace("-", "_").replace(" ", "_")
        if normalized and normalized in path:
            score += 10
        score += min(3, sample.count(term.lower()))
    return score


def _normalize(value: str) -> str:
    return value.replace("\\", "/").strip("/") or "."


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _inside_scope(path: str, scope: str) -> bool:
    return scope == "." or path == scope or path.startswith(f"{scope}/")


def _unique_records(records: list[dict]) -> list[dict]:
    unique: dict[tuple[str, str], dict] = {}
    for record in records:
        unique[(record["repo_id"], record["path"])] = record
    return sorted(unique.values(), key=lambda item: (item["repo_id"], item["path"]))
