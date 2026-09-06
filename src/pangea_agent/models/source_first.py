"""Contracts used by the source-first workflow.

The source-first path deliberately keeps the machine-owned envelope small.  An
Agent writes prose records, while the workflow owns identifiers, binding,
revisions, and completion state.  The models in this module are transport
contracts only; they do not attempt to score the meaning of an Agent record.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SOURCE_FIRST_VERSION = "source-first-v1"
NOTES_FORMAT_VERSION = "pangea-notes-v1"


class SourceFirstModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceBinding(SourceFirstModel):
    """The immutable identity attached to every source/result operation."""

    data_root: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)


class SourceRegion(SourceFirstModel):
    region_id: str = Field(min_length=1)
    repo_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    kind: Literal["function", "type", "global", "macro", "branch", "raw"]
    line_start: int = Field(gt=0)
    line_end: int = Field(ge=1)
    symbol: str | None = Field(default=None, min_length=1)
    parser: str | None = Field(default=None, min_length=1)
    parse_complete: bool = True
    parsing_note: str | None = Field(default=None, min_length=1)


class SourceFileIndex(SourceFirstModel):
    repo_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line_count: int = Field(ge=0)
    regions: list[SourceRegion] = Field(default_factory=list)
    parse_complete: bool = True
    scope_role: Literal["owned", "reference"] = "reference"
    region_count: int = Field(default=0, ge=0)


class SourceRegionSummary(SourceFirstModel):
    region_id: str = Field(min_length=1)
    kind: Literal["function", "type", "global", "macro", "branch", "raw"]
    line_start: int = Field(gt=0)
    line_end: int = Field(ge=1)
    symbol: str | None = None


class SourceIndexFilePage(SourceFirstModel):
    repo_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line_count: int = Field(ge=0)
    scope_role: Literal["owned", "reference"] = "reference"
    region_count: int = Field(default=0, ge=0)
    regions: list[SourceRegionSummary] = Field(default_factory=list)


class SourceIndexPage(SourceFirstModel):
    format_version: Literal["pangea-source-index-v1"] = "pangea-source-index-v1"
    binding: SourceBinding
    files: list[SourceIndexFilePage] = Field(default_factory=list)
    next_cursor: str | None = None
    total_files: int = Field(ge=0)
    total_regions: int = Field(default=0, ge=0)
    page_mode: Literal["files", "regions"] = "files"


class SourceReadResult(SourceFirstModel):
    format_version: Literal["pangea-source-read-v1"] = "pangea-source-read-v1"
    binding: SourceBinding
    repo_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line_start: int = Field(gt=0)
    line_end: int = Field(ge=1)
    text: str
    evidence_handle: str = Field(min_length=1)
    next_cursor: str | None = None


class SourceSearchHit(SourceFirstModel):
    repo_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line: int = Field(gt=0)
    text: str
    evidence_handle: str = Field(min_length=1)
    region_ids: list[str] = Field(default_factory=list)


class SourceSearchResult(SourceFirstModel):
    format_version: Literal["pangea-source-search-v1"] = "pangea-source-search-v1"
    binding: SourceBinding
    query: str = Field(min_length=1)
    hits: list[SourceSearchHit] = Field(default_factory=list)
    next_cursor: str | None = None


class OwnedRegion(SourceFirstModel):
    """A Planning Agent's semantic ownership declaration."""

    repo_id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)


class PlannedUnit(SourceFirstModel):
    unit_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    owned_regions: list[OwnedRegion] = Field(min_length=1)
    context_regions: list[OwnedRegion] = Field(default_factory=list)
    purpose: str = ""
    body: str = ""


class PlanRecord(SourceFirstModel):
    record_id: str = Field(min_length=1)
    unit: PlannedUnit


class SourceFirstPlan(SourceFirstModel):
    format_version: Literal["pangea-plan-v1"] = "pangea-plan-v1"
    binding: SourceBinding
    revision: int = Field(ge=0)
    summary: str = ""
    units: list[PlannedUnit] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


NoteKind = Literal[
    "summary",
    "flow",
    "risk",
    "test_case",
    "finding",
    "note",
    "unresolved",
    "unit_plan",
    "review_decision",
    "completion",
    "branch",
    "evidence",
    "scenario",
    "review_finding",
    "blackbox_translation",
]


class NoteRecord(SourceFirstModel):
    record_id: str = Field(min_length=1)
    body: Any
    kind: str = "note"
    # Keep the original JSON values when a worker sends an unusual relation
    # shape.  The store warns about it, but must not silently replace it.
    evidence: Any = Field(default_factory=list)
    relates_to: Any = Field(default_factory=list)
    # The Agent may explicitly retire earlier records without erasing audit
    # history.  Keep unusual input shapes readable; the store records warnings
    # and only valid references affect the active projection.
    supersedes: Any = Field(default_factory=list)
    created_revision: int = Field(ge=1)


class CompletionDeclaration(SourceFirstModel):
    complete: bool
    note: str = ""
    declared_revision: int = Field(ge=0)


class NotesResult(SourceFirstModel):
    format_version: Literal["pangea-notes-v1"] = "pangea-notes-v1"
    binding: SourceBinding
    revision: int = Field(ge=0)
    records: list[NoteRecord] = Field(default_factory=list)
    completion: CompletionDeclaration | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    receipts: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ReviewDecision(SourceFirstModel):
    # The decision vocabulary is machine-routed, but the Reviewer may carry
    # additional semantic fields in the original body.  They must survive
    # transport without becoming a new rich schema gate.
    model_config = ConfigDict(extra="allow")
    disposition: Literal["pass", "unresolved", "finding"]
    summary: str = ""
    finding_keys: list[str] = Field(default_factory=list)
    closure_units: list[str] = Field(default_factory=list)
    body: Any = None
