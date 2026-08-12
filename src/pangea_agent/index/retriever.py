from __future__ import annotations

import sqlite3
from pathlib import Path


def search_evidence(index_path: Path, query: str, top_k: int = 10) -> list[dict[str, str]]:
    if not index_path.exists():
        return []
    safe_query = query.replace('"', ' ')
    with sqlite3.connect(index_path) as conn:
        rows = conn.execute(
            "SELECT c.chunk_id, c.source_type, c.repo_id, c.path, c.line_start, c.line_end, c.content "
            "FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.chunk_id "
            "WHERE chunks_fts MATCH ? LIMIT ?",
            (safe_query, top_k),
        ).fetchall()
    results = []
    for chunk_id, source_type, repo_id, path, line_start, line_end, content in rows:
        location = f"{path}:{line_start}-{line_end}" if line_start and line_end else path
        results.append({
            "chunk_id": chunk_id,
            "source_type": source_type,
            "repo_id": repo_id or "",
            "path": path,
            "location": location,
            "content": content,
        })
    return results
