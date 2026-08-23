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


def _buggy_oracle_issue_ids(issues) -> list[str]:
    return [
        issue.issue_id
        for issue in issues
        if re.search(
            r"expected[_ ]?result|测试预期|预期结果",
            issue.required_change,
            re.IGNORECASE,
        )
        and re.search(
            r"(?:当前实现|源码当前|当前源码|实际行为).{0,80}"
            r"(?:违反|错误|缺陷|bug|fail)",
            issue.required_change,
            re.IGNORECASE,
        )
    ]


def _conflated_oracle_observation_issue_ids(issues) -> list[str]:
    return [
        issue.issue_id
        for issue in issues
        if (
            combined := f"{issue.reason}\n{issue.required_change}"
        )
        and re.search(r"expected[_ ]?result|测试预期|预期结果", combined, re.IGNORECASE)
        and re.search(r"failure[_ ]?observation|失败观测", combined, re.IGNORECASE)
        and re.search(r"矛盾|互相否定|不矛盾|自洽|修正其一|二者", combined, re.IGNORECASE)
    ]


def _reviewer_self_correction_issue_ids(issues) -> list[str]:
    return [
        issue.issue_id
        for issue in issues
        if (
            re.search(r"修改|修正|纠正|改写", issue.required_change)
            and re.search(
                r"独立\s*(?:finding|结论|复核)|reviewer\s*(?:finding|结论)",
                issue.required_change,
                re.IGNORECASE,
            )
        )
        or (
            re.search(r"独立\s*(?:finding|结论)", issue.reason, re.IGNORECASE)
            and re.search(r"(?:worker|分析结果).{0,500}(?:正确|一致)", issue.reason, re.IGNORECASE)
            and re.search(r"(?:修改|修正|纠正|改写).{0,80}finding", issue.required_change, re.IGNORECASE)
        )
    ]


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
    explicit_repo_prefix = f"repo:{task.unit.repo_id}:"
    if prefix.startswith(explicit_repo_prefix):
        repo_id = task.unit.repo_id
        prefix = prefix[len(explicit_repo_prefix):]
    elif prefix.startswith(repo_prefix):
        repo_id = task.unit.repo_id
        prefix = prefix[len(repo_prefix):]
    elif prefix.startswith("source:"):
        prefix = prefix[len("source:"):]
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


def _require_confirmed_evidence(task: WorkerTask, result: WorkerResult) -> None:
    _evidence_rows(task, result)
    pending = sorted({
        evidence.chunk_id
        for evidence in _all_evidence(result)
        if evidence.status == "pending_confirmation"
    })
    if pending:
        raise ArtifactRejected(
            "证据未绑定当前 Run 的真实索引片段，请使用 read-material 返回的 chunk_id 或"
            f" repo_id:path:line 源码引用：{pending}"
        )


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


def _validate_coverage_claims(task: WorkerTask, result: WorkerResult) -> None:
    """Reject precise Coverage conclusions when this unit has no Coverage input."""

    if task.coverage_context:
        return
    if result.analysis_checkpoint.coverage_priorities:
        raise ArtifactRejected("coverage_context 为空，不得生成 Coverage 优先级")
    if result.analysis_checkpoint.coverage_decisions:
        raise ArtifactRejected("coverage_context 为空，不得生成 Coverage 闭环记录")
    unsupported_claims = [
        risk.risk_id
        for risk in result.risks
        if re.search(
            r"coverage|覆盖率|(?:函数|分支|路径).{0,40}"
            r"(?:未.{0,12}(?:测试|执行|触发)|执行\s*0\s*次|0\s*次)",
            risk.upstream_semantics.existing_tests,
            re.IGNORECASE,
        )
    ]
    if unsupported_claims:
        raise ArtifactRejected(
            "coverage_context 为空，upstream_semantics.existing_tests 不得引用 Coverage "
            f"结论：{unsupported_claims}"
        )


def _material_id(path: str) -> str:
    normalized = path.replace("\\", "/")
    return f"MAT:{normalized}"


def _validate_test_basis_closure(task: WorkerTask, result: WorkerResult) -> None:
    case_by_id = {case.test_case_id: case for case in result.test_cases}
    if len(case_by_id) != len(result.test_cases):
        raise ArtifactRejected("当前单元测试用例 ID 重复")
    invalid_expected_results: list[str] = []
    mixed_behavior_pattern = re.compile(
        r"当前实现|源码当前|现有实现|现行实现|实测|正确预期|正确值|错误值|"
        r"实际为|实际会|(?:会|将|即)\s*FAIL|暴露\s*RISK|复现\s*RISK",
        re.IGNORECASE,
    )
    state_claim_pattern = re.compile(
        r"\b[A-Za-z_][A-Za-z0-9_.]*\s*(?:==|=|!=|>=|<=|>|<)\s*"
        r"(?:nil|true|false|-?\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\")",
        re.IGNORECASE,
    )
    for case in result.test_cases:
        for index, step in enumerate(case.steps, start=1):
            if mixed_behavior_pattern.search(step.expected_result):
                invalid_expected_results.append(f"{case.test_case_id} 第 {index} 步")
            if step.failure_observation == step.expected_result:
                invalid_expected_results.append(f"{case.test_case_id} 第 {index} 步（通过标准与失败观测相同）")
            if step.failure_observation:
                expected_claims = {
                    re.sub(r"\s+", "", item).lower()
                    for item in state_claim_pattern.findall(
                        re.split(r"[（(]", step.expected_result, maxsplit=1)[0]
                    )
                }
                failure_claims = {
                    re.sub(r"\s+", "", item).lower()
                    for item in state_claim_pattern.findall(
                        re.split(r"[（(]", step.failure_observation, maxsplit=1)[0]
                    )
                }
                if expected_claims and expected_claims == failure_claims:
                    invalid_expected_results.append(
                        f"{case.test_case_id} 第 {index} 步（PASS 与 FAIL 状态断言相同）"
                    )
    if invalid_expected_results:
        raise ArtifactRejected(
            "以下测试步骤的 expected_result 混入当前实现行为："
            f"{invalid_expected_results}；expected_result 只能写正确实现的通过标准"
        )

    expected_behavior_risks = [
        risk.risk_id
        for risk in result.risks
        if risk.upstream_semantics.conclusion == "expected_behavior"
    ]
    if expected_behavior_risks:
        raise ArtifactRejected(
            "已被上游语义确认是预期行为的结论不得保留为风险："
            f"{expected_behavior_risks}；需要补测时使用需求、资料或 Coverage 关联"
        )

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
    _validate_coverage_claims(task, result)
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
    coverage_link_errors: list[str] = []
    for coverage_id, decision in decision_by_id.items():
        if decision.disposition == "unreachable_from_supported_entry":
            continue
        if not decision.linked_test_case_ids:
            coverage_link_errors.append(f"{coverage_id} 未关联测试用例")
            continue
        for case_id in decision.linked_test_case_ids:
            case = case_by_id.get(case_id)
            if case is None:
                coverage_link_errors.append(f"{coverage_id} 引用了不存在的测试用例 {case_id}")
            elif coverage_id not in case.linked_coverage_ids:
                coverage_link_errors.append(f"{coverage_id} 的用例 {case_id} 未反向关联")
    if coverage_link_errors:
        raise ArtifactRejected(f"Coverage 双向闭环存在问题：{coverage_link_errors}")

    # Requirement / design 是可选输入；一旦 worker 判定资料为 current，就必须至少有一条资料驱动用例。
    manifest_path = Path(task.source_manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactRejected(f"无法校验当前资料闭环：{manifest_path} ({exc})") from exc
    material_catalog = {
        item.get("path"): item
        for item in manifest.get("material_catalog", [])
        if isinstance(item, dict) and item.get("type") == "material" and item.get("path")
    }
    material_decisions = result.analysis_checkpoint.material_decisions
    decision_paths = [decision.path for decision in material_decisions]
    duplicate_decision_paths = sorted({
        path for path in decision_paths if decision_paths.count(path) > 1
    })
    if duplicate_decision_paths:
        raise ArtifactRejected(f"资料存在重复 decision：{duplicate_decision_paths}")
    unknown_decision_paths = sorted(set(decision_paths) - set(material_catalog))
    if unknown_decision_paths:
        raise ArtifactRejected(
            f"资料 decision 引用了当前 Run 清单外的路径：{unknown_decision_paths}"
        )
    parsed_material_paths = {
        path
        for path, item in material_catalog.items()
        if str(item.get("parse_status", "")).startswith("parsed")
    }
    missing_decision_paths = sorted(parsed_material_paths - set(decision_paths))
    if missing_decision_paths:
        raise ArtifactRejected(
            f"已解析资料尚未逐项填写 material_decisions：{missing_decision_paths}"
        )
    known_material_ids = {_material_id(path) for path in material_catalog}
    linked_material_ids = {
        material_id
        for case in result.test_cases
        for material_id in case.linked_material_ids
    }
    unknown_material_ids = linked_material_ids - known_material_ids
    if unknown_material_ids:
        raise ArtifactRejected(f"测试用例引用了当前 Run 不存在的资料：{sorted(unknown_material_ids)}")
    for decision in material_decisions:
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


def _bind_worker_result(task: WorkerTask, result: WorkerResult) -> None:
    """Restore fields owned by the graph task before checking agent content."""

    result.run_id = task.run_id
    result.unit_id = task.unit.unit_id
    result.attempt = task.attempt
    result.analyzed_scope = list(task.unit.source_scope)
    result.analyzed_context_scope = list(task.unit.context_scope)


def _semantic_path(value: str, repo_id: str) -> str:
    """Compare semantic paths without rejecting normal source-reference detail."""

    normalized = value.replace("\\", "/").strip()
    normalized = normalized.split(" (", 1)[0]
    repo_prefix = f"{repo_id}:"
    if normalized.startswith(repo_prefix):
        normalized = normalized[len(repo_prefix):]
    return re.sub(r":\d+(?:-\d+)?$", "", normalized).strip("/")


def _validate_semantic_check_closure(task: WorkerTask, result: WorkerResult) -> None:
    risk_by_id = {risk.risk_id: risk for risk in result.risks}
    check_by_id = {check.check_id: check for check in task.semantic_check_items}
    risk_paths = {
        risk.risk_id: {
            _semantic_path(path, task.unit.repo_id)
            for path in risk.affected_paths
        }
        for risk in result.risks
    }
    failure_by_id: dict[str, list] = {}
    for failure_path in result.analysis_checkpoint.failure_paths:
        failure_by_id.setdefault(failure_path.path_id, []).append(failure_path)
        unknown_risks = set(failure_path.linked_risk_ids) - set(risk_by_id)
        if unknown_risks:
            raise ArtifactRejected(
                f"failure path {failure_path.path_id} 引用了不存在的风险：{sorted(unknown_risks)}"
            )
    invalid_check_paths = {
        check_id: len(failure_by_id.get(check_id, []))
        for check_id in check_by_id
        if len(failure_by_id.get(check_id, [])) != 1
    }
    if invalid_check_paths:
        raise ArtifactRejected(
            "以下 semantic check 必须各有且只有一条同 ID failure path："
            f"{invalid_check_paths}"
        )
    for check_id, check in check_by_id.items():
        matches = failure_by_id[check_id]
        failure_path = matches[0]
        if failure_path.disposition == "risk" and not failure_path.linked_risk_ids:
            raise ArtifactRejected(f"semantic check {check_id} 判定为 risk 时必须关联对应风险")
        for risk_id in failure_path.linked_risk_ids:
            subject_path = _semantic_path(check.subject_path, task.unit.repo_id)
            if subject_path not in risk_paths[risk_id]:
                raise ArtifactRejected(
                    f"风险 {risk_id} 未声明 semantic check {check_id} 的受影响路径 {check.subject_path}"
                )
    semantic_subjects = {
        _semantic_path(check.subject_path, task.unit.repo_id)
        for check in task.semantic_check_items
    }
    def owning_check_id(path_id: str) -> str | None:
        if path_id in check_by_id:
            return path_id
        base_id = path_id.split(":", 1)[0]
        return base_id if base_id in check_by_id else None

    supported_pairs = {
        (
            risk_id,
            _semantic_path(check_by_id[check_id].subject_path, task.unit.repo_id),
        )
        for path in result.analysis_checkpoint.failure_paths
        if (check_id := owning_check_id(path.path_id)) is not None
        and path.disposition == "risk"
        for risk_id in path.linked_risk_ids
    }
    for risk in result.risks:
        for affected_path in risk_paths[risk.risk_id] & semantic_subjects:
            if (risk.risk_id, affected_path) not in supported_pairs:
                raise ArtifactRejected(
                    f"风险 {risk.risk_id} 声明受影响路径 {affected_path}，但对应 semantic check 未判定并关联该风险"
                )


def _validate_failure_path_internal_consistency(result: WorkerResult) -> None:
    write_verbs = re.compile(r"写入|增加|修改|置为|设为")
    negated_write = re.compile(r"(?:未|没有|不曾|不会).{0,8}(?:写入|增加|修改|置为|设为)")
    for failure_path in result.analysis_checkpoint.failure_paths:
        fields = {
            "trigger": failure_path.trigger,
            "side_effects": failure_path.side_effects,
            "failure": failure_path.failure,
            "caller_handling": failure_path.caller_handling,
            "final_states": failure_path.final_states,
        }
        combined = "；".join(fields.values())
        callbacks = set(re.findall(
            r"\b(C\d+(?:_[A-Za-z0-9]+|\([^)]*\))?)\b.{0,16}(?:执行\s*0\s*次|未执行)",
            combined,
            re.IGNORECASE,
        ))
        for callback in callbacks:
            for field_name, text in fields.items():
                for clause in re.split(r"[；。]", text):
                    if callback not in clause or not write_verbs.search(clause):
                        continue
                    if negated_write.search(clause):
                        continue
                    raise ArtifactRejected(
                        f"failure path {failure_path.path_id} 内部矛盾：{callback} 已声明未执行，"
                        f"但 {field_name} 又把状态写入归因给它"
                    )


def _validate_explicit_semantic_scenarios(
    task: WorkerTask,
    result: WorkerResult,
) -> None:
    """Keep graph-generated scenario checks on their declared execution sequence."""

    paths = {item.path_id: item for item in result.analysis_checkpoint.failure_paths}
    for check in task.semantic_check_items:
        failure_path = paths.get(check.check_id)
        if failure_path is None:
            continue
        combined = "；".join((
            failure_path.trigger,
            failure_path.side_effects,
            failure_path.final_states,
        )).lower()
        if check.check_id.endswith(":retry"):
            if not re.search(r"(?:emit|update)\s*(?:\(|一次|事件)", combined):
                raise ArtifactRejected(
                    f"semantic check {check.check_id} 未执行 Graph 指定的重试后单次 emit/update"
                )
        if check.check_id.endswith(":multi-instance"):
            if "trip" in combined or combined.count("normal") < 2:
                raise ArtifactRejected(
                    f"semantic check {check.check_id} 必须按 Graph 指定的 A normal 后 B normal 场景重放"
                )


def validate_worker_stage_result(
    task: WorkerTask,
    result: WorkerResult,
    expected_stage: str,
) -> None:
    """Validate the cumulative worker artifact at one graph-owned checkpoint."""

    _bind_worker_result(task, result)
    if result.finish_reason != "stop":
        raise ArtifactRejected(f"worker 未正常完成：finish_reason={result.finish_reason}")
    if result.completed_stage != expected_stage:
        raise ArtifactRejected(
            f"worker 结果阶段为 {result.completed_stage}，当前 Graph 等待 {expected_stage}"
        )

    checkpoint = result.analysis_checkpoint
    _validate_failure_path_internal_consistency(result)
    _validate_explicit_semantic_scenarios(task, result)
    if expected_stage == "source_checkpoint":
        if result.evidence or result.business_flows or result.risks or result.test_cases:
            raise ArtifactRejected("源码 checkpoint 只能提交源码理解和 failure paths")
        if set(checkpoint.source_paths_reviewed) != set(task.unit.source_scope):
            raise ArtifactRejected("源码 checkpoint 必须记录全部 source_scope 文件")
        failure_ids = [item.path_id for item in checkpoint.failure_paths]
        invalid_check_paths = {
            check.check_id: failure_ids.count(check.check_id)
            for check in task.semantic_check_items
            if failure_ids.count(check.check_id) != 1
        }
        if invalid_check_paths:
            raise ArtifactRejected(
                "源码 checkpoint 的以下 semantic check 必须各有且只有一条同 ID failure path："
                f"{invalid_check_paths}"
            )
        return

    if expected_stage == "risk_analysis":
        if not result.evidence or not result.business_flows:
            raise ArtifactRejected("风险分析必须包含真实证据和业务流程")
        if not checkpoint.risk_set_frozen:
            raise ArtifactRejected("风险分析尚未冻结风险集合")
        if result.test_cases:
            raise ArtifactRejected("风险分析阶段不能提前生成测试用例")
        _validate_semantic_check_closure(task, result)
        expected_behavior_risks = [
            risk.risk_id
            for risk in result.risks
            if risk.upstream_semantics.conclusion == "expected_behavior"
        ]
        if expected_behavior_risks:
            raise ArtifactRejected(
                "已被上游语义确认是预期行为的结论不得保留为风险："
                f"{expected_behavior_risks}"
            )
        _validate_coverage_claims(task, result)
        _require_confirmed_evidence(task, result)
        _validate_visual_findings(task, result)
        return

    if expected_stage not in {"test_generation", "rework"}:
        raise ArtifactRejected(f"未知 worker 阶段：{expected_stage}")
    validate_worker_result(task, result)


def validate_worker_result(task: WorkerTask, result: WorkerResult) -> None:
    _bind_worker_result(task, result)

    if task.task_type == "rework":
        buggy_oracle_issues = _buggy_oracle_issue_ids(task.review_issues)
        if buggy_oracle_issues:
            raise ArtifactRejected(
                "返工 task 包含把当前错误实现写成测试通过标准的无效 review issue："
                f"{buggy_oracle_issues}"
            )
        conflated_oracle_issues = _conflated_oracle_observation_issue_ids(
            task.review_issues
        )
        if conflated_oracle_issues:
            raise ArtifactRejected(
                "返工 task 把正确通过标准与当前错误观测的差异误判为矛盾，issue 无效："
                f"{conflated_oracle_issues}"
            )
        reviewer_self_correction_issues = _reviewer_self_correction_issue_ids(
            task.review_issues
        )
        if reviewer_self_correction_issues:
            raise ArtifactRejected(
                "返工 task 要求 worker 修正 reviewer 自身误判，issue 无效："
                f"{reviewer_self_correction_issues}"
            )
    if result.finish_reason != "stop":
        raise ArtifactRejected(f"worker 未正常完成：finish_reason={result.finish_reason}")
    expected_stage = "rework" if task.task_type == "rework" else "test_generation"
    if result.completed_stage != expected_stage:
        raise ArtifactRejected(
            f"worker 最终结果阶段必须是 {expected_stage}，实际为 {result.completed_stage}"
        )
    if not result.evidence or not result.business_flows:
        raise ArtifactRejected("worker 正常完成时必须包含真实证据和业务流程")
    checkpoint = result.analysis_checkpoint
    if not checkpoint.risk_set_frozen:
        raise ArtifactRejected("worker 尚未冻结风险集合，不能开始生成或提交测试用例")
    if not checkpoint.counterexamples_checked:
        raise ArtifactRejected("worker 尚未记录提交前的反例检查")
    _validate_semantic_check_closure(task, result)
    _validate_test_basis_closure(task, result)
    _require_confirmed_evidence(task, result)
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
        renamed_cases: dict[str, str] = {}
        for case in result.test_cases:
            original = case.test_case_id
            candidate = original
            suffix = 2
            while candidate in seen_cases:
                candidate = f"{original}-{suffix}"
                suffix += 1
            case.test_case_id = candidate
            seen_cases.add(candidate)
            if candidate != original:
                renamed_cases[original] = candidate
        if renamed_cases:
            for decision in result.analysis_checkpoint.coverage_decisions:
                decision.linked_test_case_ids = [
                    renamed_cases.get(case_id, case_id)
                    for case_id in decision.linked_test_case_ids
                ]


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

    units_without_coverage: set[str] = set()
    for task_ref in task.analysis_tasks:
        try:
            worker_task = WorkerTask.model_validate(
                json.loads(Path(task_ref.task_path).read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ArtifactRejected(
                f"复核无法读取 analysis task：{task_ref.task_path}: {validation_message(exc)}"
            ) from exc
        if not worker_task.coverage_context:
            units_without_coverage.add(task_ref.unit_id)
    invented_coverage_issues = [
        issue.issue_id
        for issue in result.issues
        if issue.unit_id in units_without_coverage
        and re.search(
            r"补充.{0,30}coverage_decisions|增加.{0,30}linked_coverage_ids|"
            r"new_coverage_case|闭环.{0,20}(?:coverage|gap)",
            issue.required_change,
            re.IGNORECASE,
        )
    ]
    if invented_coverage_issues:
        raise ArtifactRejected(
            "analysis task 的 coverage_context 为空，review issue 不得要求伪造 Coverage 闭环："
            f"{invented_coverage_issues}"
        )
    buggy_oracle_issues = _buggy_oracle_issue_ids(result.issues)
    if buggy_oracle_issues:
        raise ArtifactRejected(
            "review issue 不得把当前错误实现写成测试通过标准："
            f"{buggy_oracle_issues}"
        )
    conflated_oracle_issues = _conflated_oracle_observation_issue_ids(result.issues)
    if conflated_oracle_issues:
        raise ArtifactRejected(
            "review issue 不得要求 expected_result 与 failure_observation 相同或不矛盾；"
            "前者是正确通过标准，后者是当前错误观测："
            f"{conflated_oracle_issues}"
        )
    reviewer_self_correction_issues = _reviewer_self_correction_issue_ids(result.issues)
    if reviewer_self_correction_issues:
        raise ArtifactRejected(
            "review issue 只能要求修改 worker result；独立 finding 被源码推翻时，"
            "应在 disposition reason 中记录 reviewer 自身纠正，不得派发 worker 返工："
            f"{reviewer_self_correction_issues}"
        )
    unknown_findings = {item.unit_id for item in result.independent_findings} - known_units
    if unknown_findings:
        raise ArtifactRejected(f"独立发现引用了未知单元：{sorted(unknown_findings)}")
    if task.stage in {"comparison_review", "rework_verification"}:
        if independent_result is None:
            raise ArtifactRejected("复核缺少已完成的独立复核结果")
        if result.reviewer_id != independent_result.reviewer_id:
            raise ArtifactRejected("复核必须由原独立 reviewer 完成")
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
            raise ArtifactRejected("复核必须逐项保留独立复核 findings")
        for key, finding in original.items():
            comparison = compared[key]
            if comparison.finding != finding.finding or comparison.evidence != finding.evidence:
                raise ArtifactRejected("复核不能改写独立复核结论或证据")

        worker_risks: set[tuple[str, str]] = set()
        worker_tests: set[tuple[str, str]] = set()
        worker_test_expectations: dict[tuple[str, str], list[str]] = {}
        worker_test_failures: dict[tuple[str, str], list[str | None]] = {}
        risks_without_confirmed_evidence: list[tuple[str, str]] = []
        for result_ref in task.analysis_results:
            try:
                worker_result = WorkerResult.model_validate(
                    json.loads(Path(result_ref.result_path).read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, ValidationError) as exc:
                raise ArtifactRejected(
                    f"复核无法读取 worker result：{result_ref.result_path}: {validation_message(exc)}"
                ) from exc
            worker_risks.update(
                (result_ref.unit_id, risk.risk_id) for risk in worker_result.risks
            )
            worker_tests.update(
                (result_ref.unit_id, case.test_case_id) for case in worker_result.test_cases
            )
            worker_test_expectations.update({
                (result_ref.unit_id, case.test_case_id): [
                    step.expected_result for step in case.steps
                ]
                for case in worker_result.test_cases
            })
            worker_test_failures.update({
                (result_ref.unit_id, case.test_case_id): [
                    step.failure_observation for step in case.steps
                ]
                for case in worker_result.test_cases
            })
            risks_without_confirmed_evidence.extend(
                (result_ref.unit_id, risk.risk_id)
                for risk in worker_result.risks
                if not risk.evidence
                or any(evidence.status != "confirmed" for evidence in risk.evidence)
            )

        if result.status == "PASS" and risks_without_confirmed_evidence:
            raise ArtifactRejected(
                "PASS 的风险必须全部使用当前 Run 已确认的证据："
                f"{sorted(risks_without_confirmed_evidence)}"
            )

        actual_test_checks = {
            (item.unit_id, item.test_case_id): item for item in result.test_case_checks
        }
        if len(actual_test_checks) != len(result.test_case_checks):
            raise ArtifactRejected("每条 TestCase 只能有一条独立通过标准检查")
        if set(actual_test_checks) != set(worker_test_expectations):
            missing = sorted(set(worker_test_expectations) - set(actual_test_checks))
            unknown = sorted(set(actual_test_checks) - set(worker_test_expectations))
            raise ArtifactRejected(
                "复核必须逐条检查全部 TestCase 的通过标准："
                f"missing={missing}, unknown={unknown}"
            )
        for key, expected_results in worker_test_expectations.items():
            if actual_test_checks[key].expected_results != expected_results:
                raise ArtifactRejected(
                    f"TestCase 通过标准检查未逐项回显当前预期：{key}"
                )
            if actual_test_checks[key].failure_observations != worker_test_failures[key]:
                raise ArtifactRejected(
                    f"TestCase 失败观测检查未逐项回显当前用例：{key}"
                )
        if result.status == "PASS":
            invalid = sorted(
                key for key, item in actual_test_checks.items()
                if item.verdict != "valid"
            )
            if invalid:
                raise ArtifactRejected(
                    f"PASS 不能包含 invalid 或 unresolved TestCase：{invalid}"
                )

        linked_risks: list[tuple[str, str]] = []
        linked_tests: list[tuple[str, str]] = []
        for finding in result.independent_findings:
            finding_risks = [
                (finding.unit_id, risk_id) for risk_id in finding.linked_worker_risk_ids
            ]
            finding_tests = [
                (finding.unit_id, case_id)
                for case_id in finding.linked_worker_test_case_ids
            ]
            if finding.worker_disposition == "reasonably_excluded" and (
                finding_risks or finding_tests
            ):
                raise ArtifactRejected(
                    "reasonably_excluded finding 不能同时关联 worker 风险或用例；"
                    "worker 保留同一路径时必须标 contradiction 并生成 issue"
                )
            linked_risks.extend(finding_risks)
            linked_tests.extend(finding_tests)

        unknown_risks = set(linked_risks) - worker_risks
        unknown_tests = set(linked_tests) - worker_tests
        if unknown_risks or unknown_tests:
            raise ArtifactRejected(
                f"复核关联了不存在的 worker 产物：risks={sorted(unknown_risks)}, "
                f"tests={sorted(unknown_tests)}"
            )
        duplicate_risks = sorted({item for item in linked_risks if linked_risks.count(item) > 1})
        duplicate_tests = sorted({item for item in linked_tests if linked_tests.count(item) > 1})
        if duplicate_risks or duplicate_tests:
            raise ArtifactRejected(
                f"每个 worker 产物只能由一个独立 finding 负责对照："
                f"risks={duplicate_risks}, tests={duplicate_tests}"
            )
        missing_risks = sorted(worker_risks - set(linked_risks))
        missing_tests = sorted(worker_tests - set(linked_tests))
        if missing_risks or missing_tests:
            raise ArtifactRejected(
                "复核结果的 finding 关联不完整：请把下列风险补入且只补入一个 finding 的 "
                "linked_worker_risk_ids，把下列用例补入且只补入一个 finding 的 "
                "linked_worker_test_case_ids；"
                f"missing_risks={missing_risks}, missing_tests={missing_tests}"
            )


def validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)
