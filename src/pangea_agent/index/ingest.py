from __future__ import annotations

import zipfile
from pathlib import Path

from pangea_agent.documents import DependencyUnavailableError, extract_document
from pangea_agent.documents.coverage import parse_coverage_xlsx

from .chunker import chunk_text, chunk_text_file
from .store import clear_source_types, replace_source_chunks

CODE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}
DOCUMENT_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".xlsx"}
IGNORED_PARTS = {".git", "build", "dist", "third_party", "node_modules", "__pycache__", ".pangea"}


def _iter_files(root: Path, suffixes: set[str]):
    if root.is_file():
        if root.suffix.lower() in suffixes:
            yield root
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in suffixes:
            yield path


def _material_tags(path: Path, text: str) -> tuple[str, ...]:
    sample = f"{path.name}\n{text[:4000]}".lower()
    tags = ["material"]
    if any(value in sample for value in ("测试用例", "test case", "expected result", "预期结果", "测试步骤")):
        tags.append("testcase_reference")
    if any(value in sample for value in ("规格", "设计", "specification", "design")):
        tags.append("design_spec")
    if any(value in sample for value in ("协议", "protocol", "rfc", "nvme", "iscsi")):
        tags.append("protocol")
    return tuple(tags)


def _replace(index_paths: tuple[Path, ...], chunks, *, source_type: str, repo_id: str | None, path: str) -> None:
    for index_path in index_paths:
        replace_source_chunks(
            index_path,
            source_type=source_type,
            repo_id=repo_id,
            path=path,
            chunks=chunks,
        )


def build_run_index(
    index_path: Path,
    repositories: list[dict],
    module_scope: list[str],
    data_root: Path,
    scope_expansion: dict | None = None,
) -> dict:
    file_count = 0
    chunk_count = 0
    attachments: list[dict] = []
    warnings: list[dict[str, str]] = []
    missing_dependencies: list[dict[str, str]] = []
    coverage_records: list[dict] = []
    expansion = scope_expansion or {}
    context_by_repo: dict[str, list[str]] = {}
    for item in expansion.get("context_files", []):
        context_by_repo.setdefault(item["repo_id"], []).append(item["path"])
    attachments_root = data_root / ".pangea" / "evidence-attachments"
    material_index_path = data_root / ".pangea" / "materials.sqlite"
    material_source_types = ("material", "coverage", "coverage_record", "testcase")
    for target in {index_path, material_index_path}:
        clear_source_types(target, material_source_types)
    for repo in repositories:
        repo_id = repo["repo_id"]
        root = Path(repo["source_root"])
        scopes = module_scope or ["."]
        for scope in scopes:
            scoped_root = root / scope
            if not scoped_root.exists():
                continue
            for path in _iter_files(scoped_root, CODE_SUFFIXES):
                file_count += 1
                chunks = chunk_text_file(path, source_type="code", repo_id=repo_id, root=root)
                relative_path = path.relative_to(root).as_posix()
                _replace((index_path,), chunks, source_type="code", repo_id=repo_id, path=relative_path)
                chunk_count += len(chunks)
        for relative_path in context_by_repo.get(repo_id, []):
            path = root / relative_path
            if not path.is_file():
                warnings.append({"path": relative_path, "warning": "expanded context file is missing"})
                continue
            file_count += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            chunks = chunk_text(
                text,
                path=relative_path,
                source_type="source_context",
                repo_id=repo_id,
                tags=("source_context", "upstream_semantics"),
            )
            _replace(
                (index_path,),
                chunks,
                source_type="source_context",
                repo_id=repo_id,
                path=relative_path,
            )
            chunk_count += len(chunks)
    for folder, source_type in (("inbox", "material"), ("coverage", "coverage")):
        source_root = data_root / folder
        if source_root.exists():
            for path in _iter_files(source_root, DOCUMENT_SUFFIXES):
                file_count += 1
                relative_path = path.relative_to(data_root).as_posix()
                try:
                    extracted = extract_document(path, attachments_root)
                except DependencyUnavailableError as exc:
                    missing_dependencies.append({
                        "path": relative_path,
                        "package": exc.package,
                        "document_type": exc.document_type,
                        "error": str(exc),
                    })
                    continue
                except (OSError, ValueError, zipfile.BadZipFile) as exc:
                    warnings.append({"path": relative_path, "warning": f"{type(exc).__name__}: {exc}"})
                    continue
                except Exception as exc:
                    warnings.append({"path": relative_path, "warning": f"{type(exc).__name__}: {exc}"})
                    continue
                tags = _material_tags(path, extracted.text) if source_type == "material" else (source_type,)
                chunks = chunk_text(
                    extracted.text,
                    path=relative_path,
                    source_type=source_type,
                    tags=tags,
                )
                _replace((index_path, material_index_path), chunks, source_type=source_type, repo_id=None, path=relative_path)
                chunk_count += len(chunks)
                attachments.extend({
                    "source_path": relative_path,
                    "attachment_path": item.attachment_path,
                    "location": item.location,
                    "media_type": item.media_type,
                    "status": "awaiting_visual_analysis",
                } for item in extracted.attachments)
                warnings.extend({"path": relative_path, "warning": value} for value in extracted.warnings)
                missing_dependencies.extend({
                    "path": relative_path,
                    "package": package,
                    "document_type": f"{path.suffix.lower()} attachment",
                    "error": f"attachment extraction requires the '{package}' package",
                } for package in extracted.missing_dependencies)

                if source_type == "coverage" and path.suffix.lower() == ".xlsx":
                    records, coverage_warnings = parse_coverage_xlsx(path)
                    coverage_records.extend(records)
                    coverage_text = "\n".join(
                        f"module={record['module']}\tfunction={record['function']}\tcount={record['count']}\t"
                        f"sheet={record['sheet']}\trow={record['row']}"
                        for record in records
                    )
                    coverage_chunks = chunk_text(
                        coverage_text,
                        path=f"{relative_path}#coverage-records",
                        source_type="coverage_record",
                        tags=("coverage", "function_execution_reference"),
                    )
                    _replace(
                        (index_path, material_index_path),
                        coverage_chunks,
                        source_type="coverage_record",
                        repo_id=None,
                        path=f"{relative_path}#coverage-records",
                    )
                    chunk_count += len(coverage_chunks)
                    warnings.extend({"path": relative_path, "warning": value} for value in coverage_warnings)
    return {
        "index_path": str(index_path),
        "material_index_path": str(material_index_path),
        "file_count": file_count,
        "chunk_count": chunk_count,
        "attachments": attachments,
        "warnings": warnings,
        "missing_dependencies": missing_dependencies,
        "coverage_records": coverage_records,
        "scope_expansion": expansion,
        "repository_versions": [
            {"repo_id": repo["repo_id"], "git": repo.get("git", {"is_git": False})}
            for repo in repositories
        ],
    }
