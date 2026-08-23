from __future__ import annotations

import sqlite3
import json
import re
from pathlib import Path


SYNONYMS = {
    "nvme-tcp": ("nvme tcp", "nvmetcp"),
    "chap": ("challenge handshake authentication protocol", "认证"),
    "tls": ("transport layer security", "加密"),
    "iscsi": ("internet small computer systems interface",),
    "可靠性": ("reliability", "故障恢复"),
    "并发": ("concurrency", "竞态", "race"),
}


def _evidence_role(tags: list[str]) -> str:
    return "reference_only" if "reference_only" in tags or "testcase_reference" in tags else "evidence"


def _terms(query: str) -> list[str]:
    terms = [value for value in re.split(r"[\s,，;；/]+", query.strip()) if value]
    lowered = query.lower()
    for key, aliases in SYNONYMS.items():
        if key in lowered:
            terms.extend(aliases)
    # LIKE on short character groups makes Chinese queries useful without a tokenizer plugin.
    for token in list(terms):
        if re.search(r"[\u3400-\u9fff]", token) and len(token) > 2:
            terms.extend(token[index:index + 2] for index in range(len(token) - 1))
    return list(dict.fromkeys(terms))


def search_evidence(index_path: Path, query: str, top_k: int = 10) -> list[dict[str, object]]:
    if not index_path.exists():
        return []
    terms = _terms(query)
    if not terms:
        return []
    match_query = " OR ".join(f'"{term.replace(chr(34), " ")}"' for term in terms)
    with sqlite3.connect(index_path) as conn:
        rows = conn.execute(
            "SELECT c.chunk_id, c.source_type, c.repo_id, c.path, c.line_start, c.line_end, c.content, c.tags "
            "FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.chunk_id "
            "WHERE chunks_fts MATCH ? LIMIT ?",
            (match_query, top_k),
        ).fetchall()
        if len(rows) < top_k:
            clauses = " OR ".join("c.content LIKE ?" for _ in terms)
            excluded = {row[0] for row in rows}
            candidates = conn.execute(
                "SELECT c.chunk_id, c.source_type, c.repo_id, c.path, c.line_start, c.line_end, c.content, c.tags "
                f"FROM chunks c WHERE {clauses} LIMIT ?",
                (*[f"%{term}%" for term in terms], top_k),
            ).fetchall()
            rows.extend(row for row in candidates if row[0] not in excluded)
            rows = rows[:top_k]
    results = []
    for chunk_id, source_type, repo_id, path, line_start, line_end, content, tags in rows:
        prefix = f"{repo_id}:" if repo_id else ""
        location = f"{prefix}{path}:{line_start}-{line_end}" if line_start and line_end else f"{prefix}{path}"
        parsed_tags = json.loads(tags or "[]")
        results.append({
            "chunk_id": chunk_id,
            "source_type": source_type,
            "repo_id": repo_id or "",
            "path": path,
            "location": location,
            "content": content,
            "tags": parsed_tags,
            "evidence_role": _evidence_role(parsed_tags),
        })
    return results


def read_material(index_path: Path, path: str) -> list[dict[str, object]]:
    if not index_path.exists():
        return []
    with sqlite3.connect(index_path) as conn:
        rows = conn.execute(
            "SELECT chunk_id, source_type, repo_id, path, line_start, line_end, content, tags "
            "FROM chunks WHERE source_type = 'material' AND path = ? "
            "ORDER BY line_start, chunk_id",
            (path,),
        ).fetchall()
    results = []
    for chunk_id, source_type, repo_id, item_path, line_start, line_end, content, tags in rows:
        location = (
            f"{item_path}:{line_start}-{line_end}"
            if line_start and line_end
            else item_path
        )
        parsed_tags = json.loads(tags or "[]")
        results.append({
            "chunk_id": chunk_id,
            "source_type": source_type,
            "repo_id": repo_id or "",
            "path": item_path,
            "location": location,
            "content": content,
            "tags": parsed_tags,
            "evidence_role": _evidence_role(parsed_tags),
        })
    return results
