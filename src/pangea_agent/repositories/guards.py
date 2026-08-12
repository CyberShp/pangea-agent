from __future__ import annotations

from pathlib import Path


def ensure_inside_repositories(path: Path, repositories_root: Path) -> None:
    resolved = path.resolve()
    allowed = repositories_root.resolve()
    if not resolved.is_relative_to(allowed):
        raise ValueError(f"source path escapes repositories root: {path}")
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"source repository is not readable: {path}")
