"""Dispatch each file to its own language parser, preserving existing parsers."""
from pathlib import Path

from .languages import source_files
from .source_scanner import build_lightweight_inventory
from .lua_source_scanner import build_lua_inventory
from .script_source_scanner import build_script_inventory


def build_inventory(repositories: list[dict], scopes: list[str]) -> dict:
    groups = {}
    for repo, path, language in source_files(repositories, scopes):
        key = (repo["repo_id"], language)
        groups.setdefault(key, (repo, []))[1].append(path.relative_to(Path(repo["source_root"]).resolve()).as_posix())
    files, failures, missing = [], [], set()
    for (_, language), (repo, paths) in groups.items():
        scanner = {"c_cpp": build_lightweight_inventory, "lua": build_lua_inventory}.get(language, build_script_inventory)
        inventory = scanner([repo], paths)
        files.extend({**entry, "language": language} for entry in inventory["files"])
        failures.extend(inventory.get("parse_failures", []))
        missing.update(inventory.get("missing_dependencies", []))
    files.sort(key=lambda item: (item["repo_id"], item["path"]))
    return {"files": files, "file_count": len(files), "parse_failures": failures,
            "missing_dependencies": sorted(missing),
            "structural_parse_complete": all(f.get("parse_complete") for f in files)}


def scoped_languages(manifest: dict, paths: list[dict]) -> list[str]:
    """Use frozen file metadata; older Runs retain their single-language default."""
    selected = {(p["repo_id"], p["path"]) for p in paths}
    languages = sorted({p["language"] for p in manifest.get("file_languages", [])
                        if (p["repo_id"], p["path"]) in selected})
    return languages or manifest.get("analysis_languages") or [manifest.get("analysis_language", "c_cpp")]
