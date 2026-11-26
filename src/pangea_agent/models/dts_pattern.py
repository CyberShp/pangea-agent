"""Data models for DTS failure topology cards and frozen per-run DTS context."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


NonEmptyText = Annotated[str, Field(min_length=1)]
DTSPatternTrack = Literal["error_rollback", "concurrency_timing", "resource_lifecycle", "memory_corruption"]
DTSPatternStatus = Literal["enabled", "disabled"]
DTSSourceKind = Literal[
    "mcp_dts_codehub",
    "offline_file_with_git",
    "offline_file_text",
    "historical_defect_asset",
    "manual",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DTSPatternInput(StrictModel):
    pattern_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    title: NonEmptyText
    track: DTSPatternTrack
    dts_id: str | None = None
    module_hints: list[NonEmptyText] = Field(default_factory=list)
    context_triggers: list[NonEmptyText] = Field(min_length=1)
    buggy_skeleton: NonEmptyText
    fixed_guard: NonEmptyText
    defect_mechanism: NonEmptyText
    already_fixed_check: NonEmptyText
    repro_instrumentation_recipe: str = ""
    has_diff_evidence: bool = False
    mr_url: str | None = None
    commit_ref: str | None = None
    source_kind: DTSSourceKind = "offline_file_text"
    source_refs: list[NonEmptyText] = Field(default_factory=list)


class DTSPatternCard(DTSPatternInput):
    format_version: Literal["dts-pattern-v1"] = "dts-pattern-v1"
    status: DTSPatternStatus = "enabled"
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def valid_timestamps(self) -> "DTSPatternCard":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        return self


class DTSPatternRegistry(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    patterns: list[DTSPatternCard] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_patterns(self) -> "DTSPatternRegistry":
        ids = [item.pattern_id for item in self.patterns]
        if len(ids) != len(set(ids)):
            raise ValueError("DTS 故障拓扑库中 pattern_id 不能重复")
        return self


class FrozenHomologousDTS(StrictModel):
    dts_id: NonEmptyText
    title: NonEmptyText
    track: DTSPatternTrack | None = None
    module_path: str = ""
    symptom: str = ""
    root_cause: str = ""
    mr_url: str = ""
    commit_ref: str = ""
    diff_excerpt: str = ""
    already_fixed_check: str = ""
    source_kind: DTSSourceKind = "offline_file_text"


class DTSContextSnapshot(StrictModel):
    format_version: Literal["dts-context-v1"] = "dts-context-v1"
    run_id: NonEmptyText
    frozen_at: AwareDatetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_dts_ids: list[NonEmptyText] = Field(default_factory=list)
    requested_mr_urls: list[NonEmptyText] = Field(default_factory=list)
    mcp_status: dict = Field(default_factory=dict)
    homologous_dts: list[FrozenHomologousDTS] = Field(default_factory=list)
    matched_patterns: list[DTSPatternCard] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
