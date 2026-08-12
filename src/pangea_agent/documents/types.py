from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvidenceAttachment:
    source_path: str
    attachment_path: str
    location: str
    media_type: str


@dataclass(frozen=True)
class DocumentExtraction:
    text: str
    attachments: list[EvidenceAttachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
