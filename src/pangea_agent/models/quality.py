from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PASS", "REWORK", "UNRESOLVED"]
    unresolved: list[dict] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
