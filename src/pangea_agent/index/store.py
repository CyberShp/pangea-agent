from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .types import EvidenceChunk


def init_store(index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(index_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, source_type TEXT, repo_id TEXT, path TEXT, line_start INTEGER, line_end INTEGER, content TEXT, tags TEXT NOT NULL DEFAULT '[]')")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
        if "tags" not in columns:
            conn.execute("ALTER TABLE chunks ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id, content)")


def upsert_chunks(index_path: Path, chunks: list[EvidenceChunk]) -> None:
    init_store(index_path)
    with sqlite3.connect(index_path) as conn:
        for chunk in chunks:
            conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk.chunk_id,))
            conn.execute(
                "INSERT OR REPLACE INTO chunks (chunk_id, source_type, repo_id, path, line_start, line_end, content, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chunk.chunk_id, chunk.source_type, chunk.repo_id, chunk.path, chunk.line_start, chunk.line_end, chunk.content, json.dumps(chunk.tags, ensure_ascii=False)),
            )
            conn.execute("INSERT INTO chunks_fts(chunk_id, content) VALUES (?, ?)", (chunk.chunk_id, chunk.content))


def replace_source_chunks(
    index_path: Path,
    *,
    source_type: str,
    repo_id: str | None,
    path: str,
    chunks: list[EvidenceChunk],
) -> None:
    """Replace one source atomically so shortened or changed files leave no stale chunks."""
    init_store(index_path)
    with sqlite3.connect(index_path) as conn:
        identifiers = [row[0] for row in conn.execute(
            "SELECT chunk_id FROM chunks WHERE source_type = ? AND repo_id IS ? AND path = ?",
            (source_type, repo_id, path),
        )]
        conn.executemany("DELETE FROM chunks_fts WHERE chunk_id = ?", ((value,) for value in identifiers))
        conn.execute(
            "DELETE FROM chunks WHERE source_type = ? AND repo_id IS ? AND path = ?",
            (source_type, repo_id, path),
        )
        for chunk in chunks:
            conn.execute(
                "INSERT INTO chunks (chunk_id, source_type, repo_id, path, line_start, line_end, content, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chunk.chunk_id, chunk.source_type, chunk.repo_id, chunk.path, chunk.line_start, chunk.line_end, chunk.content, json.dumps(chunk.tags, ensure_ascii=False)),
            )
            conn.execute("INSERT INTO chunks_fts(chunk_id, content) VALUES (?, ?)", (chunk.chunk_id, chunk.content))


def clear_source_types(index_path: Path, source_types: tuple[str, ...]) -> None:
    init_store(index_path)
    placeholders = ",".join("?" for _ in source_types)
    with sqlite3.connect(index_path) as conn:
        identifiers = [row[0] for row in conn.execute(
            f"SELECT chunk_id FROM chunks WHERE source_type IN ({placeholders})",
            source_types,
        )]
        conn.executemany("DELETE FROM chunks_fts WHERE chunk_id = ?", ((value,) for value in identifiers))
        conn.execute(f"DELETE FROM chunks WHERE source_type IN ({placeholders})", source_types)
