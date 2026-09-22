from __future__ import annotations

import shutil
import hashlib
import json
from uuid import uuid4
from datetime import datetime
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.documents.coverage import parse_coverage_xlsx, parse_coverage_combined
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
        if source_path.suffix.lower() not in {".xlsx", ".json"}:
            raise ValueError("Coverage 支持 XLSX 或查询 combined JSON")
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
        "items": [{**item.model_dump(mode="json"), "input_revision": asset_input_revision(data_root, item)} for item in page],
        "next_cursor": next_cursor if next_cursor < len(records) else None,
        "total": len(records),
    }


def asset_detail(data_root: str, asset_id: str) -> dict:
    record = load_asset(data_root, asset_id)
    result = None
    result_path = _asset_result_path(data_root, record)
    if result_path and result_path.is_file():
        result = read_json(result_path)
    text_path = result_path.parent / "extracted.txt" if result_path else None
    original_text = text_path.read_text(encoding="utf-8") if text_path and text_path.is_file() else None
    return {"asset": {**record.model_dump(mode="json"), "input_revision": asset_input_revision(data_root, record)}, "result": result, "normalized_preview": original_text}


def _asset_result_path(data_root: str, record: AssetRecord) -> Path | None:
    """Resolve an asset result independently of the worktree that created it."""
    if not record.result_path:
        return None
    recorded = Path(record.result_path)
    root = _asset_dir(data_root, record.asset_id).resolve()
    try:
        relative = recorded.resolve().relative_to(root)
    except ValueError:
        parts = recorded.parts
        relative = Path(*parts[parts.index("extraction-attempts"):]) if "extraction-attempts" in parts else Path(recorded.name)
    resolved = (root / relative).resolve()
    resolved.relative_to(root)
    return resolved


def _asset_snapshot(data_root: str, record: AssetRecord, *, parse_result: bool = True) -> tuple[str, dict | None]:
    path = _asset_result_path(data_root, record)
    content = path.read_bytes() if path and path.is_file() else b""
    metadata = record.model_dump(mode="json")
    if load_asset(data_root, record.asset_id).model_dump(mode="json") != metadata:
        raise ValueError(f"资产输入已变更，请重新选择：{record.asset_id}")
    revision = hashlib.sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode() + b"\0" + content).hexdigest()
    return revision, json.loads(content) if content and parse_result else None


def asset_input_revision(data_root: str, record: AssetRecord) -> str:
    return _asset_snapshot(data_root, record, parse_result=False)[0]


def analysis_asset_inputs(data_root: str, asset_ids: list[str] | None = None, *, expected_revisions: dict[str, str] | None = None) -> dict:
    # ``None`` is the catalog-management view (all available assets).  A Run
    # contract always supplies a list, where an empty list means no assets.
    selected = None if asset_ids is None else set(asset_ids)
    # Only read result bytes for selected assets. Catalog pagination carries
    # fingerprints for the UI and must not make a Run hash unrelated assets.
    records = [AssetRecord.model_validate(read_json(path))
               for path in _assets_root(data_root).glob("*/asset.json")
               if selected is None or path.parent.name in selected]
    records.sort(key=lambda item: (item.created_at, item.asset_id), reverse=True)

    candidates: list[dict] = []
    items: dict[str, dict] = {}
    coverage_records: list[dict] = []
    coverage_diagnostics: list[dict] = []
    snapshots: list[dict] = []
    consumed: set[str] = set()
    for record in records:
        if selected is not None and record.asset_id not in selected:
            continue
        if record.status != "available" or not record.result_path:
            continue
        result_path = _asset_result_path(data_root, record)
        if result_path is None or not result_path.is_file():
            continue
        revision, result = _asset_snapshot(data_root, record)
        if expected_revisions is not None and record.asset_id in expected_revisions and revision != expected_revisions[record.asset_id]:
            raise ValueError(f"资产输入已变更，请重新选择：{record.asset_id}")
        consumed.add(record.asset_id)
        snapshots.append({"asset_id": record.asset_id, "input_revision": revision,
                          "metadata": record.model_dump(mode="json"), "result": result})
        if record.asset_type == "coverage":
            coverage_diagnostics.append({"asset_id": record.asset_id,
                                         "input_revision": revision,
                                         "record_count": len(result.get("records", [])),
                                         **(result.get("acquisition") or {}),
                                         "warnings": result.get("warnings", [])})
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
                or payload.get("expected_results")
                or payload.get("key_facts")
                or [],
                "applicable_modules": payload.get("modules")
                or payload.get("applicable_modules")
                or [],
                "source_references": payload.get("source_references", []),
            })
    if expected_revisions is not None and selected is not None and selected - consumed:
        raise ValueError(f"选定资产不可用或未完成解析：{', '.join(sorted(selected - consumed))}")
    return {
        "candidates": candidates,
        "items": items,
        "coverage_records": coverage_records,
        "coverage_diagnostics": coverage_diagnostics,
        "snapshots": snapshots,
    }


def prepare_asset_extraction(data_root: str, asset_id: str, *, restart: bool = False) -> dict:
    record = load_asset(data_root, asset_id)
    if record.status == "archived":
        raise ValueError("已归档资产不能开始提取")
    if record.status == "extracting" and not restart:
        action = load_asset_action(data_root, asset_id)
        return {"asset": record.model_dump(mode="json"), "action": action.model_dump(mode="json")}
    source = Path(record.source_path)
    asset_dir = _asset_dir(data_root, asset_id)

    if record.asset_type == "coverage":
        if source.suffix.lower() == ".json":
            result = parse_coverage_combined(source)
        else:
            records, warnings = parse_coverage_xlsx(source)
            result = {"records": records, "warnings": warnings}
        records, warnings = result["records"], result["warnings"]
        result_path = asset_dir / "coverage.json"
        write_json(result_path, result)
        record.result_path = str(result_path)
        record.structured_item_count = len(records)
        record.warnings = warnings
        record.status = "available" if records or result.get("acquisition", {}).get("status") in {"success", "partial"} else "no_items"
        _save_record(data_root, record)
        return {"asset": record.model_dump(mode="json"), "action": None}

    attempt = uuid4().hex
    attempt_dir = asset_dir / "extraction-attempts" / attempt
    attempt_dir.mkdir(parents=True)
    # Preserve the previous action and inputs/result paths before replacing the active pointer.
    previous_action = asset_action_path(data_root, asset_id)
    if previous_action.is_file():
        write_json(attempt_dir / "previous-action.json", read_json(previous_action))
    extraction = extract_document(source, attempt_dir / "attachments")
    text_path = attempt_dir / "extracted.txt"
    text_path.write_text(extraction.text, encoding="utf-8")
    task_path = attempt_dir / "extraction-task.json"
    result_path = attempt_dir / "extraction-result.json"
    # Freeze only the selected type's instructions and schema for this attempt.
    rubric = project_path("src", "pangea_agent", "rubrics", "builtin", "asset_extraction.md").read_text(encoding="utf-8")
    instructions = rubric.split("## ", 1)[0] + "## " + record.asset_type + "\n" + rubric.split("## " + record.asset_type + "\n", 1)[1].split("\n## ", 1)[0]
    schema = read_json(project_path("schemas", "asset_extraction_result.schema.json"))
    item_schema = schema["properties"]["items"]["items"]
    selected_ref = item_schema["discriminator"]["mapping"][record.asset_type]
    schema["properties"]["items"]["items"] = {"$ref": selected_ref}
    definitions = schema["$defs"]
    keep = {}
    def references(value):
        if isinstance(value, dict):
            if "$ref" in value:
                yield value["$ref"].rsplit("/", 1)[-1]
            for child in value.values():
                yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    pending = [selected_ref.rsplit("/", 1)[-1]]
    while pending:
        name = pending.pop()
        if name in keep:
            continue
        keep[name] = definitions[name]
        pending.extend(references(keep[name]))
    schema["$defs"] = keep
    schema_path = attempt_dir / "result-schema.json"
    write_json(schema_path, schema)
    task = AssetExtractionTask(
        asset_id=asset_id,
        asset_type=record.asset_type,
        title=record.title,
        source_path=record.source_path,
        extracted_text_path=str(text_path),
        extraction_instructions=instructions,
        attachments=[item.__dict__ for item in extraction.attachments],
        result_schema_path=str(schema_path),
        result_path=str(result_path),
    )
    write_json(task_path, task.model_dump(mode="json"))
    record.status = "extracting"
    record.extraction_task_path = str(task_path)
    record.result_path = str(result_path)
    record.warnings = extraction.warnings
    _save_record(data_root, record)
    action = ActionState(
        action_id=f"asset:{asset_id}:extract:{attempt}",
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
        raise ValueError(f"提取结果类型与资产类型不一致：expected={record.asset_type}；"
                         f"条目={[(item.item_id, item.item_type) for item in result.items if item.item_type != record.asset_type]}；"
                         "请原 worker 修正同一 result_path，不改 asset_id、不机械转换类型")
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
