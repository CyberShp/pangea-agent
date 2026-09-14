"""Frozen file catalogue and per-file structural queries; no target inference."""
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from .languages import C_CPP_SUFFIXES, LUA_SUFFIXES, IGNORED_PARTS
from .source_regions import build_file_regions


def requested_files(repositories, scopes, language):
    groups = []
    suffixes = LUA_SUFFIXES if language == "lua" else C_CPP_SUFFIXES
    for repository in repositories:
        root = Path(repository["source_root"]).resolve()
        paths = set()
        for scope in scopes:
            candidate = (root / scope).resolve()
            candidate.relative_to(root)
            for file in ([candidate] if candidate.is_file() else candidate.rglob("*")):
                relative = file.relative_to(root)
                if file.is_file() and file.suffix.lower() in suffixes and not any(p in IGNORED_PARTS for p in relative.parts):
                    file.resolve().relative_to(root)
                    paths.add(relative.as_posix())
        groups.append({"repo_id": repository["repo_id"], "code_paths": sorted(paths), "context_paths": []})
    return {"groups": groups, "context_files": [], "added_files": [],
            "context_budget": {"policy": "on-demand-v1"}}


def file_inventory(repositories, expansion):
    roots = {r["repo_id"]: Path(r["source_root"]) for r in repositories}
    files = []
    for group in expansion["groups"]:
        for relative in dict.fromkeys([*group["code_paths"], *group["context_paths"]]):
            file = roots[group["repo_id"]] / relative
            files.append({"repo_id": group["repo_id"], "path": relative,
                          "line_count": len(file.read_text(encoding="utf-8", errors="replace").splitlines()),
                          "size_bytes": file.stat().st_size, "parse_complete": False,
                          "fallback_analysis": "结构尚未查询；可按文件读取或检索冻结原文"})
    return {"files": files, "file_count": len(files), "index_policy": "on-demand-v1"}


def directory_overview(inventory):
    groups = {}
    for file in inventory["files"]:
        # First-level groups keep the initial prompt independent of subtree size.
        parent = file["path"].split("/")[0] if "/" in file["path"] else "."
        key = (file["repo_id"], parent)
        groups[key] = groups.get(key, 0) + 1
    return [{"repo_id": repo, "directory": folder, "file_count": count}
            for (repo, folder), count in sorted(groups.items())]


def read_index(run_dir):
    index = read_json(run_dir / "inputs" / "source-index.json")
    if index.get("index_policy") != "on-demand-v1":
        return index
    files = []
    for entry in index["files"]:
        cached = detail_path(run_dir, entry["repo_id"], entry["path"])
        if cached.is_file():
            detail = read_json(cached)
            # Keep the original whole-file handle stable after detailed queries.
            known = {r["region_id"] for r in detail["regions"]}
            entry = {**entry, **detail, "regions": detail["regions"] + [r for r in entry["regions"] if r["region_id"] not in known]}
        files.append(entry)
    return {**index, "files": files}


def detail_path(run_dir, repo_id, relative):
    base = (run_dir / "inputs" / "source-details").resolve()
    path = (base / repo_id / (relative + ".json")).resolve()
    path.relative_to(base)
    return path


def ensure_file_index(run_dir, repo_id, relative, root):
    """Called only after the source access layer validates the exact file."""
    destination = detail_path(run_dir, repo_id, relative)
    if destination.is_file():
        return
    from .source_scanner import build_lightweight_inventory
    from .lua_source_scanner import build_lua_inventory
    scan = build_lua_inventory if Path(relative).suffix.lower() == ".lua" else build_lightweight_inventory
    inventory = scan([{"repo_id": repo_id, "source_root": str(root)}], [relative])
    if inventory["files"]:
        write_json(destination, build_file_regions(inventory["files"][0]).model_dump(mode="json"))
