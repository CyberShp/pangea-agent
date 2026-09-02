from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


AssetType = Literal[
    "requirement",
    "design",
    "historical_defect",
    "reference",
    "coverage",
    "test_case_example",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceReference(StrictModel):
    path: str = Field(min_length=1)
    location: str = Field(min_length=1)


class RequirementItem(StrictModel):
    item_type: Literal["requirement"] = "requirement"
    item_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    requirement_id: str | None = Field(default=None, min_length=1)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(min_length=1)


class DesignItem(StrictModel):
    item_type: Literal["design"] = "design"
    item_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    modules: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    main_flows: list[str] = Field(default_factory=list)
    branch_flows: list[str] = Field(default_factory=list)
    error_flows: list[str] = Field(default_factory=list)
    recovery_flows: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(min_length=1)


class HistoricalDefectItem(StrictModel):
    item_type: Literal["historical_defect"] = "historical_defect"
    item_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    symptom: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    propagation: list[str] = Field(min_length=1)
    defect_mechanism: str = Field(min_length=1)
    exclusion_conditions: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(min_length=1)


class ReferenceItem(StrictModel):
    item_type: Literal["reference"] = "reference"
    item_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    applicable_modules: list[str] = Field(default_factory=list)
    key_facts: list[str] = Field(min_length=1)
    source_references: list[SourceReference] = Field(min_length=1)


StructuredAssetItem = Annotated[
    Union[RequirementItem, DesignItem, HistoricalDefectItem, ReferenceItem],
    Field(discriminator="item_type"),
]


class AssetRecord(StrictModel):
    # ``1.0`` is accepted so old asset catalogues and old Runs remain
    # readable.  New records and every mutation are written as 2.0.
    schema_version: Literal["1.0", "2.0"] = "2.0"
    asset_id: str = Field(min_length=1)
    asset_type: AssetType
    title: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    source_name: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_size: int = Field(default=0, ge=0)
    repository_ids: list[str] = Field(default_factory=list)
    module_tags: list[str] = Field(default_factory=list)
    language_tags: list[str] = Field(default_factory=list)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    status: Literal[
        "imported",
        "extracting",
        "awaiting_review",
        "available",
        "no_items",
        "rejected",
        "failed",
        "archived",
    ] = "imported"
    review_status: Literal["not_required", "pending", "approved", "rejected"] = (
        "not_required"
    )
    structured_item_count: int = Field(default=0, ge=0)
    extraction_task_path: str | None = None
    normalized_text_path: str | None = None
    parser_version: str | None = None
    result_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
    last_error: str | None = None


class AssetExtractionTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["asset_extraction"] = "asset_extraction"
    asset_id: str = Field(min_length=1)
    asset_type: Literal["requirement", "design", "historical_defect", "reference"]
    title: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    extracted_text_path: str = Field(min_length=1)
    attachments: list[dict] = Field(default_factory=list)
    result_schema_path: str = Field(
        default="schemas/asset_extraction_result.schema.json", min_length=1
    )
    result_path: str = Field(min_length=1)


class AssetExtractionResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    asset_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    items: list[StructuredAssetItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_item_ids(self) -> "AssetExtractionResult":
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("结构化资产 item_id 不能重复")
        return self
