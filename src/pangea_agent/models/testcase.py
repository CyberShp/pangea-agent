from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    case_type: str = Field(min_length=1)
    linked_risk_ids: list[str] = Field(min_length=1)
    preconditions: list[str] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    expected_results: list[str] = Field(min_length=1)
    observability: list[str] = Field(min_length=1)
    cleanup: list[str] = Field(min_length=1)
    status: str = "draft"
