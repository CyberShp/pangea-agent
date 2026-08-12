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
    tags: tuple[str, ...] = ()

    @property
    def location(self) -> str:
        prefix = f"{self.repo_id}:" if self.repo_id else ""
        if self.line_start is not None and self.line_end is not None:
            return f"{prefix}{self.path}:{self.line_start}-{self.line_end}"
        return f"{prefix}{self.path}"
