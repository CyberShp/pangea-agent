from __future__ import annotations

from pathlib import Path


def list_registered_repositories(data_root: str = "pangea-data") -> list[str]:
    root = Path(data_root) / "repositories"
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())
