from __future__ import annotations

import sqlite3
from pathlib import Path

from .types import EvidenceChunk


def init_store(index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(index_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, source_type TEXT, repo_id TEXT, path TEXT, line_start INTEGER, line_end INTEGER, content TEXT)")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id, content)")


def upsert_chunks(index_path: Path, chunks: list[EvidenceChunk]) -> None:
    init_store(index_path)
    with sqlite3.connect(index_path) as conn:
        for chunk in chunks:
            conn.execute(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chunk.chunk_id, chunk.source_type, chunk.repo_id, chunk.path, chunk.line_start, chunk.line_end, chunk.content),
            )
            conn.execute("INSERT OR REPLACE INTO chunks_fts(chunk_id, content) VALUES (?, ?)", (chunk.chunk_id, chunk.content))
