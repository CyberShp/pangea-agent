from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.documents.coverage import parse_coverage_xlsx
from pangea_agent.documents.extract import extract_document
from pangea_agent.models.asset import (
    AssetExtractionResult,
    AssetRecord,
    AssetStatus,
    AssetType,
)


DOCUMENT_SUFFIXES = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".log",
    ".pdf", ".docx", ".xlsx",
}
PARSER_VERSION = "pangea-document-normalize-1"
ASSET_ALLOWED_STEPS: dict[str, list[str]] = {
    "requirement": ["02", "03", "04"],
    "design": ["02", "03", "04"],
    "coverage": ["03", "05", "07"],
    "historical_defect": ["05"],
    "reference": ["02", "03", "04", "05", "06", "07"],
    "test_case_example": ["07"],
}
SEMANTIC_ASSET_TYPES = {"requirement", "design", "historical_defect", "reference"}
EVIDENCE_ASSET_TYPES = {"coverage", "test_case_example"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _assets_root(data_root: str) -> Path:
    return Path(data_root) / "assets"


def _asset_dir(data_root: str, asset_id: str) -> Path:
    return _assets_root(data_root) / asset_id


def _record_path(data_root: str, asset_id: str) -> Path:
    return _asset_dir(data_root, asset_id) / "asset.json"


def _review_path(data_root: str, asset_id: str) -> Path:
    return _asset_dir(data_root, asset_id) / "review.json"


def _load_review(data_root: str, asset_id: str, revision: int, result_sha256: str) -> dict:
    path = _review_path(data_root, asset_id)
    if path.is_file():
        value = read_json(path)
        if value.get("revision") == revision and value.get("result_sha256") == result_sha256:
            return value
    return {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "revision": revision,
        "result_sha256": result_sha256,
        "updated_at": _now(),
        "items": [],
    }


def _review_summary(result: dict | None, review: dict | None) -> dict:
    items = result.get("items", []) if isinstance(result, dict) else []
    decisions = {item.get("item_id"): item for item in (review or {}).get("items", [])}
    counts = {"pending": 0, "accepted": 0, "rejected": 0}
    for item in items:
        decision = decisions.get(item.get("item_id"), {}).get("decision", "pending")
        counts[decision if decision in counts else "pending"] += 1
    if counts["pending"]:
        status = "pending"
    elif counts["accepted"] and counts["rejected"]:
        status = "partially_approved"
    elif counts["accepted"]:
        status = "approved"
    else:
        status = "rejected"
    return {"status": status, "counts": counts, "items": list(decisions.values())}


def _accepted_result(result: dict, review: dict) -> dict:
    accepted = {
        item.get("item_id") for item in review.get("items", [])
        if item.get("decision") == "accepted"
    }
    return {**result, "items": [item for item in result.get("items", []) if item.get("item_id") in accepted]}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _upgrade_record(record: AssetRecord) -> AssetRecord:
    """Fill 2.0 metadata while keeping 1.0 records readable in place."""
    source = Path(record.source_path)
    if not record.source_name:
        record.source_name = source.name
    if source.is_file():
        if record.source_size == 0:
            record.source_size = source.stat().st_size
        if not record.source_sha256:
            record.source_sha256 = _sha256(source)
    if record.schema_version == "1.0":
        # An old available document with no structured result has not yet
        # crossed the deterministic-normalization boundary.  It is not
        # selectable until that boundary is crossed, but remains readable.
        if (
            record.status == "available"
            and not record.result_path
            and not record.normalized_text_path
        ):
            record.status = "imported"
        record.schema_version = "2.0"
    return record


def _next_asset_id(data_root: str) -> str:
    prefix = f"asset-{datetime.now().astimezone():%y%m%d}"
    sequence = 1
    while _asset_dir(data_root, f"{prefix}-{sequence:03d}").exists():
        sequence += 1
    return f"{prefix}-{sequence:03d}"


def _save_record(data_root: str, record: AssetRecord) -> None:
    record.schema_version = "2.0"
    _upgrade_record(record)
    record.updated_at = _now()
    write_json(_record_path(data_root, record.asset_id), record.model_dump(mode="json"))


def load_asset(data_root: str, asset_id: str) -> AssetRecord:
    path = _record_path(data_root, asset_id)
    if not path.is_file():
        raise ValueError(f"资产不存在：{asset_id}")
    return _upgrade_record(AssetRecord.model_validate(read_json(path)))


def _all_asset_records(data_root: str) -> list[AssetRecord]:
    root = _assets_root(data_root)
    if not root.exists():
        return []
    return [
        load_asset(data_root, path.parent.name)
        for path in root.glob("*/asset.json")
    ]


def _duplicate_asset(data_root: str, source_sha256: str) -> AssetRecord | None:
    for record in _all_asset_records(data_root):
        if record.source_sha256 == source_sha256 and record.status != "archived":
            return record
    return None


def _validate_asset_source(source_path: Path, asset_type: AssetType) -> None:
    if not source_path.is_file():
        raise ValueError(f"资产来源不是文件：{source_path}")
    if asset_type == "coverage":
        if source_path.suffix.lower() != ".xlsx":
            raise ValueError("Coverage 当前只支持 XLSX")
    elif source_path.suffix.lower() not in DOCUMENT_SUFFIXES:
        raise ValueError(f"不支持的资料类型：{source_path.suffix or '<none>'}")


def preview_asset_import(
    data_root: str,
    source: str,
    asset_type: AssetType,
    title: str | None = None,
) -> dict:
    source_path = Path(source)
    _validate_asset_source(source_path, asset_type)
    source_sha256 = _sha256(source_path)
    duplicate = _duplicate_asset(data_root, source_sha256)
    normalized_title = (title or source_path.stem).strip()
    conflicts = [
        record for record in _all_asset_records(data_root)
        if record.status != "archived"
        and record.source_sha256 != source_sha256
        and (
            (record.source_name or "").casefold() == source_path.name.casefold()
            or record.title.casefold() == normalized_title.casefold()
        )
    ]
    return {
        "source_name": source_path.name,
        "source_size": source_path.stat().st_size,
        "source_sha256": source_sha256,
        "asset_type": asset_type,
        "title": normalized_title,
        "knowledge_kind": "semantic" if asset_type in SEMANTIC_ASSET_TYPES else "evidence",
        "duplicate": duplicate.model_dump(mode="json") if duplicate else None,
        "conflicts": [record.model_dump(mode="json") for record in conflicts],
        "allowed_strategies": [] if duplicate else [
            "create_new",
            *(["new_revision"] if conflicts else []),
        ],
    }


def _normalize_document(data_root: str, record: AssetRecord) -> AssetRecord:
    source = Path(record.source_path)
    if not source.is_file():
        record.status = "failed"
        record.last_error = f"资产原文不可读：{source}"
        _save_record(data_root, record)
        raise ValueError(record.last_error)
    try:
        extraction = extract_document(source, _asset_dir(data_root, record.asset_id) / "attachments")
    except Exception as exc:
        record.status = "failed"
        record.last_error = str(exc)
        _save_record(data_root, record)
        raise
    text_path = _asset_dir(data_root, record.asset_id) / "normalized.txt"
    text_path.write_text(extraction.text, encoding="utf-8")
    record.normalized_text_path = str(text_path)
    record.parser_version = PARSER_VERSION
    record.extraction_task_path = None
    record.result_path = None
    if record.asset_type == "historical_defect":
        _review_path(data_root, record.asset_id).unlink(missing_ok=True)
    record.structured_item_count = 0
    record.warnings = extraction.warnings
    record.last_error = None
    record.status = "available" if extraction.text.strip() else "no_items"
    if record.asset_type == "historical_defect" and extraction.text.strip():
        record.status = "awaiting_review"
        record.review_status = "pending"
    else:
        record.review_status = "not_required"
    _save_record(data_root, record)
    return record


def _ensure_normalized_text(data_root: str, record: AssetRecord) -> AssetRecord:
    """Materialize normalized text without discarding an old structured result."""
    if record.normalized_text_path and Path(record.normalized_text_path).is_file():
        return record
    source = Path(record.source_path)
    if not source.is_file():
        raise ValueError(f"资产原文不可读：{source}")
    extraction = extract_document(source, _asset_dir(data_root, record.asset_id) / "attachments")
    text_path = _asset_dir(data_root, record.asset_id) / "normalized.txt"
    text_path.write_text(extraction.text, encoding="utf-8")
    record.normalized_text_path = str(text_path)
    record.parser_version = PARSER_VERSION
    record.warnings = [*record.warnings, *extraction.warnings]
    if record.status == "imported":
        record.status = "available" if extraction.text.strip() else "no_items"
        if record.asset_type == "historical_defect" and extraction.text.strip():
            record.status = "awaiting_review"
            record.review_status = "pending"
    _save_record(data_root, record)
    return record


def import_asset(
    data_root: str,
    source: str,
    asset_type: AssetType,
    title: str | None = None,
) -> AssetRecord:
    if asset_type not in {
        "requirement",
        "design",
        "historical_defect",
        "reference",
        "coverage",
        "test_case_example",
    }:
        raise ValueError(f"不支持的资产类型：{asset_type}")
    source_path = Path(source)
    _validate_asset_source(source_path, asset_type)
    source_sha256 = _sha256(source_path)
    duplicate = _duplicate_asset(data_root, source_sha256)
    if duplicate is not None:
        raise ValueError(
            f"检测到重复资产：内容 SHA256 已存在于 {duplicate.asset_id}（revision {duplicate.revision}）"
        )
    if asset_type == "coverage":
        destination_root = Path(data_root) / "coverage"
    else:
        destination_root = Path(data_root) / "inbox"

    asset_id = _next_asset_id(data_root)
    destination = destination_root / asset_id / source_path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    now = _now()
    record = AssetRecord(
        asset_id=asset_id,
        asset_type=asset_type,
        title=title or source_path.stem,
        source_path=str(destination),
        revision=1,
        source_name=source_path.name,
        source_sha256=source_sha256,
        source_size=source_path.stat().st_size,
        created_at=now,
        updated_at=now,
    )
    _save_record(data_root, record)
    try:
        prepare_asset_extraction(data_root, asset_id)
        return load_asset(data_root, asset_id)
    except Exception:
        # The failed record is intentionally retained so the catalogue can
        # explain why import/normalization failed instead of losing the error.
        raise


def import_asset_revision(
    data_root: str,
    asset_id: str,
    source: str,
    title: str | None = None,
) -> AssetRecord:
    """Create a new immutable source revision for an existing asset."""
    record = load_asset(data_root, asset_id)
    if record.status == "archived":
        raise ValueError("已归档资产不能创建新修订")
    source_path = Path(source)
    _validate_asset_source(source_path, record.asset_type)
    source_sha256 = _sha256(source_path)
    if source_sha256 == record.source_sha256:
        raise ValueError("新修订与当前资产内容相同")
    duplicate = _duplicate_asset(data_root, source_sha256)
    if duplicate is not None and duplicate.asset_id != asset_id:
        raise ValueError(f"检测到重复资产：内容已存在于 {duplicate.asset_id}")
    current_revision_root = _asset_dir(data_root, asset_id) / "revisions" / f"r{record.revision:04d}" / "source"
    current_revision_root.mkdir(parents=True, exist_ok=True)
    current_source = Path(record.source_path)
    if current_source.is_file():
        shutil.copy2(current_source, current_revision_root / (record.source_name or current_source.name))
    current_review = _review_path(data_root, asset_id)
    if current_review.is_file():
        review_archive = _asset_dir(data_root, asset_id) / "revisions" / f"r{record.revision:04d}" / "review.json"
        review_archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current_review, review_archive)
    new_revision = record.revision + 1
    destination = _asset_dir(data_root, asset_id) / "revisions" / f"r{new_revision:04d}" / "source" / source_path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    record.revision = new_revision
    record.source_path = str(destination)
    record.source_name = source_path.name
    record.source_sha256 = source_sha256
    record.source_size = source_path.stat().st_size
    if title:
        record.title = title
    record.normalized_text_path = None
    record.result_path = None
    record.structured_item_count = 0
    record.warnings = []
    record.last_error = None
    record.status = "imported"
    _review_path(data_root, asset_id).unlink(missing_ok=True)
    _save_record(data_root, record)
    if record.asset_type == "coverage":
        prepare_asset_extraction(data_root, asset_id)
        return load_asset(data_root, asset_id)
    return _normalize_document(data_root, record)


def list_assets(
    data_root: str,
    *,
    cursor: int = 0,
    limit: int = 50,
    asset_type: str | None = None,
    status: str | None = None,
    query: str | None = None,
    knowledge_kind: str | None = None,
    repository_id: str | None = None,
    module_tag: str | None = None,
) -> dict:
    if cursor < 0:
        raise ValueError("cursor 不能小于 0")
    if limit < 1 or limit > 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    if knowledge_kind not in {None, "", "semantic", "evidence"}:
        raise ValueError("knowledge_kind 必须是 semantic 或 evidence")
    all_records = _all_asset_records(data_root)
    summary = {
        "total": len(all_records),
        "semantic": sum(record.asset_type in SEMANTIC_ASSET_TYPES for record in all_records),
        "evidence": sum(record.asset_type in EVIDENCE_ASSET_TYPES for record in all_records),
        "available": sum(record.status == "available" for record in all_records),
        "review": sum(record.status == "awaiting_review" for record in all_records),
        "failed": sum(record.status == "failed" for record in all_records),
        "archived": sum(record.status == "archived" for record in all_records),
    }
    records = []
    normalized_query = (query or "").strip().casefold()
    normalized_repository = (repository_id or "").strip().casefold()
    normalized_module = (module_tag or "").strip().casefold()
    allowed_types = (
        SEMANTIC_ASSET_TYPES if knowledge_kind == "semantic"
        else EVIDENCE_ASSET_TYPES if knowledge_kind == "evidence"
        else None
    )
    for record in all_records:
        if allowed_types is not None and record.asset_type not in allowed_types:
            continue
        if asset_type and record.asset_type != asset_type:
            continue
        if status and record.status != status:
            continue
        if normalized_repository and not any(
            normalized_repository == str(value).strip().casefold()
            for value in record.repository_ids
        ):
            continue
        if normalized_module and not any(
            normalized_module == str(value).strip().casefold()
            for value in record.module_tags
        ):
            continue
        if normalized_query and normalized_query not in "\n".join((
            record.asset_id,
            record.title,
            record.source_path,
        )).casefold():
            continue
        records.append(record)
    records.sort(key=lambda item: (item.created_at, item.asset_id), reverse=True)
    page = records[cursor : cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "items": [item.model_dump(mode="json") for item in page],
        "next_cursor": next_cursor if next_cursor < len(records) else None,
        "total": len(records),
        "summary": summary,
    }


def asset_detail(data_root: str, asset_id: str) -> dict:
    record = load_asset(data_root, asset_id)
    result = None
    if record.result_path and Path(record.result_path).is_file():
        result = read_json(Path(record.result_path))
    normalized_preview = None
    if record.normalized_text_path and Path(record.normalized_text_path).is_file():
        normalized_preview = Path(record.normalized_text_path).read_text(
            encoding="utf-8", errors="replace"
        )[:4000]
    integrity = {
        "source_readable": Path(record.source_path).is_file(),
        "source_sha256_matches": (
            bool(record.source_sha256)
            and Path(record.source_path).is_file()
            and _sha256(Path(record.source_path)) == record.source_sha256
        ),
    }
    review = None
    if record.asset_type == "historical_defect" and isinstance(result, dict):
        result_sha256 = _sha256(Path(record.result_path)) if record.result_path and Path(record.result_path).is_file() else ""
        review = _load_review(data_root, asset_id, record.revision, result_sha256)
        review = {**review, **_review_summary(result, review)}
    return {
        "asset": record.model_dump(mode="json"),
        "result": result,
        "normalized_preview": normalized_preview,
        "integrity": integrity,
        "review": review,
        "allowed_steps": ASSET_ALLOWED_STEPS[record.asset_type],
        "failure_record": {
            "asset_id": record.asset_id,
            "title": record.title,
            "revision": record.revision,
            "source_name": record.source_name,
            "source_sha256": record.source_sha256,
            "last_error": record.last_error,
            "warnings": record.warnings,
            "updated_at": record.updated_at,
        } if record.status == "failed" else None,
    }


def freeze_asset_inputs(
    data_root: str | Path,
    run_root: str | Path,
    run_id: str,
    asset_ids: list[str] | None = None,
) -> dict:
    """Freeze selected assets into an immutable Run-local input manifest."""
    root = Path(data_root)
    destination_root = Path(run_root) / "inputs" / "assets"
    selected_ids = list(dict.fromkeys(asset_ids or []))
    manifest_items: list[dict] = []
    for asset_id in selected_ids:
        record = load_asset(str(root), asset_id)
        if record.status in {"available", "imported"} and not record.normalized_text_path:
            record = _ensure_normalized_text(str(root), record)
        if record.status != "available":
            raise ValueError(f"资产不可用于新分析：{asset_id}（status={record.status}）")
        if record.review_status not in {"not_required", "approved", "partially_approved"}:
            raise ValueError(f"资产尚未通过审核：{asset_id}")
        source = Path(record.source_path)
        if not source.is_file():
            raise ValueError(f"资产原文不可读：{asset_id} -> {source}")
        actual_sha = _sha256(source)
        if not record.source_sha256 or actual_sha != record.source_sha256:
            raise ValueError(f"资产完整性校验失败：{asset_id}")
        if record.normalized_text_path:
            normalized = Path(record.normalized_text_path)
            if not normalized.is_file():
                raise ValueError(f"资产规范化文本不可读：{asset_id} -> {normalized}")
        else:
            raise ValueError(f"资产尚未完成规范化：{asset_id}")
        item_root = destination_root / asset_id / f"revision-{record.revision:04d}"
        source_destination = item_root / "source" / (record.source_name or source.name)
        source_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, source_destination)
        normalized_destination = item_root / "normalized.txt"
        shutil.copy2(Path(record.normalized_text_path), normalized_destination)
        frozen_result = None
        result = None
        if record.result_path and Path(record.result_path).is_file():
            frozen_result = item_root / "structured.json"
            result = read_json(Path(record.result_path))
            review = None
            if record.asset_type == "historical_defect":
                result_sha256 = _sha256(Path(record.result_path))
                review = _load_review(str(root), record.asset_id, record.revision, result_sha256)
                if _review_summary(result, review)["status"] == "pending":
                    raise ValueError(f"资产仍有未审核的历史缺陷：{asset_id}")
                result = _accepted_result(result, review)
            write_json(frozen_result, result)
        manifest_items.append({
            "asset_id": record.asset_id,
            "revision": record.revision,
            "asset_type": record.asset_type,
            "title": record.title,
            "source_name": record.source_name or source.name,
            "source_sha256": record.source_sha256,
            "source_size": record.source_size,
            "frozen_source_path": str(source_destination),
            "frozen_normalized_text_path": str(normalized_destination),
            "frozen_result_path": str(frozen_result) if frozen_result else None,
            "review_status": record.review_status,
            "accepted_item_count": len(result.get("items", [])) if isinstance(result, dict) else None,
            "allowed_steps": ASSET_ALLOWED_STEPS[record.asset_type],
        })
    manifest = {
        "schema_version": "2.0",
        "run_id": run_id,
        "frozen_at": _now(),
        "assets": manifest_items,
    }
    write_json(destination_root / "manifest.json", manifest)
    return manifest


def analysis_asset_inputs(data_root: str, asset_ids: list[str] | None = None) -> dict:
    selected = set(asset_ids or [])
    page = list_assets(data_root, limit=200)
    records = page["items"]
    if page["next_cursor"] is not None:
        cursor = page["next_cursor"]
        while cursor is not None:
            next_page = list_assets(data_root, cursor=cursor, limit=200)
            records.extend(next_page["items"])
            cursor = next_page["next_cursor"]

    candidates: list[dict] = []
    items: dict[str, dict] = {}
    coverage_records: list[dict] = []
    for raw_record in records:
        record = AssetRecord.model_validate(raw_record)
        if selected and record.asset_id not in selected:
            continue
        if record.status != "available" or not record.result_path:
            continue
        result = read_json(Path(record.result_path))
        if record.asset_type == "coverage":
            for number, coverage in enumerate(result.get("records", []), 1):
                coverage_records.append({
                    **coverage,
                    "coverage_id": f"{record.asset_id}:C{number:04d}",
                    "asset_id": record.asset_id,
                })
            continue
        if record.asset_type == "historical_defect":
            review = _load_review(
                data_root,
                record.asset_id,
                record.revision,
                _sha256(Path(record.result_path)),
            )
            result = _accepted_result(result, review)
        extraction = AssetExtractionResult.model_validate(result)
        for item in extraction.items:
            candidate_id = f"{record.asset_id}:{item.item_id}"
            payload = item.model_dump(mode="json")
            payload.update({
                "candidate_id": candidate_id,
                "asset_id": record.asset_id,
                "asset_title": record.title,
            })
            items[candidate_id] = payload
            candidates.append({
                "candidate_id": candidate_id,
                "asset_type": record.asset_type,
                "title": payload.get("title") or payload.get("topic"),
                "summary": payload.get("defect_mechanism")
                or payload.get("root_cause")
                or payload.get("main_flows")
                or payload.get("acceptance_criteria")
                or payload.get("key_facts")
                or [],
                "applicable_modules": payload.get("modules")
                or payload.get("applicable_modules")
                or [],
                "source_references": payload.get("source_references", []),
            })
    return {
        "candidates": candidates,
        "items": items,
        "coverage_records": coverage_records,
    }


def prepare_asset_extraction(data_root: str, asset_id: str) -> dict:
    record = load_asset(data_root, asset_id)
    if record.status == "archived":
        raise ValueError("已归档资产不能开始提取")
    source = Path(record.source_path)
    asset_dir = _asset_dir(data_root, asset_id)
    if not source.is_file():
        record.status = "failed"
        record.last_error = f"资产原文不可读：{source}"
        _save_record(data_root, record)
        raise ValueError(record.last_error)

    if record.asset_type == "coverage":
        records, warnings = parse_coverage_xlsx(source)
        result_path = asset_dir / "coverage.json"
        write_json(result_path, {"records": records, "warnings": warnings})
        normalized_path = asset_dir / "normalized.txt"
        normalized_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in records),
            encoding="utf-8",
        )
        record.result_path = str(result_path)
        record.normalized_text_path = str(normalized_path)
        record.parser_version = "pangea-coverage-parser-1"
        record.structured_item_count = len(records)
        record.warnings = warnings
        record.last_error = None
        record.status = "available" if records else "no_items"
        _save_record(data_root, record)
        return {"asset": record.model_dump(mode="json"), "action": None}

    record = _normalize_document(data_root, record)
    return {
        "asset": record.model_dump(mode="json"),
        "action": None,
    }


def complete_asset_extraction(data_root: str, asset_id: str) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    if record.asset_type == "coverage":
        raise ValueError("Coverage 提取由 Python 直接完成")
    if record.status != "extracting" or not record.result_path:
        raise ValueError("资产当前没有等待提交的提取任务")
    result_path = Path(record.result_path)
    if not result_path.is_file():
        raise ValueError(f"提取结果不存在：{result_path}")
    result = AssetExtractionResult.model_validate(read_json(result_path))
    return _accept_extraction_result(data_root, record, result)


def _accept_extraction_result(
    data_root: str,
    record: AssetRecord,
    result: AssetExtractionResult,
) -> AssetRecord:
    if result.asset_id != record.asset_id:
        raise ValueError("提取结果 asset_id 与任务不一致")
    invalid_types = {
        item.item_type for item in result.items if item.item_type != record.asset_type
    }
    if invalid_types:
        raise ValueError(f"提取结果类型与资产类型不一致：{sorted(invalid_types)}")
    result_path = Path(
        record.result_path
        or _asset_dir(data_root, record.asset_id) / "extraction-result.json"
    )
    record.result_path = str(result_path)
    write_json(result_path, result.model_dump(mode="json"))
    _review_path(data_root, record.asset_id).unlink(missing_ok=True)
    record.structured_item_count = len(result.items)
    record.warnings = [*record.warnings, *result.warnings]
    if record.asset_type == "historical_defect":
        record.status = "awaiting_review"
        record.review_status = "pending"
    else:
        record.status = "available" if result.items else "no_items"
        record.review_status = "not_required"
    _save_record(data_root, record)
    return record


def update_asset_result(data_root: str, asset_id: str, source: str) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    if record.asset_type == "coverage":
        raise ValueError("Coverage 结果不能作为语义提取结果修改")
    if record.status not in {"awaiting_review", "available", "no_items"}:
        raise ValueError("当前资产状态不允许修改结构化结果")
    source_path = Path(source)
    if not source_path.is_file():
        raise ValueError(f"结构化结果不存在：{source_path}")
    result = AssetExtractionResult.model_validate(read_json(source_path))
    return _accept_extraction_result(data_root, record, result)


def review_asset_items(
    data_root: str,
    asset_id: str,
    revision: int,
    result_sha256: str,
    decisions: list[dict],
) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    if record.asset_type != "historical_defect":
        raise ValueError("只有历史缺陷支持逐条审核")
    if record.revision != revision:
        raise ValueError("资产 revision 已变化，请刷新后重新审核")
    if not record.result_path or not Path(record.result_path).is_file():
        raise ValueError("资产没有可审核的结构化结果")
    actual_sha256 = _sha256(Path(record.result_path))
    if actual_sha256 != result_sha256:
        raise ValueError("结构化结果已变化，请刷新后重新审核")
    result = read_json(Path(record.result_path))
    item_ids = {item.get("item_id") for item in result.get("items", [])}
    review = _load_review(data_root, asset_id, revision, actual_sha256)
    current = {item.get("item_id"): item for item in review.get("items", [])}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("审核条目必须是对象")
        item_id = decision.get("item_id")
        value = decision.get("decision")
        if item_id not in item_ids:
            raise ValueError(f"审核条目不存在：{item_id}")
        if value not in {"pending", "accepted", "rejected"}:
            raise ValueError(f"审核状态非法：{value}")
        current[item_id] = {
            "item_id": item_id,
            "decision": value,
            "note": str(decision.get("note") or "").strip(),
            "reviewed_at": _now(),
        }
    review["items"] = [current[item_id] for item_id in sorted(current)]
    review["updated_at"] = _now()
    write_json(_review_path(data_root, asset_id), review)
    summary = _review_summary(result, review)
    record.review_status = summary["status"]
    if summary["status"] == "pending":
        record.status = "awaiting_review"
    elif summary["status"] == "rejected":
        record.status = "rejected"
    else:
        record.status = "available"
    _save_record(data_root, record)
    return record


def review_asset(data_root: str, asset_id: str, decision: str) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    if record.asset_type != "historical_defect" or not record.result_path:
        raise ValueError("当前资产没有可审核的历史缺陷结果")
    result_path = Path(record.result_path)
    if not result_path.is_file():
        raise ValueError("当前资产没有可审核的结构化结果")
    if decision not in {"approve", "reject"}:
        raise ValueError("decision 必须是 approve 或 reject")
    value = "accepted" if decision == "approve" else "rejected"
    result = read_json(result_path)
    return review_asset_items(
        data_root,
        asset_id,
        record.revision,
        _sha256(result_path),
        [{"item_id": item["item_id"], "decision": value} for item in result.get("items", [])],
    )


def archive_asset(data_root: str, asset_id: str) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    if record.status == "archived":
        raise ValueError("资产已经归档")
    record.archived_from_status = record.status
    record.status = "archived"
    _save_record(data_root, record)
    return record


def restore_asset(data_root: str, asset_id: str) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    if record.status != "archived":
        raise ValueError("只有已归档资产可以恢复")
    restored: AssetStatus | None = record.archived_from_status
    if restored in {None, "archived", "extracting"}:
        if record.last_error:
            restored = "failed"
        elif record.review_status == "pending":
            restored = "awaiting_review"
        elif record.review_status == "rejected":
            restored = "rejected"
        elif record.normalized_text_path:
            restored = "available" if Path(record.normalized_text_path).read_text(
                encoding="utf-8", errors="replace"
            ).strip() else "no_items"
        else:
            restored = "imported"
    record.status = restored
    record.archived_from_status = None
    _save_record(data_root, record)
    return record


def update_asset_metadata(
    data_root: str,
    asset_id: str,
    *,
    title: str,
    repository_ids: list[str] | None = None,
    module_tags: list[str] | None = None,
    language_tags: list[str] | None = None,
) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    if record.status == "archived":
        raise ValueError("已归档资产不能编辑")
    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("资产标题不能为空")

    def clean(values: list[str] | None) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values or [] if value.strip()))

    record.title = normalized_title
    record.repository_ids = clean(repository_ids)
    record.module_tags = clean(module_tags)
    record.language_tags = clean(language_tags)
    _save_record(data_root, record)
    return record
