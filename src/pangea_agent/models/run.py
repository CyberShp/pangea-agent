from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RunPhase = Literal[
    "PREPARING",
    "WAITING_ANALYSIS",
    "WAITING_REVIEW",
    "WAITING_REWORK",
    "WAITING_REWORK_REVIEW",
    "READY_TO_FINALIZE",
    "COMPLETE",
    "INCOMPLETE",
]


class RunProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    contract_digest: str = Field(min_length=64, max_length=64)
    phase: RunPhase
    analysis_units: list[str] = Field(default_factory=list)
    completed_analysis_units: list[str] = Field(default_factory=list)
    completed_rework_units: list[str] = Field(default_factory=list)
    quality_status: Literal["PASS", "REWORK", "UNRESOLVED"] | None = None
    errors: list[dict] = Field(default_factory=list)
    error_history: list[dict] = Field(default_factory=list)
