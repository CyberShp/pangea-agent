from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError

from pangea_agent.models.worker import ReviewResult, ReviewTask, WorkerResult, WorkerTask


class ArtifactRejected(ValueError):
    pass


def _all_evidence(result: WorkerResult):
    yield from result.evidence
    for flow in result.business_flows:
        yield from flow.evidence
    for risk in result.risks:
        yield from risk.evidence


def _evidence_rows(task: WorkerTask, result: WorkerResult) -> dict[str, dict]:
    index_path = Path(task.index_path)
    evidence_refs = list(_all_evidence(result))
    if not index_path.exists():
        for evidence in evidence_refs:
            evidence.status = "pending_confirmation"
            evidence.pending_reason = f"当前运行无法读取证据索引：{index_path}"
        return {}
    chunk_ids = list(dict.fromkeys(item.chunk_id for item in evidence_refs))
    placeholders = ",".join("?" for _ in chunk_ids)
    try:
        with sqlite3.connect(index_path) as connection:
            rows = connection.execute(
                "SELECT chunk_id, source_type, repo_id, path, line_start, line_end, tags "
                f"FROM chunks WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            ).fetchall()
    except sqlite3.Error as exc:
        for evidence in evidence_refs:
            evidence.status = "pending_confirmation"
            evidence.pending_reason = f"当前运行无法查询证据索引：{exc}"
        return {}
    found = {}
    for row in rows:
        prefix = f"{row[2]}:" if row[2] else ""
        path = row[3].replace("\\", "/")
        location = (
            f"{prefix}{path}:{row[4]}-{row[5]}"
            if row[4] is not None and row[5] is not None
            else f"{prefix}{path}"
        )
        found[row[0]] = {
            "source_type": row[1],
            "repo_id": row[2],
            "path": path,
            "line_start": row[4],
            "line_end": row[5],
            "tags": set(json.loads(row[6] or "[]")),
            "location": location,
        }
    scopes = [scope.replace("\\", "/").strip("/") or "." for scope in task.unit.source_scope]
    context_scopes = [scope.replace("\\", "/").strip("/") or "." for scope in task.unit.context_scope]
    for evidence in evidence_refs:
        row = found.get(evidence.chunk_id)
        if row is None:
            evidence.status = "pending_confirmation"
            evidence.pending_reason = "chunk_id 未在当前运行的 SQLite 索引中匹配到"
            continue
        evidence.location = row["location"]
        pending_reason = None
        if row["source_type"] == "code":
            if row["repo_id"] != task.unit.repo_id:
                pending_reason = "证据所属源码仓与当前分析单元不一致"
            elif not any(
                scope == "." or row["path"] == scope or row["path"].startswith(f"{scope}/")
                for scope in scopes
            ):
                pending_reason = "证据路径不在当前分析单元的 source_scope 中"
        if row["source_type"] == "source_context":
            if row["repo_id"] != task.unit.repo_id or row["path"] not in context_scopes:
                pending_reason = "上下文证据不在当前分析单元的 context_scope 中"
        if pending_reason:
            evidence.status = "pending_confirmation"
            evidence.pending_reason = pending_reason
        else:
            evidence.status = "confirmed"
            evidence.pending_reason = None
    return found


def _validate_visual_findings(task: WorkerTask, result: WorkerResult) -> None:
    if not result.visual_findings:
        return
    manifest_path = Path(task.source_manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        for finding in result.visual_findings:
            finding.status = "pending_confirmation"
            finding.pending_reason = f"当前运行无法读取图片来源清单：{manifest_path} ({exc})"
        return
    declared = {item.get("attachment_path") for item in manifest.get("attachments", [])}
    for finding in result.visual_findings:
        if finding.attachment_path in declared:
            finding.status = "confirmed"
            finding.pending_reason = None
        else:
            finding.status = "pending_confirmation"
            finding.pending_reason = "附件路径未在当前运行的来源清单中匹配到"


def validate_nonoverlapping_units(units: list[dict]) -> None:
    owners: dict[tuple[str, str], str] = {}
    for raw_unit in units:
        unit = raw_unit["unit_id"]
        for scope in raw_unit["source_scope"]:
            normalized = scope.replace("\\", "/").strip("/") or "."
            parts = normalized.split("/")
            for owned_scope, owner in owners.items():
                owned_parts = owned_scope[1].split("/")
                if owned_scope[0] == raw_unit.get("repo_id", "") and (
                    parts[: len(owned_parts)] == owned_parts or owned_parts[: len(parts)] == parts
                ):
                    raise ArtifactRejected(f"分析单元源码范围重叠：{owner} 与 {unit} ({scope})")
            owners[(raw_unit["repo_id"], normalized)] = unit


def validate_worker_result(task: WorkerTask, result: WorkerResult) -> None:
    # 这些字段由任务定义，结果文件只承载分析内容；统一恢复为 task 中的确定值。
    result.run_id = task.run_id
    result.unit_id = task.unit.unit_id
    result.attempt = task.attempt
    result.analyzed_scope = list(task.unit.source_scope)
    result.analyzed_context_scope = list(task.unit.context_scope)

    if result.finish_reason != "stop":
        raise ArtifactRejected(f"worker 未正常完成：finish_reason={result.finish_reason}")
    if not result.evidence or not result.business_flows:
        raise ArtifactRejected("worker 正常完成时必须包含真实证据和业务流程")
    _evidence_rows(task, result)
    _validate_visual_findings(task, result)
    if task.task_type == "rework":
        expected_issues = {issue.issue_id for issue in task.review_issues}
        if set(result.addressed_review_issue_ids) != expected_issues:
            raise ArtifactRejected("返工结果未逐项回应 review issue")


def normalize_unique_ids(results: list[WorkerResult]) -> None:
    """Resolve cross-unit identifier collisions without asking an Agent to re-analyze."""

    seen_risks: set[str] = set()
    for result in results:
        renamed: dict[str, str] = {}
        for risk in result.risks:
            original = risk.risk_id
            candidate = original
            suffix = 2
            while candidate in seen_risks:
                candidate = f"{original}-{suffix}"
                suffix += 1
            risk.risk_id = candidate
            seen_risks.add(candidate)
            if candidate != original:
                renamed[original] = candidate
        for case in result.test_cases:
            case.linked_risk_ids = [renamed.get(risk_id, risk_id) for risk_id in case.linked_risk_ids]

    seen_cases: set[str] = set()
    for result in results:
        for case in result.test_cases:
            original = case.test_case_id
            candidate = original
            suffix = 2
            while candidate in seen_cases:
                candidate = f"{original}-{suffix}"
                suffix += 1
            case.test_case_id = candidate
            seen_cases.add(candidate)


def validate_review_result(task: ReviewTask, result: ReviewResult, known_units: set[str]) -> None:
    result.run_id = task.run_id
    result.task_digest = task.task_digest
    if result.finish_reason != "stop":
        raise ArtifactRejected(f"review 输出不完整：finish_reason={result.finish_reason}")
    unknown = {issue.unit_id for issue in result.issues} - known_units
    if unknown:
        raise ArtifactRejected(f"review issue 引用了未知单元：{sorted(unknown)}")
    if len({issue.issue_id for issue in result.issues}) != len(result.issues):
        raise ArtifactRejected("review issue_id 重复")


def validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)
