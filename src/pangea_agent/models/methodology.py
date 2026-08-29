from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


NonEmptyText = Annotated[str, Field(min_length=1)]
MethodologyStatus = Literal["candidate", "enabled", "disabled"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MethodologyCandidate(StrictModel):
    methodology_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    title: str = Field(min_length=1)
    applicable_when: list[NonEmptyText] = Field(min_length=1)
    checks: list[NonEmptyText] = Field(min_length=1)
    expected_signals: list[NonEmptyText] = Field(min_length=1)
    failure_signals: list[NonEmptyText] = Field(min_length=1)
    exceptions: list[NonEmptyText] = Field(default_factory=list)
    source_item_ids: list[NonEmptyText] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_lists(self) -> "MethodologyCandidate":
        for field_name in (
            "applicable_when",
            "checks",
            "expected_signals",
            "failure_signals",
            "exceptions",
            "source_item_ids",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} 不能包含重复项")
        return self


class MethodologyCandidateFile(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: AwareDatetime
    source: Literal["confirmed_historical_defects"] = (
        "confirmed_historical_defects"
    )
    non_binding: Literal[True] = True
    candidates: list[MethodologyCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_candidates(self) -> "MethodologyCandidateFile":
        identifiers = [item.methodology_id for item in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("候选文件中 methodology_id 不能重复")
        return self


class MethodologyRecord(MethodologyCandidate):
    status: MethodologyStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def valid_timestamps(self) -> "MethodologyRecord":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        return self


class MethodologyRegistry(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    methodologies: list[MethodologyRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_methodologies(self) -> "MethodologyRegistry":
        identifiers = [item.methodology_id for item in self.methodologies]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("注册表中 methodology_id 不能重复")
        return self


class FrozenMethodologyRef(StrictModel):
    methodology_id: str = Field(min_length=1)
    origin: Literal["user"] = "user"
    title: str = Field(min_length=1)
    path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_item_ids: list[NonEmptyText] = Field(min_length=1)
    applicable_when: list[NonEmptyText] = Field(default_factory=list)
    exceptions: list[NonEmptyText] = Field(default_factory=list)


class ExcludedMethodologyRef(StrictModel):
    methodology_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: Literal["candidate", "disabled"]


class FrozenMethodologyManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    frozen_at: AwareDatetime
    enabled_user_methodologies: list[FrozenMethodologyRef] = Field(
        default_factory=list
    )
    excluded_user_methodologies: list[ExcludedMethodologyRef] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_entries(self) -> "FrozenMethodologyManifest":
        enabled = [item.methodology_id for item in self.enabled_user_methodologies]
        excluded = [item.methodology_id for item in self.excluded_user_methodologies]
        if len(enabled) != len(set(enabled)):
            raise ValueError("冻结清单中的启用方法论不能重复")
        if len(excluded) != len(set(excluded)):
            raise ValueError("冻结清单中的未启用方法论不能重复")
        if set(enabled) & set(excluded):
            raise ValueError("同一方法论不能同时启用和排除")
        return self


class MethodologySelectionRef(StrictModel):
    methodology_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    origin: Literal["user"] = "user"
    title: NonEmptyText
    source_item_ids: list[NonEmptyText] = Field(min_length=1)
    applicable_when: list[NonEmptyText] = Field(min_length=1)
    exceptions: list[NonEmptyText] = Field(default_factory=list)


class BuiltinMethodologySelectionRef(StrictModel):
    methodology_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    origin: Literal["builtin"] = "builtin"
    title: NonEmptyText
    applicable_when: list[NonEmptyText] = Field(min_length=1)
    exceptions: list[NonEmptyText] = Field(default_factory=list)


class FrozenMethodologyCatalog(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    frozen_at: AwareDatetime
    enabled_user_methodologies: list[MethodologySelectionRef] = Field(
        default_factory=list
    )
    builtin_methodologies: list[BuiltinMethodologySelectionRef] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_entries(self) -> "FrozenMethodologyCatalog":
        user_identifiers = [
            item.methodology_id for item in self.enabled_user_methodologies
        ]
        builtin_identifiers = [
            item.methodology_id for item in self.builtin_methodologies
        ]
        if len(user_identifiers) != len(set(user_identifiers)):
            raise ValueError("方法论精简目录中的 methodology_id 不能重复")
        if len(builtin_identifiers) != len(set(builtin_identifiers)):
            raise ValueError("内置方法论精简目录中的 methodology_id 不能重复")
        overlap = set(user_identifiers) & set(builtin_identifiers)
        if overlap:
            raise ValueError(f"用户方法论与内置方法论 ID 冲突：{sorted(overlap)}")
        return self


class MethodologyDerivationTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["methodology_derivation"] = "methodology_derivation"
    task_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    created_at: AwareDatetime | None = None
    data_root: str = Field(min_length=1)
    source_asset_ids: list[NonEmptyText] = Field(min_length=1)
    source_items_path: str = Field(min_length=1)
    existing_methodologies_path: str = Field(min_length=1)
    result_schema_path: str = Field(min_length=1)
    result_path: str = Field(min_length=1)


class MethodologyDerivationReceipt(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str = Field(min_length=1)
    completed_at: AwareDatetime
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    imported: dict


def utc_now() -> datetime:
    return datetime.now().astimezone()
