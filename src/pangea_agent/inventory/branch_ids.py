from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def assign_branch_ids(
    repo_id: str,
    path: str,
    branches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return branch records with deterministic IDs inside one frozen source file."""

    occurrences: dict[tuple[object, str], int] = {}
    identified: list[dict[str, Any]] = []
    for raw in branches:
        branch = dict(raw) if isinstance(raw, Mapping) else {}
        line = branch.get("line")
        kind = str(branch.get("kind") or "branch")
        signature = (line, kind)
        ordinal = occurrences.get(signature, 0) + 1
        occurrences[signature] = ordinal
        branch["branch_id"] = f"{repo_id}:{path}:{line}:{kind}:{ordinal}"
        identified.append(branch)
    return identified
