from __future__ import annotations

from pathlib import Path

from pangea_agent.repositories.registry import list_registered_repositories


def print_repositories(data_root: str = "pangea-data") -> None:
    for repo_id in list_registered_repositories(data_root):
        print(repo_id)
