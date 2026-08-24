from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pangea_agent.agent_io import agent_path


RunPhase = Literal[
    "PREPARING",
    "WAITING_SOURCE_CHECKPOINT",
    "WAITING_RISK_ANALYSIS",
    "WAITING_TEST_GENERATION",
    "WAITING_INDEPENDENT_REVIEW",
    "WAITING_COMPARISON_REVIEW",
    "WAITING_REWORK",
    "WAITING_REWORK_VERIFICATION",
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
    stage: Literal[
        "source_checkpoint",
        "risk_analysis",
        "test_generation",
        "independent_review",
        "comparison_review",
        "rework",
        "rework_verification",
    ]
    task_id: str | None = None
    status: Literal["pending", "dispatched", "completed"] = "pending"


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["dispatch_agent", "continue_agent"]
    role: Literal["analysis", "review", "rework"]
    stage: Literal[
        "source_checkpoint",
        "risk_analysis",
        "test_generation",
        "independent_review",
        "comparison_review",
        "rework",
        "rework_verification",
    ]
    session_key: str = Field(min_length=1)
    unit_id: str | None = None
    task_path: str = Field(min_length=1)
    task_id: str | None = None
    replacement_allowed: bool = False
    after_completion: Literal["resume_run"] = "resume_run"

    @field_validator("task_path", mode="before")
    @classmethod
    def normalize_task_path(cls, value: str) -> str:
        return agent_path(value)


class RunProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    workflow_version: Literal[2]
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
