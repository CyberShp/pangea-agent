from __future__ import annotations

from pathlib import Path

from .types import EvidenceChunk


def chunk_text(
    text: str,
    *,
    path: str,
    source_type: str,
    repo_id: str | None = None,
    max_lines: int = 120,
    tags: tuple[str, ...] = (),
) -> list[EvidenceChunk]:
    lines = text.splitlines()
    chunks: list[EvidenceChunk] = []
    for start in range(0, len(lines), max_lines):
        end = min(start + max_lines, len(lines))
        content = "\n".join(lines[start:end])
        chunk_id = f"{repo_id or source_type}:{path}:{start + 1}-{end}"
        chunks.append(EvidenceChunk(chunk_id, source_type, repo_id, path, start + 1, end, content, tags))
    return chunks


def chunk_text_file(path: Path, *, source_type: str, repo_id: str | None = None, root: Path | None = None, max_lines: int = 120) -> list[EvidenceChunk]:
    text = path.read_text(encoding="utf-8", errors="replace")
    relative = path.relative_to(root) if root and path.is_relative_to(root) else path
    return chunk_text(text, path=relative.as_posix(), source_type=source_type, repo_id=repo_id, max_lines=max_lines)
