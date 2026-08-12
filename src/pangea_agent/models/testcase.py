from __future__ import annotations

from pydantic import BaseModel, Field


class TestCase(BaseModel):
    test_case_id: str
    title: str
    linked_risk_ids: list[str] = Field(default_factory=list)
    preconditions: list[str]
    steps: list[str]
    expected_results: list[str]
    observability: list[str]
    cleanup: list[str]
