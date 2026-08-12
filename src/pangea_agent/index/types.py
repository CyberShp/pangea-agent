from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    source_type: str
    repo_id: str | None
    path: str
    line_start: int | None
    line_end: int | None
    content: str

    @property
    def location(self) -> str:
        if self.line_start is not None and self.line_end is not None:
            return f"{self.path}:{self.line_start}-{self.line_end}"
        return self.path
