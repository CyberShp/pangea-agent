from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .cpp_symbols import externally_callable_definitions

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
HEADER_SUFFIXES = {".h", ".hpp", ".hh"}
CODE_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES
CONTEXT_SUFFIXES = {".md", ".rst", ".txt", ".sh", ".py", ".json", ".yaml", ".yml"}
IGNORED_PARTS = {".git", "build", "dist", "third_party", "node_modules", "__pycache__", ".pangea"}
COMMON_FUNCTIONS = {"main", "free", "calloc", "malloc", "memcpy", "memset", "strcmp", "strlen"}
GENERIC_TERMS = {"analysis", "feature", "include", "module", "source", "test", "功能", "模块", "分析"}
MAX_TARGET_CONTEXT_PER_GROUP = 8
MAX_CALLER_CONTEXT_FILES_PER_GROUP = 24
MAX_CALLER_CONTEXT_DEPTH = 8
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
_DEF_RE = re.compile(r"^[ \t]*(?!if\b|for\b|while\b|switch\b)(?:[A-Za-z_]\w*[ \t*]+)+([A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t\n]*\{", re.MULTILINE)


def expand_analysis_scope(repositories: list[dict], requested_scopes: list[str], *, target: str, focus: list[str]) -> dict:
    normalized_scopes = [_normalize(value) for value in requested_scopes or ["."]]
    requested_groups = _group_requested_scopes(normalized_scopes)
    domain_terms = _domain_terms(target, focus, normalized_scopes)
    groups: list[dict] = []
    context_files: list[dict] = []
    added_files: list[dict] = []
    caller_context_truncations: list[dict] = []

    for repository in repositories:
        repo_id = repository["repo_id"]
        root = Path(repository["source_root"])
        code_files = list(_iter_files(root, CODE_SUFFIXES))
        code_paths = {path: _relative(path, root) for path in code_files}
        function_pointer_implementations = _function_pointer_implementation_index(code_paths)
        explicit_paths = {relative for relative in code_paths.values() if any(_inside_scope(relative, scope) for scope in normalized_scopes)}
        declarations_by_group = [_declared_symbols(scopes, root) for scopes in requested_groups]
        wanted_definitions = set().union(*declarations_by_group) if declarations_by_group else set()
        definition_symbols_by_path = _definition_symbols_by_path(wanted_definitions, code_paths)
        repo_groups = []
        for group_index, scopes in enumerate(requested_groups):
            paths = {relative for relative in code_paths.values() if any(_inside_scope(relative, scope) for scope in scopes)}
            companions = _companion_paths(scopes, code_paths.values())
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
            paths.update(companions)
            paths.update(definitions)
            inline_headers = _called_inline_headers(
                (root / relative for relative in paths),
                code_paths,
            )
            for relative, symbols in sorted(inline_headers.items()):
                context_files.append({
                    "repo_id": repo_id,
                    "path": relative,
                    "reason": f"direct_inline_dependency:{','.join(sorted(symbols)[:5])}",
                })
            pointer_implementations = _called_function_pointer_implementations(
                (root / relative for relative in paths),
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
            repo_groups.append({
                "repo_id": repo_id,
                "requested_scope": list(scopes),
                "code_paths": sorted(paths),
                "context_paths": sorted(set(pointer_implementations) | set(inline_headers)),
            })

        repo_groups = _merge_overlapping_groups(repo_groups)
        owned = {path for group in repo_groups for path in group["code_paths"]}
        symbols_by_group = [
            _exported_symbols((root / path for path in group["code_paths"]), domain_terms)
            for group in repo_groups
        ]
        all_symbols = set().union(*symbols_by_group) if symbols_by_group else set()
        target_context_candidates: list[list[tuple[int, str]]] = [[] for _ in repo_groups]

        caller_records, truncations = _transitive_caller_context(
            repo_id,
            root,
            code_files,
            code_paths,
            repo_groups,
            symbols_by_group,
            owned,
        )
        context_files.extend(caller_records)
        caller_context_truncations.extend(truncations)

        for path in _iter_files(root, CONTEXT_SUFFIXES):
            relative = _relative(path, root)
            text = path.read_text(encoding="utf-8", errors="replace")
            calls = set(_CALL_RE.findall(text)) & all_symbols
            group_index = _select_group(relative, calls, repo_groups, symbols_by_group)
            if calls:
                record = {"repo_id": repo_id, "path": relative, "reason": f"direct_reference:{','.join(sorted(calls)[:5])}" if calls else "target_context"}
                repo_groups[group_index]["context_paths"].append(relative)
                context_files.append(record)
            elif owned and _matches_domain(relative, text, domain_terms):
                score = _target_context_score(relative, text, domain_terms)
                if score >= 10:
                    target_context_candidates[group_index].append((score, relative))

        for group_index, candidates in enumerate(target_context_candidates):
            selected = sorted(candidates, key=lambda item: (-item[0], item[1]))[:MAX_TARGET_CONTEXT_PER_GROUP]
            for _, relative in selected:
                repo_groups[group_index]["context_paths"].append(relative)
                context_files.append({"repo_id": repo_id, "path": relative, "reason": "target_context"})

        for group in repo_groups:
            group["code_paths"] = sorted(dict.fromkeys(group["code_paths"]))
            group["context_paths"] = sorted(dict.fromkeys(group["context_paths"]))
        groups.extend(repo_groups)

    return {
        "requested_scope": normalized_scopes,
        "groups": groups,
        "context_files": _unique_records(context_files),
        "added_files": _unique_records(added_files),
        "caller_context_truncations": caller_context_truncations,
        "boundary": "source_scope = explicit scope + declared implementations; context_scope = inline/function-pointer dependencies + bounded transitive callers + target-related config/docs/tests; caller budgets are resource guards, not semantic completion",
    }


def _transitive_caller_context(
    repo_id: str,
    root: Path,
    code_files: list[Path],
    code_paths: dict[Path, str],
    repo_groups: list[dict],
    symbols_by_group: list[set[str]],
    owned: set[str],
) -> tuple[list[dict], list[dict]]:
    """Add bounded read-only caller context without changing source ownership."""

    texts = {
        code_paths[path]: path.read_text(encoding="utf-8", errors="replace")
        for path in code_files
    }
    calls_by_path = {
        relative: set(_CALL_RE.findall(text))
        for relative, text in texts.items()
    }
    candidate_paths = sorted(
        relative
        for relative in texts
        if relative not in owned
        and PurePosixPath(relative).suffix.lower() in SOURCE_SUFFIXES
    )
    records: list[dict] = []
    truncations: list[dict] = []

    for group_index, group in enumerate(repo_groups):
        frontier = set(symbols_by_group[group_index])
        reachable_symbols = set(frontier)
        selected_paths: set[str] = set()
        depth = 1
        budget_hit = False

        while frontier and depth <= MAX_CALLER_CONTEXT_DEPTH:
            matched: list[tuple[str, set[str]]] = []
            for relative in candidate_paths:
                if relative in selected_paths:
                    continue
                calls = calls_by_path.get(relative, set()) & frontier
                if calls:
                    matched.append((relative, calls))
            if not matched:
                break

            remaining = MAX_CALLER_CONTEXT_FILES_PER_GROUP - len(selected_paths)
            if remaining <= 0:
                budget_hit = True
                break
            if len(matched) > remaining:
                matched = matched[:remaining]
                budget_hit = True

            next_frontier: set[str] = set()
            for relative, calls in matched:
                selected_paths.add(relative)
                group["context_paths"].append(relative)
                records.append({
                    "repo_id": repo_id,
                    "path": relative,
                    "reason": f"caller_depth_{depth}:{','.join(sorted(calls)[:5])}",
                })
                new_symbols = (
                    externally_callable_definitions(root / relative)
                    - reachable_symbols
                )
                next_frontier.update(new_symbols)
                reachable_symbols.update(new_symbols)

            if budget_hit:
                break
            frontier = next_frontier
            depth += 1

        if budget_hit:
            truncations.append({
                "repo_id": repo_id,
                "requested_scope": list(group.get("requested_scope", [])),
                "reason": "caller_file_budget",
                "limit": MAX_CALLER_CONTEXT_FILES_PER_GROUP,
                "selected_count": len(selected_paths),
                "depth_reached": depth,
            })
        elif frontier and depth > MAX_CALLER_CONTEXT_DEPTH:
            truncations.append({
                "repo_id": repo_id,
                "requested_scope": list(group.get("requested_scope", [])),
                "reason": "caller_depth_budget",
                "limit": MAX_CALLER_CONTEXT_DEPTH,
                "selected_count": len(selected_paths),
                "depth_reached": MAX_CALLER_CONTEXT_DEPTH,
            })

    return records, truncations


def _iter_files(root: Path, suffixes: set[str]):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in suffixes and not any(part in IGNORED_PARTS for part in path.relative_to(root).parts):
            yield path


def _group_requested_scopes(scopes: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    file_groups: dict[tuple[str, str], list[str]] = {}
    for scope in scopes:
        path = PurePosixPath(scope)
        if path.suffix.lower() in CODE_SUFFIXES:
            key = (str(path.parent), path.stem)
            if key not in file_groups:
                file_groups[key] = []
                groups.append(file_groups[key])
            file_groups[key].append(scope)
        else:
            groups.append([scope])
    return groups


def _companion_paths(scopes: list[str], code_paths) -> set[str]:
    keys = {(str(path.parent), path.stem) for scope in scopes for path in [PurePosixPath(scope)] if path.suffix.lower() in CODE_SUFFIXES}
    if not keys:
        return set()
    return {relative for relative in code_paths for path in [PurePosixPath(relative)] if (str(path.parent), path.stem) in keys and path.suffix.lower() in CODE_SUFFIXES}


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
        calls = set(_CALL_RE.findall(text))
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


def _exported_symbols(paths, domain_terms: tuple[str, ...]) -> set[str]:
    symbols: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in SOURCE_SUFFIXES:
            symbols.update(externally_callable_definitions(path))
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
