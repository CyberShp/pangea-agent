from __future__ import annotations

from pydantic import BaseModel, Field


class TaskContract(BaseModel):
    run_id: str = "RUN-local"
    mode: str = "module_analysis"
    repository: str
    target: str
    source_scope: list[str] = Field(default_factory=list)
