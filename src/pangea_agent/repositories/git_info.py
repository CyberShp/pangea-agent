from __future__ import annotations

import subprocess
from pathlib import Path


def read_git_info(path: Path) -> dict:
    if not path.is_dir():
        return {"is_git": False, "version_status": "missing", "reason": "source root does not exist"}
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return {"is_git": False, "version_status": "unversioned"}
    try:
        top_level = Path(subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=path, text=True, stderr=subprocess.DEVNULL
        ).strip()).resolve()
        if top_level != path.resolve():
            return {
                "is_git": False,
                "version_status": "nested_repository",
                "reason": f"source root is nested inside Git repository {top_level}",
            }
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=path, text=True, stderr=subprocess.DEVNULL
        ).strip()
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=path, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=path, text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
        return {
            "is_git": True,
            "version_status": "verified",
            "commit": commit,
            "branch": branch,
            "dirty": dirty,
        }
    except Exception as exc:
        return {"is_git": True, "version_status": "unverifiable", "error": str(exc)}
