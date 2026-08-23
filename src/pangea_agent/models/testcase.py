from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TestStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    expected_result: str = Field(min_length=1)
    failure_observation: str | None = Field(default=None, min_length=1)


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    case_type: str = Field(min_length=1)
    linked_risk_ids: list[str] = Field(default_factory=list)
    linked_requirement_ids: list[str] = Field(default_factory=list)
    linked_material_ids: list[str] = Field(default_factory=list)
    linked_coverage_ids: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(min_length=1)
    steps: list[TestStep] = Field(min_length=1)
    observability: list[str] = Field(min_length=1)
    cleanup: list[str] = Field(min_length=1)
    status: str = "draft"

    @model_validator(mode="after")
    def validate_links(self) -> "TestCase":
        if not any((
            self.linked_risk_ids,
            self.linked_requirement_ids,
            self.linked_material_ids,
            self.linked_coverage_ids,
        )):
            raise ValueError("测试用例必须关联至少一个风险、需求、当前资料或 Coverage 缺口")
        if self.linked_risk_ids and not any(step.failure_observation for step in self.steps):
            raise ValueError("风险用例必须至少提供一项 failure_observation")
        return self
