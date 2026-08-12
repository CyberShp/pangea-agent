from __future__ import annotations

from importlib.resources import files


def load_builtin_rubric(name: str) -> str:
    path = files("pangea_agent.rubrics.builtin").joinpath(name)
    return path.read_text(encoding="utf-8")
