from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    location: str | None = None
    observation: str = Field(min_length=1)
