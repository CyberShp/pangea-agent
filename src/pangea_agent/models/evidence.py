from __future__ import annotations

from pydantic import BaseModel


class EvidenceRef(BaseModel):
    chunk_id: str
    location: str
    observation: str
