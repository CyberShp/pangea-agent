from __future__ import annotations

from pathlib import Path

from .chunker import chunk_text_file
from .store import upsert_chunks

SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".md", ".txt"}


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "build", "dist", "third_party", "node_modules", "__pycache__"} for part in path.parts):
            continue
        if path.suffix.lower() in SOURCE_SUFFIXES:
            yield path


def build_run_index(index_path: Path, repositories: list[dict], module_scope: list[str], data_root: Path) -> dict:
    chunks = []
    file_count = 0
    for repo in repositories:
        repo_id = repo["repo_id"]
        root = Path(repo["source_root"])
        scopes = module_scope or ["."]
        for scope in scopes:
            scoped_root = root / scope
            if not scoped_root.exists():
                continue
            for path in _iter_files(scoped_root):
                file_count += 1
                chunks.extend(chunk_text_file(path, source_type="code", repo_id=repo_id, root=root))
    for folder, source_type in (("inbox", "material"), ("coverage", "coverage"), ("testcases", "testcase")):
        source_root = data_root / folder
        if source_root.exists():
            for path in _iter_files(source_root):
                file_count += 1
                chunks.extend(chunk_text_file(path, source_type=source_type, repo_id=None, root=data_root))
    upsert_chunks(index_path, chunks)
    return {"index_path": str(index_path), "file_count": file_count, "chunk_count": len(chunks)}
