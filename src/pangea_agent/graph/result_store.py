"""Atomic, append-oriented storage for source-first Agent notes.

This store owns the envelope and revision.  It never interprets the prose in
``record.body``.  A malformed request or a stale revision is reported to the
caller; a valid but unknown ``kind`` or relation is preserved with a warning so
the rest of a Run remains readable.
"""

from __future__ import annotations

import base64
import json
import hashlib
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
    "test_case_group",
    "finding",
    "note",
    "unresolved",
    "unit_plan",
    "review_decision",
    "completion",
    "branch",
    "evidence",
    "scenario",
    "review_finding",
    "blackbox_translation",
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


def supersession_map(result: NotesResult) -> dict[str, list[str]]:
    """Return explicit, valid record retirement links without judging prose."""

    known: set[str] = set()
    superseded_by: dict[str, list[str]] = {}
    for record in result.records:
        targets = record.supersedes
        if isinstance(targets, list):
            for target in targets:
                if isinstance(target, str) and target in known:
                    superseded_by.setdefault(target, []).append(record.record_id)
        known.add(record.record_id)
    return superseded_by


def active_records(result: NotesResult) -> list[NoteRecord]:
    superseded = supersession_map(result)
    return [record for record in result.records if record.record_id not in superseded]


def repair_result_shell(
    path: str | Path,
    binding: SourceBinding,
    records: list[dict[str, Any]],
    *,
    expected_sha256: str,
) -> dict[str, Any]:
    """Replace only an unreadable result shell using content resent by its bound Agent."""

    path = Path(path)
    if not isinstance(records, list):
        raise ResultStoreError("result_repair records 必须是 JSON array")
    lock_path = path.with_name(f".{path.name}.lock")
    with _file_lock(lock_path):
        try:
            _load(path)
        except ResultStoreError:
            pass
        else:
            raise ResultStoreError("result_path 当前可读取；请使用 result_write 局部修正")
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            raise ResultStoreError(f"result_path 无法读取原始字节：{path}") from exc
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if expected_sha256 != actual_sha256:
            raise ResultStoreError(
                f"corrupt result 已变化：expected_sha256={expected_sha256} actual_sha256={actual_sha256}"
            )
        try:
            raw = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("binding"), dict):
            raw_binding = raw["binding"]
            for key in ("run_id", "action_id", "task_id"):
                value = raw_binding.get(key)
                if value not in {None, "pending", getattr(binding, key)}:
                    raise ResultStoreError(f"result binding {key} 损坏或不一致，不能自动恢复")

        revision = 1 if records else 0
        generated: list[NoteRecord] = []
        for offset, item in enumerate(records):
            if not isinstance(item, dict) or "body" not in item:
                raise ResultStoreError(f"record[{offset}] 必须包含 body")
            generated.append(NoteRecord(
                record_id=f"rec-{offset + 1:06d}",
                kind=str(item.get("kind") or "note"),
                body=item["body"],
                evidence=item.get("evidence", []),
                relates_to=item.get("relates_to", []),
                supersedes=item.get("supersedes", []),
                created_revision=revision,
            ))
        backup_path = path.with_name(f"{path.name}.corrupt-{actual_sha256[:12]}")
        if not backup_path.exists():
            backup_path.write_bytes(raw_bytes)
        restored = NotesResult(binding=binding, revision=revision, records=generated)
        write_json(path, restored.model_dump(mode="json"))
        return {
            "format_version": "pangea-notes-repair-v1",
            "revision": revision,
            "record_ids": [item.record_id for item in generated],
            "corrupt_sha256": actual_sha256,
            "backup_path": str(backup_path),
        }


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
        known_record_ids = {item.record_id for item in current.records}
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
            supersedes = item.get("supersedes", [])
            if not isinstance(supersedes, list) or any(
                not isinstance(target, str) or not target for target in supersedes
            ):
                warnings.append({
                    "kind": "invalid_supersedes_shape",
                    "record_index": offset,
                    "message": "supersedes must be an array of prior record_id strings; original value preserved",
                    "original_value": supersedes,
                })
            elif len(set(supersedes)) != len(supersedes):
                warnings.append({
                    "kind": "duplicate_supersedes_record_id",
                    "record_index": offset,
                    "record_ids": supersedes,
                })
            if isinstance(supersedes, list):
                for target in supersedes:
                    if isinstance(target, str) and target not in known_record_ids:
                        warnings.append({
                            "kind": "unknown_supersedes_record_id",
                            "record_index": offset,
                            "record_id": target,
                            "message": "supersedes only affects an earlier record in this result",
                        })
            record_id = f"rec-{next_number:06d}"
            generated.append(NoteRecord(
                record_id=record_id,
                body=item["body"],
                kind=kind,
                evidence=evidence,
                relates_to=relates_to,
                supersedes=supersedes,
                created_revision=revision,
            ))
            known_record_ids.add(record_id)
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
    superseded_by = supersession_map(result)
    records = result.records
    if record_id:
        records = [item for item in records if item.record_id == record_id]
    else:
        records = records[cursor : cursor + limit]
    next_cursor = None
    if not record_id and cursor + len(records) < len(result.records):
        next_cursor = cursor + len(records)
    record_views = []
    for item in records:
        view = item.model_dump(mode="json")
        replacements = superseded_by.get(item.record_id, [])
        view["active"] = not replacements
        view["superseded_by"] = replacements
        record_views.append(view)
    return {
        "format_version": "pangea-notes-v1",
        "binding": result.binding.model_dump(mode="json"),
        "revision": result.revision,
        "records": record_views,
        "completion": result.completion.model_dump(mode="json") if result.completion else None,
        "warnings": result.warnings,
        "active_record_count": len(active_records(result)),
        "superseded_record_ids": sorted(superseded_by),
        "next_cursor": next_cursor,
    }


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _page_token(value: dict[str, Any]) -> str:
    context_sha256 = value.get("context_sha256")
    item_index = value.get("item_index")
    char_offset = value.get("char_offset")
    if (
        isinstance(context_sha256, str)
        and len(context_sha256) == 64
        and all(character in "0123456789abcdef" for character in context_sha256)
        and isinstance(item_index, int)
        and isinstance(char_offset, int)
    ):
        line_start = value.get("line_start")
        line_end = value.get("line_end")
        if isinstance(line_start, int) and isinstance(line_end, int):
            return (
                f"p2.{item_index}.{char_offset}.{context_sha256[:24]}."
                f"{line_start}.{line_end}"
            )
        # A short, punctuation-light token is less likely to be corrupted by
        # an Agent than a base64-encoded JSON blob.  The 96-bit SHA prefix
        # still binds the position to the exact selection and revision.
        return f"p1.{item_index}.{char_offset}.{context_sha256[:24]}"
    raw = _compact_json(value).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _read_page_token(value: str) -> dict[str, Any]:
    if value.startswith("p2."):
        parts = value.split(".")
        if len(parts) != 6:
            raise ResultStoreError("compact page_token 无效")
        _, raw_item_index, raw_char_offset, context_prefix, raw_line_start, raw_line_end = parts
        try:
            item_index = int(raw_item_index)
            char_offset = int(raw_char_offset)
            line_start = int(raw_line_start)
            line_end = int(raw_line_end)
        except ValueError as exc:
            raise ResultStoreError("compact page_token 无效") from exc
        if (
            len(context_prefix) != 24
            or any(character not in "0123456789abcdef" for character in context_prefix)
            or line_start < 1
            or line_end < line_start
        ):
            raise ResultStoreError("compact page_token 无效")
        return {
            "context_sha256": context_prefix,
            "item_index": item_index,
            "char_offset": char_offset,
            "line_start": line_start,
            "line_end": line_end,
        }
    if value.startswith("p1."):
        parts = value.split(".")
        if len(parts) != 4:
            raise ResultStoreError("compact page_token 无效")
        _, raw_item_index, raw_char_offset, context_prefix = parts
        try:
            item_index = int(raw_item_index)
            char_offset = int(raw_char_offset)
        except ValueError as exc:
            raise ResultStoreError("compact page_token 无效") from exc
        if (
            len(context_prefix) != 24
            or any(character not in "0123456789abcdef" for character in context_prefix)
        ):
            raise ResultStoreError("compact page_token 无效")
        return {
            "context_sha256": context_prefix,
            "item_index": item_index,
            "char_offset": char_offset,
        }
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii") + b"===")
        decoded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResultStoreError("compact page_token 无效") from exc
    if not isinstance(decoded, dict):
        raise ResultStoreError("compact page_token 无效")
    return decoded


def compact_page_resume(page_token: str | None) -> dict[str, int]:
    """Return token-owned source range fields without weakening context checks."""

    if not page_token:
        return {}
    token = _read_page_token(page_token)
    line_start = token.get("line_start")
    line_end = token.get("line_end")
    if isinstance(line_start, int) and isinstance(line_end, int):
        return {"line_start": line_start, "line_end": line_end}
    return {}


def compact_items_page(
    *,
    metadata: dict[str, Any],
    items: list[dict[str, Any]],
    token_context: dict[str, Any],
    page_token: str | None,
    max_chars: int,
) -> dict[str, Any]:
    """Return a bounded, lossless page of JSON items.

    Whole items are returned whenever they fit.  A single oversized item is
    exposed as a JSON-text fragment whose pieces can be concatenated before
    parsing.  The token binds the page position to the exact read selection.
    """

    if not isinstance(max_chars, int) or max_chars < 1_000 or max_chars > 24_000:
        raise ResultStoreError("compact max_chars 必须在 1000 到 24000 之间")
    item_index = 0
    char_offset = 0
    context_sha256 = hashlib.sha256(_compact_json(token_context).encode("utf-8")).hexdigest()
    if page_token:
        token = _read_page_token(page_token)
        token_context_sha256 = token.get("context_sha256")
        if (
            not isinstance(token_context_sha256, str)
            or not context_sha256.startswith(token_context_sha256)
        ):
            raise ResultStoreError("compact page_token 与当前读取条件或 revision 不一致")
        item_index = token.get("item_index")
        char_offset = token.get("char_offset")
        if not isinstance(item_index, int) or not isinstance(char_offset, int):
            raise ResultStoreError("compact page_token 位置无效")
    if item_index < 0 or item_index > len(items) or char_offset < 0:
        raise ResultStoreError("compact page_token 位置无效")

    def make_token(index: int, offset: int) -> str | None:
        if index >= len(items):
            return None
        value = {
            "context_sha256": context_sha256,
            "item_index": index,
            "char_offset": offset,
        }
        if token_context.get("kind") == "source-read":
            value["line_start"] = token_context.get("line_start")
            value["line_end"] = token_context.get("line_end")
        return _page_token(value)

    def response(
        page_items: list[dict[str, Any]],
        fragment: dict[str, Any] | None,
        next_index: int,
        next_offset: int,
    ) -> dict[str, Any]:
        return {
            **metadata,
            "items": page_items,
            "item_fragment": fragment,
            "next_page_token": make_token(next_index, next_offset),
        }

    page_items: list[dict[str, Any]] = []
    current = item_index
    if char_offset:
        if current >= len(items):
            raise ResultStoreError("compact page_token 片段位置无效")
        serialized = _compact_json(items[current])
        if char_offset >= len(serialized):
            raise ResultStoreError("compact page_token 片段位置无效")
        low, high = char_offset + 1, len(serialized)
        best: dict[str, Any] | None = None
        while low <= high:
            end = (low + high) // 2
            next_index = current + 1 if end == len(serialized) else current
            next_offset = 0 if end == len(serialized) else end
            fragment = {
                "item_index": current,
                "char_start": char_offset,
                "char_end": end,
                "complete": end == len(serialized),
                "text": serialized[char_offset:end],
            }
            candidate = response([], fragment, next_index, next_offset)
            if len(_compact_json(candidate)) <= max_chars:
                best = candidate
                low = end + 1
            else:
                high = end - 1
        if best is None:
            raise ResultStoreError("compact max_chars 太小，无法返回片段元数据")
        return best

    while current < len(items):
        candidate_items = [*page_items, items[current]]
        candidate = response(candidate_items, None, current + 1, 0)
        if len(_compact_json(candidate)) <= max_chars:
            page_items = candidate_items
            current += 1
            continue
        break
    if page_items or current >= len(items):
        return response(page_items, None, current, 0)

    serialized = _compact_json(items[current])
    low, high = 1, len(serialized)
    best: dict[str, Any] | None = None
    while low <= high:
        end = (low + high) // 2
        next_index = current + 1 if end == len(serialized) else current
        next_offset = 0 if end == len(serialized) else end
        fragment = {
            "item_index": current,
            "char_start": 0,
            "char_end": end,
            "complete": end == len(serialized),
            "text": serialized[:end],
        }
        candidate = response([], fragment, next_index, next_offset)
        if len(_compact_json(candidate)) <= max_chars:
            best = candidate
            low = end + 1
        else:
            high = end - 1
    if best is None:
        raise ResultStoreError("compact max_chars 太小，无法返回片段元数据")
    return best


def read_records_compact(
    path: str | Path,
    binding: SourceBinding,
    *,
    record_id: str | None = None,
    include_history: bool = False,
    page_token: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    result = _assert_binding(_load(Path(path)), binding)
    superseded_by = supersession_map(result)
    if record_id:
        selected = [item for item in result.records if item.record_id == record_id]
    elif include_history:
        selected = list(result.records)
    else:
        selected = active_records(result)
    items: list[dict[str, Any]] = []
    for record in selected:
        view = record.model_dump(mode="json")
        replacements = superseded_by.get(record.record_id, [])
        view["active"] = not replacements
        view["superseded_by"] = replacements
        items.append({"item_type": "record", "value": view})
    if result.completion is not None:
        items.append({
            "item_type": "completion",
            "value": result.completion.model_dump(mode="json"),
        })
    if include_history:
        items.extend({"item_type": "warning", "value": item} for item in result.warnings)
    metadata = {
        "format_version": "pangea-notes-compact-v1",
        "binding": result.binding.model_dump(mode="json"),
        "revision": result.revision,
        "total_record_count": len(result.records),
        "active_record_count": len(active_records(result)),
        "warning_count": len(result.warnings),
        "completion_complete": result.completion.complete if result.completion else False,
        "completion_declared_revision": (
            result.completion.declared_revision if result.completion else None
        ),
        "include_history": bool(include_history),
    }
    context = {
        "kind": "result",
        "run_id": binding.run_id,
        "action_id": binding.action_id,
        "task_id": binding.task_id,
        "revision": result.revision,
        "record_id": record_id,
        "include_history": bool(include_history),
    }
    return compact_items_page(
        metadata=metadata,
        items=items,
        token_context=context,
        page_token=page_token,
        max_chars=max_chars,
    )


def is_empty_result(path: str | Path) -> bool:
    result = _load(Path(path))
    return not result.records and result.completion is None
