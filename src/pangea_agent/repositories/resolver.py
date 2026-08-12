from __future__ import annotations

from pathlib import Path

from .git_info import read_git_info
from .guards import ensure_inside_repositories


def resolve_repositories_from_contract(contract: dict, data_root: str) -> list[dict]:
    repo_ids = contract.get("repositories") or [contract.get("repository")]
    repo_ids = [item for item in repo_ids if item]
    if not repo_ids:
        raise ValueError("任务契约必须指定 repository 或 repositories")
    repositories_root = Path(data_root) / "repositories"
    results = []
    for repo_id in repo_ids:
        root = repositories_root / repo_id
        ensure_inside_repositories(root, repositories_root)
        results.append({
            "repo_id": repo_id,
            "source_root": str(root),
            "git": read_git_info(root),
        })
    return results
