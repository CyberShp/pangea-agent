from __future__ import annotations

from importlib.resources import files


V1_BUILTIN_RUBRICS = (
    "c_cpp_analysis.md",
    "dfx.md",
    "risk_reproducibility.md",
    "test_case_generation.md",
)


def load_builtin_rubric(name: str) -> str:
    path = files("pangea_agent.rubrics.builtin").joinpath(name)
    return path.read_text(encoding="utf-8")


def load_v1_rubrics() -> list[str]:
    """Load the V1 defaults; SFMEA is intentionally not part of this set."""
    return [load_builtin_rubric(name) for name in V1_BUILTIN_RUBRICS]
