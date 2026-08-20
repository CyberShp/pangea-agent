from __future__ import annotations

import json
import re
from pathlib import Path
import sqlite3

from pydantic import ValidationError

from pangea_agent.models.worker import (
    IndependentReviewResult,
    ReviewResult,
    ReviewTask,
    WorkerResult,
    WorkerTask,
)


class ArtifactRejected(ValueError):
    pass


def _all_evidence(result: WorkerResult):
    yield from result.evidence
    for flow in result.business_flows:
        yield from flow.evidence
    for risk in result.risks:
        yield from risk.evidence


def _row_record(row) -> dict:
    prefix = f"{row[2]}:" if row[2] else ""
    path = row[3].replace("\\", "/")
    location = (
        f"{prefix}{path}:{row[4]}-{row[5]}"
        if row[4] is not None and row[5] is not None
        else f"{prefix}{path}"
    )
    return {
        "chunk_id": row[0],
        "source_type": row[1],
        "repo_id": row[2],
        "path": path,
        "line_start": row[4],
        "line_end": row[5],
        "tags": set(json.loads(row[6] or "[]")),
        "location": location,
    }


def _parse_evidence_reference(value: str, task: WorkerTask) -> dict | None:
    match = re.match(r"^(?P<prefix>.+):(?P<start>\d+)(?:-(?P<end>\d+))?$", value.strip())
    if match is None:
        return None
    prefix = match.group("prefix").replace("\\", "/")
    repo_id = None
    repo_prefix = f"{task.unit.repo_id}:"
    if prefix.startswith(repo_prefix):
        repo_id = task.unit.repo_id
        prefix = prefix[len(repo_prefix):]
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if end < start:
        start, end = end, start
    return {"repo_id": repo_id, "path": prefix.strip("/"), "start": start, "end": end}


def _fallback_evidence_row(task: WorkerTask, chunk_id: str, rows: list[dict]) -> tuple[dict | None, str]:
    reference = _parse_evidence_reference(chunk_id, task)
    if reference is None:
        return None, "chunk_id 格式无法解析，且未在当前运行的 SQLite 索引中精确匹配"

    candidates: list[tuple[tuple[int, int, int, int], dict]] = []
    for row in rows:
        if row["line_start"] is None or row["line_end"] is None:
            continue
        if reference["repo_id"] is not None and row["repo_id"] != reference["repo_id"]:
            continue
        if reference["repo_id"] is None and row["source_type"] in {"code", "source_context"} and row["repo_id"] != task.unit.repo_id:
            continue

        row_path = row["path"].strip("/")
        ref_path = reference["path"]
        exact_path = row_path == ref_path
        suffix_path = row_path.endswith(f"/{ref_path}") or ref_path.endswith(f"/{row_path}")
        if not exact_path and not suffix_path:
            continue

        overlap = min(reference["end"], row["line_end"]) - max(reference["start"], row["line_start"]) + 1
        if overlap <= 0:
            continue
        exact_range = reference["start"] == row["line_start"] and reference["end"] == row["line_end"]
        contains_range = row["line_start"] <= reference["start"] and row["line_end"] >= reference["end"]
        score = (int(exact_path), int(exact_range), int(contains_range), overlap)
        candidates.append((score, row))

    if not candidates:
        return None, "chunk_id 未在当前运行的 SQLite 索引中匹配到对应路径和行号"
    best_score = max(score for score, _ in candidates)
    best = [row for score, row in candidates if score == best_score]
    if len(best) != 1:
        return None, "chunk_id 可匹配多个源码片段，无法唯一确认"
    return best[0], ""


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
            all_rows = []
            if len(rows) < len(chunk_ids):
                all_rows = connection.execute(
                    "SELECT chunk_id, source_type, repo_id, path, line_start, line_end, tags FROM chunks"
                ).fetchall()
    except sqlite3.Error as exc:
        for evidence in evidence_refs:
            evidence.status = "pending_confirmation"
            evidence.pending_reason = f"当前运行无法查询证据索引：{exc}"
        return {}
    found = {row[0]: _row_record(row) for row in rows}
    fallback_rows = [_row_record(row) for row in all_rows]
    scopes = [scope.replace("\\", "/").strip("/") or "." for scope in task.unit.source_scope]
    context_scopes = [scope.replace("\\", "/").strip("/") or "." for scope in task.unit.context_scope]
    for evidence in evidence_refs:
        row = found.get(evidence.chunk_id)
        if row is None:
            row, reason = _fallback_evidence_row(task, evidence.chunk_id, fallback_rows)
            if row is None:
                evidence.status = "pending_confirmation"
                evidence.pending_reason = reason
                continue
            evidence.chunk_id = row["chunk_id"]
            found[evidence.chunk_id] = row
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


def _material_id(path: str) -> str:
    return f"MAT:{path.replace('\\', '/')}"


def _validate_test_basis_closure(task: WorkerTask, result: WorkerResult) -> None:
    case_by_id = {case.test_case_id: case for case in result.test_cases}
    if len(case_by_id) != len(result.test_cases):
        raise ArtifactRejected("当前单元测试用例 ID 重复")

    # Risk 是基础驱动：凡是已经具备可执行测试翻译的风险，都必须至少有一条风险用例。
    for risk in result.risks:
        if risk.translation_status == "Developer-confirm":
            continue
        if not any(risk.risk_id in case.linked_risk_ids for case in result.test_cases):
            raise ArtifactRejected(f"可执行风险 {risk.risk_id} 尚未闭环到测试用例")

    # Coverage 只有在与当前分析单元唯一匹配并形成 gap 时才升级为强制输入。
    known_coverage_ids = {
        gap.coverage_id
        for coverage in task.coverage_context
        for gap in coverage.gaps
    }
    linked_coverage_ids = {
        coverage_id
        for case in result.test_cases
        for coverage_id in case.linked_coverage_ids
    }
    unknown_coverage_ids = linked_coverage_ids - known_coverage_ids
    if unknown_coverage_ids:
        raise ArtifactRejected(f"测试用例引用了当前单元不存在的 Coverage 缺口：{sorted(unknown_coverage_ids)}")

    decisions = result.analysis_checkpoint.coverage_decisions
    decision_by_id = {decision.coverage_id: decision for decision in decisions}
    if len(decision_by_id) != len(decisions):
        raise ArtifactRejected("Coverage 缺口存在重复闭环记录")
    unknown_decisions = set(decision_by_id) - known_coverage_ids
    if unknown_decisions:
        raise ArtifactRejected(f"Coverage 闭环引用了当前单元不存在的缺口：{sorted(unknown_decisions)}")
    missing_decisions = known_coverage_ids - set(decision_by_id)
    if missing_decisions:
        raise ArtifactRejected(f"Coverage 缺口尚未闭环：{sorted(missing_decisions)}")
    for coverage_id, decision in decision_by_id.items():
        if decision.disposition == "unreachable_from_supported_entry":
            continue
        for case_id in decision.linked_test_case_ids:
            case = case_by_id.get(case_id)
            if case is None:
                raise ArtifactRejected(f"Coverage 缺口 {coverage_id} 引用了不存在的测试用例 {case_id}")
            if coverage_id not in case.linked_coverage_ids:
                raise ArtifactRejected(
                    f"Coverage 缺口 {coverage_id} 的闭环用例 {case_id} 未反向关联该 coverage_id"
                )

    # Requirement / design 是可选输入；一旦 worker 判定资料为 current，就必须至少有一条资料驱动用例。
    manifest_path = Path(task.source_manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactRejected(f"无法校验当前资料闭环：{manifest_path} ({exc})") from exc
    material_catalog = {
        item.get("path"): item
        for item in manifest.get("material_catalog", [])
        if isinstance(item, dict) and item.get("type") == "material"
    }
    known_material_ids = {_material_id(path) for path in material_catalog}
    linked_material_ids = {
        material_id
        for case in result.test_cases
        for material_id in case.linked_material_ids
    }
    unknown_material_ids = linked_material_ids - known_material_ids
    if unknown_material_ids:
        raise ArtifactRejected(f"测试用例引用了当前 Run 不存在的资料：{sorted(unknown_material_ids)}")
    for decision in result.analysis_checkpoint.material_decisions:
        if decision.decision != "current":
            continue
        catalog_item = material_catalog.get(decision.path)
        if catalog_item is None:
            continue
        if not str(catalog_item.get("parse_status", "")).startswith("parsed"):
            continue
        material_id = _material_id(decision.path)
        if not any(material_id in case.linked_material_ids for case in result.test_cases):
            raise ArtifactRejected(
                f"当前资料 {decision.path} 已判定与分析对象相关，但尚未闭环到测试用例（{material_id}）"
            )


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
    checkpoint = result.analysis_checkpoint
    if not checkpoint.risk_set_frozen:
        raise ArtifactRejected("worker 尚未冻结风险集合，不能开始生成或提交测试用例")
    if not checkpoint.counterexamples_checked:
        raise ArtifactRejected("worker 尚未记录提交前的反例检查")
    risk_by_id = {risk.risk_id: risk for risk in result.risks}
    check_by_id = {check.check_id: check for check in task.semantic_check_items}
    failure_by_id: dict[str, list] = {}
    for failure_path in checkpoint.failure_paths:
        failure_by_id.setdefault(failure_path.path_id, []).append(failure_path)
        unknown_risks = set(failure_path.linked_risk_ids) - set(risk_by_id)
        if unknown_risks:
            raise ArtifactRejected(
                f"failure path {failure_path.path_id} 引用了不存在的风险：{sorted(unknown_risks)}"
            )
    for check_id, check in check_by_id.items():
        matches = failure_by_id.get(check_id, [])
        if len(matches) != 1:
            raise ArtifactRejected(f"semantic check {check_id} 必须有且只有一条同 ID failure path")
        failure_path = matches[0]
        if failure_path.disposition == "risk" and not failure_path.linked_risk_ids:
            raise ArtifactRejected(f"semantic check {check_id} 判定为 risk 时必须关联对应风险")
        for risk_id in failure_path.linked_risk_ids:
            if check.subject_path not in risk_by_id[risk_id].affected_paths:
                raise ArtifactRejected(
                    f"风险 {risk_id} 未声明 semantic check {check_id} 的受影响路径 {check.subject_path}"
                )
    semantic_subjects = {check.subject_path for check in task.semantic_check_items}
    supported_pairs = {
        (risk_id, check_by_id[path.path_id].subject_path)
        for path in checkpoint.failure_paths
        if path.path_id in check_by_id and path.disposition == "risk"
        for risk_id in path.linked_risk_ids
    }
    for risk in result.risks:
        for affected_path in set(risk.affected_paths) & semantic_subjects:
            if (risk.risk_id, affected_path) not in supported_pairs:
                raise ArtifactRejected(
                    f"风险 {risk.risk_id} 声明受影响路径 {affected_path}，但对应 semantic check 未判定并关联该风险"
                )
    _validate_test_basis_closure(task, result)
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


def validate_independent_review_result(
    task: ReviewTask,
    result: IndependentReviewResult,
    expected_checks: set[tuple[str, str]],
) -> None:
    result.run_id = task.run_id
    if task.stage != "independent_review":
        raise ArtifactRejected("独立复核结果只能绑定 independent_review task")
    if result.finish_reason != "stop":
        raise ArtifactRejected(f"独立复核输出不完整：finish_reason={result.finish_reason}")
    known_units = {item.unit_id for item in task.analysis_tasks}
    if set(result.reviewed_units) != known_units:
        raise ArtifactRejected("独立复核未记录全部分析单元")
    actual_checks = {(item.unit_id, item.check_id) for item in result.findings}
    if len(actual_checks) != len(result.findings):
        raise ArtifactRejected("独立复核 check_id 重复")
    unknown_units = {item.unit_id for item in result.findings} - known_units
    if unknown_units:
        raise ArtifactRejected(f"独立复核引用了未知单元：{sorted(unknown_units)}")
    missing_checks = expected_checks - actual_checks
    if missing_checks:
        formatted = [f"{unit_id}:{check_id}" for unit_id, check_id in sorted(missing_checks)]
        raise ArtifactRejected(f"独立复核遗漏 semantic check：{formatted}")


def validate_review_result(
    task: ReviewTask,
    result: ReviewResult,
    known_units: set[str],
    independent_result: IndependentReviewResult | None = None,
) -> None:
    result.run_id = task.run_id
    if result.finish_reason != "stop":
        raise ArtifactRejected(f"review 输出不完整：finish_reason={result.finish_reason}")
    unknown = {issue.unit_id for issue in result.issues} - known_units
    if unknown:
        raise ArtifactRejected(f"review issue 引用了未知单元：{sorted(unknown)}")
    if len({issue.issue_id for issue in result.issues}) != len(result.issues):
        raise ArtifactRejected("review issue_id 重复")
    if set(result.reviewed_units) != known_units:
        raise ArtifactRejected("review 未记录全部分析单元的独立复核")
    unknown_findings = {item.unit_id for item in result.independent_findings} - known_units
    if unknown_findings:
        raise ArtifactRejected(f"独立发现引用了未知单元：{sorted(unknown_findings)}")
    if task.stage == "comparison_review":
        if independent_result is None:
            raise ArtifactRejected("对照复核缺少已完成的独立复核结果")
        if result.reviewer_id != independent_result.reviewer_id:
            raise ArtifactRejected("对照复核必须由原独立 reviewer 完成")
        original = {
            (item.unit_id, item.check_id): item
            for item in independent_result.findings
        }
        compared = {
            (item.unit_id, item.check_id): item
            for item in result.independent_findings
            if item.check_id is not None
        }
        if len(compared) != len(result.independent_findings) or set(compared) != set(original):
            raise ArtifactRejected("对照复核必须逐项保留独立复核 findings")
        for key, finding in original.items():
            comparison = compared[key]
            if comparison.finding != finding.finding or comparison.evidence != finding.evidence:
                raise ArtifactRejected("对照复核不能改写独立复核结论或证据")


def validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)
