from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError

from pangea_agent.models.worker import ReviewResult, ReviewTask, WorkerResult, WorkerTask


class ArtifactRejected(ValueError):
    pass


def _evidence_rows(task: WorkerTask, result: WorkerResult) -> dict[str, dict]:
    index_path = Path(task.index_path)
    if not index_path.exists():
        raise ArtifactRejected(f"证据索引不存在：{index_path}")
    chunk_ids = list(dict.fromkeys(item.chunk_id for item in result.evidence))
    placeholders = ",".join("?" for _ in chunk_ids)
    with sqlite3.connect(index_path) as connection:
        rows = connection.execute(
            "SELECT chunk_id, source_type, repo_id, path, line_start, line_end, tags "
            f"FROM chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
    found = {
        row[0]: {
            "source_type": row[1],
            "repo_id": row[2],
            "path": row[3].replace("\\", "/"),
            "line_start": row[4],
            "line_end": row[5],
            "tags": set(json.loads(row[6] or "[]")),
        }
        for row in rows
    }
    missing = sorted(set(chunk_ids) - set(found))
    if missing:
        raise ArtifactRejected(f"证据 chunk_id 不存在：{missing}")

    scopes = [scope.replace("\\", "/").strip("/") or "." for scope in task.unit.source_scope]
    context_scopes = [scope.replace("\\", "/").strip("/") or "." for scope in task.unit.context_scope]
    for evidence in result.evidence:
        row = found[evidence.chunk_id]
        prefix = f"{row['repo_id']}:" if row["repo_id"] else ""
        if row["line_start"] is not None and row["line_end"] is not None:
            location = f"{prefix}{row['path']}:{row['line_start']}-{row['line_end']}"
        else:
            location = f"{prefix}{row['path']}"
        if evidence.location != location:
            raise ArtifactRejected(
                f"证据 {evidence.chunk_id} location 不匹配：期望 {location}，实际 {evidence.location}"
            )
        if row["source_type"] == "code":
            if row["repo_id"] != task.unit.repo_id:
                raise ArtifactRejected(f"证据 {evidence.chunk_id} 不属于当前仓库 {task.unit.repo_id}")
            if not any(
                scope == "." or row["path"] == scope or row["path"].startswith(f"{scope}/")
                for scope in scopes
            ):
                raise ArtifactRejected(f"证据 {evidence.chunk_id} 超出当前分析单元范围")
        if row["source_type"] == "source_context":
            if row["repo_id"] != task.unit.repo_id:
                raise ArtifactRejected(f"上下文证据 {evidence.chunk_id} 不属于当前仓库 {task.unit.repo_id}")
            if row["path"] not in context_scopes:
                raise ArtifactRejected(f"上下文证据 {evidence.chunk_id} 超出当前分析单元范围")
    return found


def _validate_visual_findings(task: WorkerTask, result: WorkerResult) -> None:
    manifest_path = Path(task.source_manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactRejected(f"无法读取图片来源清单：{manifest_path}") from exc
    declared = {item.get("attachment_path") for item in manifest.get("attachments", [])}
    claimed = [item.attachment_path for item in result.visual_findings]
    unknown = sorted(set(claimed) - declared)
    if unknown:
        raise ArtifactRejected(f"图片结论引用了清单外附件：{unknown}")
    duplicates = sorted(item for item, count in Counter(claimed).items() if count > 1)
    if duplicates:
        raise ArtifactRejected(f"同一 worker 重复声明图片结论：{duplicates}")


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
    if result.run_id != task.run_id or result.unit_id != task.unit.unit_id:
        raise ArtifactRejected("worker 结果与 run_id/unit_id 不匹配")
    if result.attempt != task.attempt or result.input_digest != task.input_digest:
        raise ArtifactRejected("worker 结果与任务版本不匹配")
    if result.finish_reason != "stop":
        raise ArtifactRejected(f"worker 输出不完整：finish_reason={result.finish_reason}")
    expected_scope = {scope.replace("\\", "/").strip("/") or "." for scope in task.unit.source_scope}
    actual_scope = {scope.replace("\\", "/").strip("/") or "." for scope in result.analyzed_scope}
    if actual_scope != expected_scope:
        raise ArtifactRejected(f"worker 分析范围不完整：期望 {sorted(expected_scope)}，实际 {sorted(actual_scope)}")
    expected_context = {scope.replace("\\", "/").strip("/") or "." for scope in task.unit.context_scope}
    actual_context = {scope.replace("\\", "/").strip("/") or "." for scope in result.analyzed_context_scope}
    if actual_context != expected_context:
        raise ArtifactRejected(
            f"worker 上游语义范围不完整：期望 {sorted(expected_context)}，实际 {sorted(actual_context)}"
        )
    if not result.evidence:
        raise ArtifactRejected("worker 结果缺少单元证据")
    rows = _evidence_rows(task, result)
    _validate_visual_findings(task, result)
    evidence_keys = {(evidence.chunk_id, evidence.location) for evidence in result.evidence}
    for flow in result.business_flows:
        if any((evidence.chunk_id, evidence.location) not in evidence_keys for evidence in flow.evidence):
            raise ArtifactRejected(f"业务流程 {flow.title} 的证据未绑定到当前单元")
        if not any(rows[evidence.chunk_id]["source_type"] == "code" for evidence in flow.evidence):
            raise ArtifactRejected(f"业务流程 {flow.title} 缺少当前源码证据")
        if any("testcase_reference" in rows[evidence.chunk_id]["tags"] for evidence in flow.evidence):
            raise ArtifactRejected(f"业务流程 {flow.title} 不能把历史测试用例作为实现证据")
    for risk in result.risks:
        if risk.upstream_semantics.conclusion != "risk_remains":
            raise ArtifactRejected(
                f"风险 {risk.risk_id} 的上游语义结论为 {risk.upstream_semantics.conclusion}，不能继续列为风险"
            )
        if not risk.evidence or any(
            (evidence.chunk_id, evidence.location) not in evidence_keys for evidence in risk.evidence
        ):
            raise ArtifactRejected(f"风险 {risk.risk_id} 的证据未绑定到当前单元")
        if not any(rows[evidence.chunk_id]["source_type"] == "code" for evidence in risk.evidence):
            raise ArtifactRejected(f"风险 {risk.risk_id} 缺少当前源码证据")
        if any("testcase_reference" in rows[evidence.chunk_id]["tags"] for evidence in risk.evidence):
            raise ArtifactRejected(f"风险 {risk.risk_id} 不能把历史测试用例作为风险存在证据")
    if task.task_type == "rework":
        expected_issues = {issue.issue_id for issue in task.review_issues}
        if set(result.addressed_review_issue_ids) != expected_issues:
            raise ArtifactRejected("返工结果未逐项回应 review issue")


def validate_task_result_path(task_path: Path, result_path: Path, run_dir: Path) -> None:
    resolved_run = run_dir.resolve()
    resolved_result = result_path.resolve()
    if resolved_result != Path(task_path).resolve():
        raise ArtifactRejected("任务声明的 result_path 与约定路径不一致")
    if resolved_run not in resolved_result.parents:
        raise ArtifactRejected("result_path 超出当前 run 目录")


def validate_unique_ids(results: list[WorkerResult]) -> None:
    for label, identifiers in (
        ("risk_id", [risk.risk_id for result in results for risk in result.risks]),
        ("test_case_id", [case.test_case_id for result in results for case in result.test_cases]),
    ):
        duplicates = sorted(item for item, count in Counter(identifiers).items() if count > 1)
        if duplicates:
            raise ArtifactRejected(f"跨单元 {label} 重复：{duplicates}")


def validate_review_result(task: ReviewTask, result: ReviewResult, known_units: set[str]) -> None:
    if result.run_id != task.run_id or result.task_digest != task.task_digest:
        raise ArtifactRejected("review 结果与任务版本不匹配")
    if result.finish_reason != "stop":
        raise ArtifactRejected(f"review 输出不完整：finish_reason={result.finish_reason}")
    unknown = {issue.unit_id for issue in result.issues} - known_units
    if unknown:
        raise ArtifactRejected(f"review issue 引用了未知单元：{sorted(unknown)}")
    if len({issue.issue_id for issue in result.issues}) != len(result.issues):
        raise ArtifactRejected("review issue_id 重复")


def validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(error["msg"] for error in exc.errors())
    return str(exc)
