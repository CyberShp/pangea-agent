"""Atomic, append-oriented storage for source-first Agent notes.

This store owns the envelope and revision.  It never interprets the prose in
``record.body``.  A malformed request or a stale revision is reported to the
caller; a valid but unknown ``kind`` or relation is preserved with a warning so
the rest of a Run remains readable.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.models.source_first import (
    CompletionDeclaration,
    NoteRecord,
    NotesResult,
    SourceBinding,
)


class ResultStoreError(ValueError):
    """A deterministic result-store contract error."""


class RevisionConflict(ResultStoreError):
    def __init__(self, path: Path, expected: int, current: int) -> None:
        self.path = path
        self.expected_revision = expected
        self.current_revision = current
        super().__init__(
            f"result revision conflict: expected={expected} current={current} path={path}"
        )


class ResultAlreadyBound(ResultStoreError):
    pass


_KNOWN_KINDS = {
    "summary",
    "flow",
    "risk",
    "test_case",
    "finding",
    "note",
    "unresolved",
    "unit_plan",
    "review_decision",
    "completion",
}


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Lock a small sidecar file on both Windows and POSIX hosts."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _resolved_data_root(value: str) -> str:
    return str(Path(value).resolve())


def _empty_result(binding: SourceBinding) -> NotesResult:
    return NotesResult(binding=binding, revision=0)


def initialize_result(path: str | Path, binding: SourceBinding) -> None:
    """Create the Graph-owned result shell exactly once."""

    path = Path(path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, _empty_result(binding).model_dump(mode="json"))


def _load(path: Path) -> NotesResult:
    try:
        raw = read_json(path)
    except FileNotFoundError as exc:
        raise ResultStoreError(f"result_path 不存在：{path}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ResultStoreError(f"result_path 不可读取：{path}: {exc}") from exc
    try:
        return NotesResult.model_validate(raw)
    except ValueError as exc:
        raise ResultStoreError(f"pangea-notes-v1 结果外壳损坏：{path}: {exc}") from exc


def read_result(path: str | Path) -> NotesResult:
    return _load(Path(path))


def _assert_binding(stored: NotesResult, binding: SourceBinding) -> NotesResult:
    if _resolved_data_root(stored.binding.data_root) != _resolved_data_root(binding.data_root):
        raise ResultStoreError("result binding data_root 不一致")
    if stored.binding.run_id != binding.run_id:
        raise ResultStoreError("result binding run_id 不一致")
    if stored.binding.action_id != binding.action_id:
        raise ResultStoreError("result binding action_id 不一致")
    # The Graph creates the shell before the host has a real task id.  The
    # first bound operation seals that one slot; a later caller cannot replace
    # it with another session.
    if stored.binding.task_id == "pending":
        return stored.model_copy(update={"binding": binding})
    if stored.binding.task_id != binding.task_id:
        raise ResultStoreError("result binding task_id 不一致")
    return stored


def _atomic_mutate(path: Path, mutate: Callable[[NotesResult], tuple[NotesResult, Any]]) -> Any:
    lock_path = path.with_name(f".{path.name}.lock")
    with _file_lock(lock_path):
        current = _load(path)
        updated, response = mutate(current)
        if updated.model_dump(mode="json") != current.model_dump(mode="json"):
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, updated.model_dump(mode="json"))
        return response


def _check_revision(current: NotesResult, expected_revision: int) -> None:
    if not isinstance(expected_revision, int) or expected_revision < 0:
        raise ResultStoreError("expected_revision 必须是非负整数")
    if current.revision != expected_revision:
        raise RevisionConflict(Path("result_path"), expected_revision, current.revision)


def append_records(
    path: str | Path,
    binding: SourceBinding,
    expected_revision: int,
    records: list[dict[str, Any]],
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Append a small batch and return generated record IDs and warnings."""

    path = Path(path)
    if not isinstance(records, list) or not records:
        raise ResultStoreError("result_write 至少需要一条 record")

    def mutate(current: NotesResult) -> tuple[NotesResult, dict[str, Any]]:
        current = _assert_binding(current, binding)
        if request_id and request_id in current.receipts:
            return current, dict(current.receipts[request_id])
        _check_revision(current, expected_revision)
        revision = current.revision + 1
        warnings: list[dict[str, Any]] = []
        generated: list[NoteRecord] = []
        next_number = len(current.records) + 1
        for offset, item in enumerate(records):
            if not isinstance(item, dict) or "body" not in item:
                raise ResultStoreError(f"record[{offset}] 必须包含 body")
            kind = item.get("kind", "note")
            if not isinstance(kind, str) or not kind:
                raise ResultStoreError(f"record[{offset}].kind 必须是非空字符串")
            if kind not in _KNOWN_KINDS:
                warnings.append({
                    "kind": "unknown_record_kind",
                    "record_index": offset,
                    "record_kind": kind,
                })
            evidence = item.get("evidence", [])
            relates_to = item.get("relates_to", [])
            if not isinstance(evidence, list):
                warnings.append({
                    "kind": "invalid_evidence_shape",
                    "record_index": offset,
                    "message": "evidence must be a list; original value preserved in record",
                    "original_value": evidence,
                })
            if not isinstance(relates_to, list):
                warnings.append({
                    "kind": "invalid_relation_shape",
                    "record_index": offset,
                    "message": "relates_to must be a list; original value preserved in record",
                    "original_value": relates_to,
                })
            generated.append(NoteRecord(
                record_id=f"rec-{next_number:06d}",
                body=item["body"],
                kind=kind,
                evidence=evidence,
                relates_to=relates_to,
                created_revision=revision,
            ))
            next_number += 1
        updated = current.model_copy(update={
            "revision": revision,
            "records": [*current.records, *generated],
            "warnings": [*current.warnings, *warnings],
        })
        response = {
            "format_version": "pangea-notes-v1",
            "revision": revision,
            "record_ids": [item.record_id for item in generated],
            "warnings": warnings,
        }
        if request_id:
            receipts = {**updated.receipts, request_id: response}
            updated = updated.model_copy(update={"receipts": receipts})
        return updated, response

    try:
        return _atomic_mutate(path, mutate)
    except RevisionConflict as exc:
        # Keep the real result path in the error for client diagnostics.
        exc.path = path
        exc.args = (
            f"result revision conflict: expected={exc.expected_revision} "
            f"current={exc.current_revision} path={path}",
        )
        raise


def declare_completion(
    path: str | Path,
    binding: SourceBinding,
    expected_revision: int,
    *,
    complete: bool,
    note: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    path = Path(path)

    def mutate(current: NotesResult) -> tuple[NotesResult, dict[str, Any]]:
        current = _assert_binding(current, binding)
        if request_id and request_id in current.receipts:
            return current, dict(current.receipts[request_id])
        _check_revision(current, expected_revision)
        updated = current.model_copy(update={
            "revision": current.revision + 1,
            "completion": CompletionDeclaration(
                complete=bool(complete),
                note=str(note),
                declared_revision=current.revision + 1,
            ),
        })
        response = {
            "format_version": "pangea-notes-v1",
            "revision": updated.revision,
            "complete": bool(complete),
        }
        if request_id:
            updated = updated.model_copy(update={
                "receipts": {**updated.receipts, request_id: response},
            })
        return updated, response

    try:
        return _atomic_mutate(path, mutate)
    except RevisionConflict as exc:
        exc.path = path
        exc.args = (
            f"result revision conflict: expected={exc.expected_revision} "
            f"current={exc.current_revision} path={path}",
        )
        raise


def read_records(
    path: str | Path,
    binding: SourceBinding | None = None,
    *,
    record_id: str | None = None,
    cursor: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    result = _load(Path(path))
    if binding is not None:
        result = _assert_binding(result, binding)
    if cursor < 0 or limit < 1 or limit > 500:
        raise ResultStoreError("result_read cursor/limit 无效")
    records = result.records
    if record_id:
        records = [item for item in records if item.record_id == record_id]
    else:
        records = records[cursor : cursor + limit]
    next_cursor = None
    if not record_id and cursor + len(records) < len(result.records):
        next_cursor = cursor + len(records)
    return {
        "format_version": "pangea-notes-v1",
        "binding": result.binding.model_dump(mode="json"),
        "revision": result.revision,
        "records": [item.model_dump(mode="json") for item in records],
        "completion": result.completion.model_dump(mode="json") if result.completion else None,
        "warnings": result.warnings,
        "next_cursor": next_cursor,
    }


def is_empty_result(path: str | Path) -> bool:
    result = _load(Path(path))
    return not result.records and result.completion is None
