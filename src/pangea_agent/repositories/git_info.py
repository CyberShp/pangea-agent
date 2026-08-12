from __future__ import annotations

import subprocess
from pathlib import Path


def read_git_info(path: Path) -> dict:
    if not (path / ".git").exists():
        return {"is_git": False}
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=path, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=path, text=True).strip())
        return {"is_git": True, "commit": commit, "branch": branch, "dirty": dirty}
    except Exception as exc:
        return {"is_git": True, "error": str(exc)}
