from __future__ import annotations

from pathlib import Path

from .cpp_branches import extract_branches
from .cpp_resources import extract_resource_signals
from .cpp_symbols import extract_functions

CODE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}


def build_lightweight_inventory(repositories: list[dict], module_scope: list[str]) -> dict:
    files = []
    for repo in repositories:
        repo_id = repo["repo_id"]
        root = Path(repo["source_root"])
        for scope in module_scope or ["."]:
            scoped_root = root / scope
            if not scoped_root.exists():
                continue
            for path in scoped_root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in CODE_SUFFIXES:
                    continue
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                files.append({
                    "repo_id": repo_id,
                    "path": str(path.relative_to(root)),
                    "line_count": len(lines),
                    "functions": extract_functions(lines),
                    "branches": extract_branches(lines),
                    "resource_signals": extract_resource_signals(lines),
                })
    return {"files": files, "file_count": len(files)}
