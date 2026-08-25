from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.documents.coverage import parse_coverage_xlsx
from pangea_agent.documents.extract import extract_document
from pangea_agent.graph.workflow_store import project_path
from pangea_agent.models.asset import (
    AssetExtractionResult,
    AssetExtractionTask,
    AssetRecord,
    AssetType,
)
from pangea_agent.models.analysis import ActionState


DOCUMENT_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".xlsx"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _assets_root(data_root: str) -> Path:
    return Path(data_root) / "assets"


def _asset_dir(data_root: str, asset_id: str) -> Path:
    return _assets_root(data_root) / asset_id


def _record_path(data_root: str, asset_id: str) -> Path:
    return _asset_dir(data_root, asset_id) / "asset.json"


def asset_action_path(data_root: str, asset_id: str) -> Path:
    return _asset_dir(data_root, asset_id) / "action.json"


def load_asset_action(data_root: str, asset_id: str) -> ActionState:
    path = asset_action_path(data_root, asset_id)
    if not path.is_file():
        raise ValueError(f"资产提取 Action 不存在：{asset_id}")
    return ActionState.model_validate(read_json(path))


def save_asset_action(data_root: str, asset_id: str, action: ActionState) -> None:
    write_json(asset_action_path(data_root, asset_id), action.model_dump(mode="json"))


def _next_asset_id(data_root: str) -> str:
    prefix = f"asset-{datetime.now().astimezone():%y%m%d}"
    sequence = 1
    while _asset_dir(data_root, f"{prefix}-{sequence:03d}").exists():
        sequence += 1
    return f"{prefix}-{sequence:03d}"


def _save_record(data_root: str, record: AssetRecord) -> None:
    record.updated_at = _now()
    write_json(_record_path(data_root, record.asset_id), record.model_dump(mode="json"))


def load_asset(data_root: str, asset_id: str) -> AssetRecord:
    path = _record_path(data_root, asset_id)
    if not path.is_file():
        raise ValueError(f"资产不存在：{asset_id}")
    return AssetRecord.model_validate(read_json(path))


def import_asset(
    data_root: str,
    source: str,
    asset_type: AssetType,
    title: str | None = None,
) -> AssetRecord:
    source_path = Path(source)
    if not source_path.is_file():
        raise ValueError(f"资产来源不是文件：{source_path}")
    if asset_type == "coverage":
        if source_path.suffix.lower() != ".xlsx":
            raise ValueError("Coverage 当前只支持 XLSX")
        destination_root = Path(data_root) / "coverage"
    else:
        if source_path.suffix.lower() not in DOCUMENT_SUFFIXES:
            raise ValueError(f"不支持的资料类型：{source_path.suffix or '<none>'}")
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
        created_at=now,
        updated_at=now,
    )
    _save_record(data_root, record)
    return record


def list_assets(
    data_root: str,
    *,
    cursor: int = 0,
    limit: int = 50,
    asset_type: str | None = None,
    status: str | None = None,
    query: str | None = None,
) -> dict:
    if cursor < 0:
        raise ValueError("cursor 不能小于 0")
    if limit < 1 or limit > 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    records = []
    normalized_query = (query or "").strip().casefold()
    root = _assets_root(data_root)
    if root.exists():
        for path in root.glob("*/asset.json"):
            record = AssetRecord.model_validate(read_json(path))
            if asset_type and record.asset_type != asset_type:
                continue
            if status and record.status != status:
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
    }


def asset_detail(data_root: str, asset_id: str) -> dict:
    record = load_asset(data_root, asset_id)
    result = None
    if record.result_path and Path(record.result_path).is_file():
        result = read_json(Path(record.result_path))
    return {"asset": record.model_dump(mode="json"), "result": result}


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

    if record.asset_type == "coverage":
        records, warnings = parse_coverage_xlsx(source)
        result_path = asset_dir / "coverage.json"
        write_json(result_path, {"records": records, "warnings": warnings})
        record.result_path = str(result_path)
        record.structured_item_count = len(records)
        record.warnings = warnings
        record.status = "available" if records else "no_items"
        _save_record(data_root, record)
        return {"asset": record.model_dump(mode="json"), "action": None}

    extraction = extract_document(source, asset_dir / "attachments")
    text_path = asset_dir / "extracted.txt"
    text_path.write_text(extraction.text, encoding="utf-8")
    task_path = asset_dir / "extraction-task.json"
    result_path = asset_dir / "extraction-result.json"
    task = AssetExtractionTask(
        asset_id=asset_id,
        asset_type=record.asset_type,
        title=record.title,
        source_path=record.source_path,
        extracted_text_path=str(text_path),
        attachments=[item.__dict__ for item in extraction.attachments],
        result_schema_path=str(
            project_path("schemas", "asset_extraction_result.schema.json")
        ),
        result_path=str(result_path),
    )
    write_json(task_path, task.model_dump(mode="json"))
    record.status = "extracting"
    record.extraction_task_path = str(task_path)
    record.result_path = str(result_path)
    record.warnings = extraction.warnings
    _save_record(data_root, record)
    action = ActionState(
        action_id=f"asset:{asset_id}:extract",
        action="dispatch_agent",
        role="asset_extraction",
        stage="structured_extraction",
        task_path=str(task_path),
    )
    save_asset_action(data_root, asset_id, action)
    return {
        "asset": record.model_dump(mode="json"),
        "action": action.model_dump(mode="json"),
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


def review_asset(data_root: str, asset_id: str, decision: str) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    if record.asset_type != "historical_defect" or record.review_status != "pending":
        raise ValueError("当前资产没有等待审核的历史缺陷结果")
    if decision == "approve":
        record.review_status = "approved"
        record.status = "available" if record.structured_item_count else "no_items"
    elif decision == "reject":
        record.review_status = "rejected"
        record.status = "rejected"
    else:
        raise ValueError("decision 必须是 approve 或 reject")
    _save_record(data_root, record)
    return record


def archive_asset(data_root: str, asset_id: str) -> AssetRecord:
    record = load_asset(data_root, asset_id)
    record.status = "archived"
    _save_record(data_root, record)
    return record
