from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    data_root: str = "pangea-data"
    # New runs are explicitly versioned by the caller.  ``None`` is retained
    # for old frozen contracts, which remain read-only compatible.
    workflow_version: Literal["legacy-v1", "source-first-v1"] | None = None
    runtime_commit: str | None = None
    model_id: str | None = None
    effective_context_budget: int | None = Field(default=None, gt=0)
    mode: Literal["module_analysis", "mr_analysis"] = "module_analysis"
    repository: str | None = None
    repositories: list[str] = Field(default_factory=list)
    target: str
    source_scope: list[str] = Field(default_factory=list)
    context_scope: list[str] | None = Field(
        default=None,
        description=(
            "仅用于理解和证据定位的冻结上下文路径；不产生源码覆盖义务，"
            "且不会触发展开声明实现。"
        ),
    )
    focus: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    test_case_examples: list[str] = Field(default_factory=list)
    mr_url: str | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "TaskContract":
        if bool(self.repository) == bool(self.repositories):
            raise ValueError("任务契约必须且只能指定 repository 或 repositories")
        if self.mode == "mr_analysis" and not self.mr_url:
            raise ValueError("MR 分析必须提供 mr_url；内部 Agent MCP 不可用时应终止任务")
        return self
