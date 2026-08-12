from __future__ import annotations

from pydantic import BaseModel, Field


class QualityReport(BaseModel):
    status: str
    unresolved: list[dict] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
