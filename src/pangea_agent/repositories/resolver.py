from __future__ import annotations

from pathlib import Path

from .git_info import read_git_info
from .guards import ensure_inside_repositories


def resolve_repository(repo_id: str, data_root: str) -> dict:
    repositories_root = Path(data_root) / "repositories"
    root = repositories_root / repo_id
    ensure_inside_repositories(root, repositories_root)
    if not root.is_dir():
        raise ValueError(f"仓库不存在：{repo_id}")
    return {
        "repo_id": repo_id,
        "source_root": str(root),
        "git": read_git_info(root),
    }
