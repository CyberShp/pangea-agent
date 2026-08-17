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

InitStep = Literal[
    "CONTRACT_FROZEN",
    "SOURCE_READY",
    "INDEX_READY",
    "INVENTORY_READY",
]


class AgentSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["analysis", "review", "rework"]
    unit_id: str | None = None
    stage: Literal["analysis", "initial_review", "rework", "rework_verification"]
    task_id: str | None = None
    status: Literal["pending", "dispatched", "completed"] = "pending"


class RunProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    phase: RunPhase
    init_step: InitStep | None = None
    analysis_units: list[str] = Field(default_factory=list)
    completed_analysis_units: list[str] = Field(default_factory=list)
    completed_rework_units: list[str] = Field(default_factory=list)
    quality_status: Literal["PASS", "REWORK", "UNRESOLVED"] | None = None
    errors: list[dict] = Field(default_factory=list)
    error_history: list[dict] = Field(default_factory=list)
    agent_sessions: dict[str, AgentSession] = Field(default_factory=dict)
