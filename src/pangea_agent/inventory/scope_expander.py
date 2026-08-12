from __future__ import annotations

import re
from pathlib import Path


CODE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}
CONTEXT_SUFFIXES = {".md", ".rst", ".txt", ".sh", ".py", ".json", ".yaml", ".yml"}
IGNORED_PARTS = {".git", "build", "dist", "third_party", "node_modules", "__pycache__", ".pangea"}
COMMON_FUNCTIONS = {"main", "free", "calloc", "malloc", "memcpy", "memset", "strcmp", "strlen"}
GENERIC_TERMS = {"analysis", "feature", "include", "module", "source", "test", "功能", "模块", "分析"}
_CALL_RE = re.compile(r"\b([A-Za-z_]\w{5,})\s*\(")
_DECL_RE = re.compile(
    r"^[ \t]*(?:extern[ \t]+)?(?:[A-Za-z_]\w*[ \t*]+)+([A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t]*;",
    re.MULTILINE,
)
_DEF_RE = re.compile(
    r"^[ \t]*(?!if\b|for\b|while\b|switch\b)(?:[A-Za-z_]\w*[ \t*]+)+"
    r"([A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t\n]*\{",
    re.MULTILINE,
)


def expand_analysis_scope(
    repositories: list[dict],
    requested_scopes: list[str],
    *,
    target: str,
    focus: list[str],
) -> dict:
    """Expand explicit C/C++ scope once to direct callers and related repository context."""
    normalized_scopes = [_normalize(value) for value in requested_scopes or ["."]]
    domain_terms = _domain_terms(target, focus)
    groups: list[dict] = []
    context_files: list[dict] = []
    added_files: list[dict] = []

    for repository in repositories:
        repo_id = repository["repo_id"]
        root = Path(repository["source_root"])
        code_files = list(_iter_files(root, CODE_SUFFIXES))
        code_paths = {path: _relative(path, root) for path in code_files}
        repo_groups = []
        explicitly_owned: set[str] = set()
        for scope in normalized_scopes:
            paths = sorted(
                relative
                for relative in code_paths.values()
                if relative not in explicitly_owned and _inside_scope(relative, scope)
            )
            explicitly_owned.update(paths)
            repo_groups.append({
                "repo_id": repo_id,
                "requested_scope": [scope],
                "code_paths": paths,
                "context_paths": [],
            })
        owned = {path for group in repo_groups for path in group["code_paths"]}
        symbols_by_group = [
            _exported_symbols((root / path for path in group["code_paths"]), domain_terms)
            for group in repo_groups
        ]
        all_symbols = set().union(*symbols_by_group) if symbols_by_group else set()

        for path in code_files:
            relative = code_paths[path]
            if relative in owned:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            calls = set(_CALL_RE.findall(text)) & all_symbols
            keyword_match = bool(owned) and _matches_domain(relative, text, domain_terms)
            if not calls and not keyword_match:
                continue
            group_index = _select_group(relative, calls, repo_groups, symbols_by_group)
            repo_groups[group_index]["code_paths"].append(relative)
            owned.add(relative)
            reason = f"direct_caller:{','.join(sorted(calls)[:5])}" if calls else "target_context"
            added_files.append({"repo_id": repo_id, "path": relative, "reason": reason})

        for path in _iter_files(root, CONTEXT_SUFFIXES):
            relative = _relative(path, root)
            text = path.read_text(encoding="utf-8", errors="replace")
            calls = set(_CALL_RE.findall(text)) & all_symbols
            if calls or (owned and _matches_domain(relative, text, domain_terms)):
                group_index = _select_group(relative, calls, repo_groups, symbols_by_group)
                record = {
                    "repo_id": repo_id,
                    "path": relative,
                    "reason": f"direct_reference:{','.join(sorted(calls)[:5])}" if calls else "target_context",
                }
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
        "boundary": "explicit scope + direct external callers + target-related config/docs/tests; no recursive caller expansion",
    }


def _iter_files(root: Path, suffixes: set[str]):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in suffixes and not any(
            part in IGNORED_PARTS for part in path.relative_to(root).parts
        ):
            yield path


def _exported_symbols(paths, domain_terms: tuple[str, ...]) -> set[str]:
    symbols: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _DEF_RE.finditer(text):
            name = match.group(1)
            declaration = match.group(0).split("(", 1)[0]
            if name not in COMMON_FUNCTIONS and not re.search(r"\bstatic\b", declaration):
                symbols.add(name)
        if path.suffix.lower() in {".h", ".hpp", ".hh"}:
            for match in _DECL_RE.finditer(text):
                name = match.group(1)
                declaration = match.group(0).lower()
                if name not in COMMON_FUNCTIONS and (
                    _matches_domain(name, declaration, domain_terms)
                    or _matches_domain(str(path), declaration, domain_terms)
                ):
                    symbols.add(name)
    return symbols


def _select_group(relative: str, calls: set[str], groups: list[dict], symbols_by_group: list[set[str]]) -> int:
    scores = [len(calls & symbols) for symbols in symbols_by_group]
    if max(scores, default=0) > 0:
        return scores.index(max(scores))
    path_parts = relative.split("/")
    proximity = []
    for group in groups:
        scope_parts = group["requested_scope"][0].split("/")
        proximity.append(sum(a == b for a, b in zip(path_parts, scope_parts)))
    return proximity.index(max(proximity)) if proximity else 0


def _domain_terms(target: str, focus: list[str]) -> tuple[str, ...]:
    source = " ".join([target, *focus]).lower()
    terms = {
        value for value in re.findall(r"[a-z0-9_+-]{3,}|[\u4e00-\u9fff]{2,}", source)
        if value not in GENERIC_TERMS
    }
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
    return str(path.relative_to(root)).replace("\\", "/")


def _inside_scope(path: str, scope: str) -> bool:
    return scope == "." or path == scope or path.startswith(f"{scope}/")


def _unique_records(records: list[dict]) -> list[dict]:
    unique: dict[tuple[str, str], dict] = {}
    for record in records:
        unique[(record["repo_id"], record["path"])] = record
    return sorted(unique.values(), key=lambda item: (item["repo_id"], item["path"]))
