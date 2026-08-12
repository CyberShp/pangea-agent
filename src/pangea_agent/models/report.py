from __future__ import annotations

from pydantic import BaseModel, Field


class ReportModel(BaseModel):
    run_id: str
    task_contract: dict = Field(default_factory=dict)
    module_scope: list[str] = Field(default_factory=list)
    repositories: list[dict] = Field(default_factory=list)
    source_manifest: dict = Field(default_factory=dict)
    inventory: dict = Field(default_factory=dict)
    business_flows: list[dict] = Field(default_factory=list)
    visual_findings: list[dict] = Field(default_factory=list)
    risks: list[dict] = Field(default_factory=list)
    test_cases: list[dict] = Field(default_factory=list)
    coverage_report: dict = Field(default_factory=dict)
    quality_report: dict = Field(default_factory=dict)
    errors: list[dict] = Field(default_factory=list)
