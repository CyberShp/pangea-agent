from __future__ import annotations

from pathlib import Path


def init_data(root: str = "pangea-data") -> None:
    base = Path(root)
    for name in ("repositories", "inbox", "coverage", "runs"):
        (base / name).mkdir(parents=True, exist_ok=True)
