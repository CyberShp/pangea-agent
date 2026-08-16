from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
HEADER_SUFFIXES = {".h", ".hpp", ".hh"}
CODE_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES
CONTEXT_SUFFIXES = {".md", ".rst", ".txt", ".sh", ".py", ".json", ".yaml", ".yml"}
IGNORED_PARTS = {".git", "build", "dist", "third_party", "node_modules", "__pycache__", ".pangea"}
COMMON_FUNCTIONS = {"main", "free", "calloc", "malloc", "memcpy", "memset", "strcmp", "strlen"}
GENERIC_TERMS = {"analysis", "feature", "include", "module", "source", "test", "功能", "模块", "分析"}
_CALL_RE = re.compile(r"\b([A-Za-z_]\w{5,})\s*\(")
_DECL_RE = re.compile(r"^[ \t]*(?:extern[ \t]+)?(?:[A-Za-z_]\w*[ \t*]+)+([A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t]*;", re.MULTILINE)
_DEF_RE = re.compile(r"^[ \t]*(?!if\b|for\b|while\b|switch\b)(?:[A-Za-z_]\w*[ \t*]+)+([A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t\n]*\{", re.MULTILINE)


def expand_analysis_scope(repositories: list[dict], requested_scopes: list[str], *, target: str, focus: list[str]) -> dict:
    normalized_scopes = [_normalize(value) for value in requested_scopes or ["."]]
    requested_groups = _group_requested_scopes(normalized_scopes)
    domain_terms = _domain_terms(target, focus, normalized_scopes)
    groups: list[dict] = []
    context_files: list[dict] = []
    added_files: list[dict] = []

    for repository in repositories:
        repo_id = repository["repo_id"]
        root = Path(repository["source_root"])
        code_files = list(_iter_files(root, CODE_SUFFIXES))
        code_paths = {path: _relative(path, root) for path in code_files}
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
            repo_groups.append({"repo_id": repo_id, "requested_scope": list(scopes), "code_paths": sorted(paths), "context_paths": []})

        repo_groups = _merge_overlapping_groups(repo_groups)
        owned = {path for group in repo_groups for path in group["code_paths"]}
        symbols_by_group = [_exported_symbols((root / path for path in group["code_paths"]), domain_terms) for group in repo_groups]
        all_symbols = set().union(*symbols_by_group) if symbols_by_group else set()

        for path in code_files:
            relative = code_paths[path]
            if relative in owned:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            calls = set(_CALL_RE.findall(text)) & all_symbols
            if not calls:
                continue
            group_index = _select_group(relative, calls, repo_groups, symbols_by_group)
            record = {"repo_id": repo_id, "path": relative, "reason": f"direct_caller:{','.join(sorted(calls)[:5])}"}
            repo_groups[group_index]["context_paths"].append(relative)
            context_files.append(record)

        for path in _iter_files(root, CONTEXT_SUFFIXES):
            relative = _relative(path, root)
            text = path.read_text(encoding="utf-8", errors="replace")
            calls = set(_CALL_RE.findall(text)) & all_symbols
            if calls or (owned and _matches_domain(relative, text, domain_terms)):
                group_index = _select_group(relative, calls, repo_groups, symbols_by_group)
                record = {"repo_id": repo_id, "path": relative, "reason": f"direct_reference:{','.join(sorted(calls)[:5])}" if calls else "target_context"}
                repo_groups[group_index]["context_paths"].append(relative)
                context_files.append(record)

        for group in repo_groups:
            group["code_paths"] = sorted(dict.fromkeys(group["code_paths"]))
            group["context_paths"] = sorted(dict.fromkeys(group["context_paths"]))
        groups.extend(repo_groups)

    return {
        "requested_scope": normalized_scopes,
        "groups": groups,
        "context_files": _unique_records(context_files),
        "added_files": _unique_records(added_files),
        "boundary": "source_scope = explicit scope + declared implementations; context_scope = direct callers + target-related config/docs/tests",
    }


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
