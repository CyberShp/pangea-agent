from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .evidence import EvidenceRef


class UpstreamSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reachability: str = Field(min_length=1)
    caller_constraints: str = Field(min_length=1)
    documented_behavior: str = Field(min_length=1)
    existing_tests: str = Field(min_length=1)
    conclusion: Literal["risk_remains", "expected_behavior", "unresolved"]


class RiskCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    dfx: list[str] = Field(min_length=1)
    severity: Literal["Low", "Medium", "High", "Critical"]
    confidence: Literal["low", "medium", "high"]
    trigger: str = Field(min_length=1)
    system_result: str = Field(min_length=1)
    external_observation: str = Field(min_length=1)
    exclusion_condition: str = Field(min_length=1)
    upstream_semantics: UpstreamSemantics
    translation_status: Literal["Blackbox-ready", "Graybox-ready", "Developer-confirm"]
    status: Literal["pending", "accepted", "confirmed", "false_positive", "claimed_fixed", "verified_fixed"]
    evidence: list[EvidenceRef] = Field(min_length=1)
