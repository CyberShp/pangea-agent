from __future__ import annotations

from pydantic import BaseModel, Field


class ReportModel(BaseModel):
    run_id: str
    risks: list[dict] = Field(default_factory=list)
    test_cases: list[dict] = Field(default_factory=list)
    quality_report: dict = Field(default_factory=dict)
