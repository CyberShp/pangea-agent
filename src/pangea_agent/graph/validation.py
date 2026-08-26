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
        if (
            re.search(
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
        )
        or re.search(
            r"expected[_ ]?result\s*(?::|：|改为|写为)\s*.{0,80}"
            r"(?:asan|valgrind|double[- ]free|use[- ]after[- ]free|"
            r"崩溃|core dump|断言失败)",
            issue.required_change,
            re.IGNORECASE,
        )
        or (
            re.search(r"expected[_ ]?result|测试预期|预期结果", issue.required_change, re.IGNORECASE)
            and re.search(
                r"actual\s+(?:buggy|broken|faulty)\s+(?:behavior|outcome)|"
                r"(?:当前|实际)(?:错误|缺陷|故障)(?:行为|结果|观测)",
                issue.required_change,
                re.IGNORECASE,
            )
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
            re.search(r"修改|修正|纠正|改写|改为|设为|标记", issue.required_change)
            and re.search(
                r"(?:独立|independent)\s*(?:finding|结论|复核)|reviewer\s*(?:finding|结论)",
                issue.required_change,
                re.IGNORECASE,
            )
        )
        or (
            re.search(r"独立\s*(?:finding|结论)", issue.reason, re.IGNORECASE)
            and re.search(r"(?:worker|分析结果).{0,500}(?:正确|一致)", issue.reason, re.IGNORECASE)
            and re.search(r"(?:修改|修正|纠正|改写).{0,80}finding", issue.required_change, re.IGNORECASE)
        )
        or (
            re.search(
                r"reviewer\s*self[- ]?correction|reviewer.{0,40}(?:自身|原始|分析)(?:错误|问题)|"
                r"finding.{0,60}(?:scope|范围).{0,40}(?:review|reviewer)",
                issue.reason,
                re.IGNORECASE,
            )
            and re.search(
                r"无需\s*worker|不需要\s*worker|no\s+worker|does not require\s+worker",
                issue.required_change,
                re.IGNORECASE,
            )
        )
    ]


def _stale_artifact_restoration_issue_ids(
    issues,
    worker_risks: set[tuple[str, str]],
    worker_tests: set[tuple[str, str]],
) -> list[str]:
    current_by_unit: dict[str, set[str]] = {}
    for unit_id, artifact_id in worker_risks | worker_tests:
        current_by_unit.setdefault(unit_id, set()).add(artifact_id)
    restoration = re.compile(
        r"恢复|补回|重新加入|重新添加|restore|re[- ]?add",
        re.IGNORECASE,
    )
    return [
        issue.issue_id
        for issue in issues
        if restoration.search(issue.required_change)
        and not any(
            artifact_id in f"{issue.reason}\n{issue.required_change}"
            for artifact_id in current_by_unit.get(issue.unit_id, set())
        )
    ]


def _non_actionable_review_issue_ids(issues) -> list[str]:
    return [
        issue.issue_id
        for issue in issues
        if re.fullmatch(
            r"\s*(?:"
            r"(?:consider|maybe|perhaps|possibly)\s+(?:updat\w*|chang\w*|remov\w*|add\w*|revise\w*)"
            r"(?:\s+(?:it|this|the\s+result|the\s+description|related\s+text|TC-[\w-]+))?|"
            r"(?:建议|考虑|也许(?:需要)?|可能需要|酌情)(?:更新|修改|删除|新增|补充|修正|调整)"
            r"(?:一下|此处|相关描述|相关内容|该项|描述)?"
            r")[。.!]?\s*",
            issue.required_change,
            re.IGNORECASE,
        )
        or re.search(
            r"无需(?:进行)?返工|不需要(?:进行)?返工|无需\s*worker|no\s+worker\s+rework|"
            r"does\s+not\s+require\s+(?:worker\s+)?rework",
            issue.required_change,
            re.IGNORECASE,
        )
    ]


def _reviewer_owned_field_issue_ids(issues) -> list[str]:
    return [
        issue.issue_id
        for issue in issues
        if re.search(
            r"(?:修改|更新|填写|补充|修正|调整|change|update|fill|edit)"
            r".{0,40}\b(?:current_behavior|test_case_checks)\b",
            issue.required_change,
            re.IGNORECASE,
        )
    ]


def _finding_excludes_linked_leak(finding: str, risk_claim: str) -> bool:
    if not re.search(r"泄漏|leak", risk_claim, re.IGNORECASE):
        return False
    return bool(re.search(
        r"未发现.{0,20}泄漏|不会.{0,12}泄漏|无.{0,8}泄漏|"
        r"全部释放|完整清理|"
        r"no .{0,20}leak|does not leak|all .{0,20}(?:freed|released)|complete cleanup",
        finding,
        re.IGNORECASE,
    ))


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
    rows = _evidence_rows(task, result)
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
    historical_only_risks = []
    for risk in result.risks:
        risk_rows = [rows.get(evidence.chunk_id) for evidence in risk.evidence]
        if any(row and "historical_issue" in row["tags"] for row in risk_rows) and not any(
            row and row["source_type"] in {"code", "source_context"}
            for row in risk_rows
        ):
            historical_only_risks.append(risk.risk_id)
    if historical_only_risks:
        raise ArtifactRejected(
            "历史问题只能作为当前风险线索；以下 RiskCard 必须同时引用当前源码证据："
            f"{sorted(historical_only_risks)}"
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
    serialized_result = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    impossible_comparison = re.search(
        r"(?P<value>-?\d+)\s*(?:>=|<=|==)\s*(?P=value).{0,40}"
        r"(?:evaluates?\s+false|=\s*false|为\s*false|判定为假)",
        serialized_result,
        re.IGNORECASE,
    )
    if impossible_comparison:
        raise ArtifactRejected(
            "源码边界判断存在可直接求值的事实错误："
            f"{impossible_comparison.group(0)}"
        )

    case_by_id = {case.test_case_id: case for case in result.test_cases}
    if len(case_by_id) != len(result.test_cases):
        raise ArtifactRejected("当前单元测试用例 ID 重复")
    invalid_expected_results: list[str] = []
    mixed_behavior_pattern = re.compile(
        r"当前实现|源码当前|现有实现|现行实现|实测|正确预期|正确值|错误值|"
        r"实际为|实际会|(?:会|将|即)\s*FAIL|暴露\s*RISK|复现\s*RISK",
        re.IGNORECASE,
    )
    defect_signal_pattern = re.compile(
        r"(?:asan|valgrind).{0,20}(?:报告|reports?)|"
        r"(?:发生|触发|检测到).{0,12}(?:double[- ]free|use[- ]after[- ]free|"
        r"heap[- ]buffer[- ]overflow)|进程.{0,12}(?:崩溃|终止)|"
        r"core dump|断言失败|undefined\s+behavio[u]?r|"
        r"(?:process|target|host|controller)?.{0,12}\bcrash(?:es|ed)?\b|"
        r"\bhangs?\s+waiting\b|\bsilent\s+loss\b",
        re.IGNORECASE,
    )
    negated_signal_pattern = re.compile(
        r"(?:不|无|未|不得|不应|不会|without\b|\bno\b|\bnot\b)",
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
            signal = defect_signal_pattern.search(step.expected_result)
            signal_context = (
                step.expected_result[max(0, signal.start() - 6):signal.end()]
                if signal else ""
            )
            if signal and not negated_signal_pattern.search(signal_context):
                invalid_expected_results.append(
                    f"{case.test_case_id} 第 {index} 步（缺陷信号只能写 failure_observation）"
                )
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

    deleted_placeholders = [
        risk.risk_id
        for risk in result.risks
        if re.search(r"^\s*\[?(?:deleted|已删除|删除)\b", risk.title, re.IGNORECASE)
    ]
    if deleted_placeholders:
        raise ArtifactRejected(
            "review 要求删除的风险必须从 risks 数组真正移除，不得改名保留占位："
            f"{deleted_placeholders}"
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

    # Coverage 输入可选；一旦 task 中存在真实 gap，就必须逐项闭环。
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
        raise ArtifactRejected(f"Coverage 缺口尚未逐项闭环：{sorted(missing_decisions)}")
    missing_linked_decisions = linked_coverage_ids - set(decision_by_id)
    if missing_linked_decisions:
        raise ArtifactRejected(
            f"已生成 Coverage 用例但缺少对应闭环记录：{sorted(missing_linked_decisions)}"
        )
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
    if set(task.allowed_material_paths) != parsed_material_paths:
        raise ArtifactRejected("worker task 的 allowed_material_paths 与冻结资料清单不一致")
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


def validate_complete_unit_coverage(units: list[dict], groups: list[dict]) -> None:
    expected = {
        (group["repo_id"], path)
        for group in groups
        for path in group.get("code_paths", [])
    }
    assigned = {
        (unit["repo_id"], path)
        for unit in units
        for path in unit.get("source_scope", [])
    }
    if assigned != expected:
        raise ArtifactRejected(
            "Analysis Unit 源码分配不完整："
            f"missing={sorted(expected - assigned)}, unexpected={sorted(assigned - expected)}"
        )


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


def _validate_risk_source_scope(task: WorkerTask, result: WorkerResult) -> None:
    """Require every RiskCard to be owned by this unit, not only by its context."""

    source_scopes = {
        _semantic_path(path, task.unit.repo_id) or "."
        for path in task.unit.source_scope
    }
    context_only_risks: dict[str, list[str]] = {}
    for risk in result.risks:
        affected_paths = {
            _semantic_path(path, task.unit.repo_id)
            for path in risk.affected_paths
        }
        if any(
            scope == "." or path == scope or path.startswith(f"{scope}/")
            for path in affected_paths
            for scope in source_scopes
        ):
            continue
        context_only_risks[risk.risk_id] = sorted(affected_paths)
    if context_only_risks:
        raise ArtifactRejected(
            "以下风险的 affected_paths 未落在当前分析单元 source_scope；"
            "context_scope 只能提供上下游语义证据，不能单独成为本单元风险对象："
            f"{context_only_risks}"
        )


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
    supported_risk_ids = {
        risk_id
        for failure_path in result.analysis_checkpoint.failure_paths
        if failure_path.disposition in {"risk", "unresolved"}
        for risk_id in failure_path.linked_risk_ids
    }
    unlinked_risk_paths = [
        failure_path.path_id
        for failure_path in result.analysis_checkpoint.failure_paths
        if failure_path.disposition in {"risk", "unresolved"}
        and not failure_path.linked_risk_ids
    ]
    if unlinked_risk_paths:
        raise ArtifactRejected(
            "以下 risk/unresolved failure path 尚未转换并关联 RiskCard："
            f"{sorted(unlinked_risk_paths)}"
        )
    risks_without_failure_path = set(risk_by_id) - supported_risk_ids
    if risks_without_failure_path:
        raise ArtifactRejected(
            "以下风险没有被当前 risk/unresolved failure path 关联："
            f"{sorted(risks_without_failure_path)}"
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


def _known_c_container_semantic_artifact_ids(
    task: WorkerTask,
    result: WorkerResult,
) -> list[str]:
    if not any(
        path.lower().endswith((".c", ".h", ".cc", ".cpp", ".cxx", ".hpp"))
        for path in (*task.unit.source_scope, *task.unit.context_scope)
    ):
        return []
    claims: list[tuple[str, str]] = []
    for risk in result.risks:
        claims.append((risk.risk_id, "\n".join((
            risk.title,
            risk.trigger,
            risk.system_result,
            risk.external_observation,
            risk.exclusion_condition,
        ))))
    for case in result.test_cases:
        claims.append((case.test_case_id, "\n".join(
            text
            for step in case.steps
            for text in (step.action, step.expected_result, step.failure_observation or "")
        )))
    return [
        artifact_id
        for artifact_id, claim in claims
        if re.search(r"TAILQ_REMOVE.{0,100}(?:no[- ]?op|silently succeeds|silent)", claim, re.IGNORECASE)
        or re.search(r"(?:no[- ]?op|silently succeeds|silent).{0,100}TAILQ_REMOVE", claim, re.IGNORECASE)
    ]


def _retained_realloc_source_paths(task: WorkerTask) -> set[str]:
    """Find the narrow realloc form where failure preserves a returned old pointer."""

    repository = next(
        (item for item in task.repositories if item.repo_id == task.unit.repo_id),
        None,
    )
    if repository is None:
        return set()
    retained: set[str] = set()
    for relative_path in task.unit.source_scope:
        if not relative_path.lower().endswith((".c", ".h", ".cc", ".cpp", ".cxx", ".hpp")):
            continue
        source_path = Path(repository.source_root) / relative_path
        try:
            source = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for allocation in re.finditer(
            r"\b(?P<temporary>[A-Za-z_]\w*)\s*=\s*realloc\s*\(\s*"
            r"(?P<original>[A-Za-z_]\w*)\s*,",
            source,
        ):
            temporary = allocation.group("temporary")
            original = allocation.group("original")
            tail = source[allocation.end():allocation.end() + 12000]
            failure = re.search(
                rf"if\s*\(\s*(?:{re.escape(temporary)}\s*==\s*NULL|"
                rf"!\s*{re.escape(temporary)})\s*\)\s*\{{(?P<body>.{{0,1200}}?)\}}",
                tail,
                re.DOTALL,
            )
            if failure is None or not re.search(r"\bbreak\s*;", failure.group("body")):
                continue
            after_failure = tail[failure.end():]
            if not re.search(
                rf"\b{re.escape(original)}\s*=\s*{re.escape(temporary)}\s*;",
                after_failure,
            ):
                continue
            if not re.search(rf"\breturn\s+{re.escape(original)}\s*;", after_failure):
                continue
            retained.add(relative_path)
    return retained


def _claims_failed_realloc_leaks(text: str) -> bool:
    return bool(
        re.search(r"realloc.{0,180}(?:memory\s+leak|leaks?|泄漏)", text, re.IGNORECASE | re.DOTALL)
        or re.search(r"(?:memory\s+leak|leaks?|泄漏).{0,180}realloc", text, re.IGNORECASE | re.DOTALL)
    )


def _post_failure_realloc_increment_variables(task: WorkerTask) -> set[str]:
    repository = next(
        (item for item in task.repositories if item.repo_id == task.unit.repo_id),
        None,
    )
    if repository is None:
        return set()
    counters: set[str] = set()
    for relative_path in task.unit.source_scope:
        if not relative_path.lower().endswith((".c", ".h", ".cc", ".cpp", ".cxx", ".hpp")):
            continue
        try:
            source = (Path(repository.source_root) / relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for allocation in re.finditer(
            r"\b(?P<temporary>[A-Za-z_]\w*)\s*=\s*realloc\s*\(",
            source,
        ):
            temporary = allocation.group("temporary")
            tail = source[allocation.end():allocation.end() + 4000]
            failure = re.search(
                rf"if\s*\(\s*(?:{re.escape(temporary)}\s*==\s*NULL|"
                rf"!\s*{re.escape(temporary)})\s*\)\s*\{{(?P<body>.{{0,1200}}?)\}}",
                tail,
                re.DOTALL,
            )
            if failure is None or not re.search(r"\bbreak\s*;", failure.group("body")):
                continue
            after_failure = tail[failure.end():]
            next_realloc = re.search(r"\brealloc\s*\(", after_failure)
            if next_realloc is not None:
                after_failure = after_failure[:next_realloc.start()]
            counters.update(re.findall(r"\b([A-Za-z_]\w*)\s*\+\+", after_failure))
            counters.update(re.findall(r"\+\+\s*([A-Za-z_]\w*)\b", after_failure))
    return counters


def _claims_failed_realloc_runs_post_increment(text: str, counters: set[str]) -> bool:
    for counter in counters:
        counter_claim = (
            rf"realloc.{{0,500}}\b{re.escape(counter)}\b.{{0,120}}"
            rf"(?:increment|increase|already|多\s*1|递增|增加|虚假|不一致)"
            rf"|\b{re.escape(counter)}\b.{{0,120}}"
            rf"(?:increment|increase|already|多\s*1|递增|增加|虚假|不一致)"
            rf".{{0,500}}realloc"
        )
        if re.search(counter_claim, text, re.IGNORECASE | re.DOTALL):
            return True
    return False


def _known_retained_realloc_artifact_ids(
    task: WorkerTask,
    result: WorkerResult,
) -> list[str]:
    if not _retained_realloc_source_paths(task):
        return []
    post_failure_counters = _post_failure_realloc_increment_variables(task)
    claims: list[tuple[str, str]] = []
    for risk in result.risks:
        claims.append((risk.risk_id, "\n".join((
            risk.title,
            risk.trigger,
            risk.system_result,
            risk.external_observation,
            risk.exclusion_condition,
        ))))
    for case in result.test_cases:
        claims.append((case.test_case_id, "\n".join(
            text
            for step in case.steps
            for text in (step.action, step.expected_result, step.failure_observation or "")
        )))
    return [
        artifact_id
        for artifact_id, claim in claims
        if _claims_failed_realloc_leaks(claim)
        or _claims_failed_realloc_runs_post_increment(claim, post_failure_counters)
    ]


def _locally_allocated_c_variables(task: WorkerTask) -> set[str]:
    repository = next(
        (item for item in task.repositories if item.repo_id == task.unit.repo_id),
        None,
    )
    if repository is None:
        return set()
    variables: set[str] = set()
    for relative_path in task.unit.source_scope:
        if not relative_path.lower().endswith((".c", ".h", ".cc", ".cpp", ".cxx", ".hpp")):
            continue
        try:
            source = (Path(repository.source_root) / relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for allocation in re.finditer(
            r"\b(?P<variable>[A-Za-z_]\w*)\s*=\s*(?:calloc|malloc)\s*\(",
            source,
        ):
            variable = allocation.group("variable")
            preceding = source[max(0, allocation.start() - 2500):allocation.start()]
            if re.search(rf"\*\s*{re.escape(variable)}\s*;", preceding):
                variables.add(variable)
    return variables


def _known_local_allocation_concurrency_artifact_ids(
    task: WorkerTask,
    result: WorkerResult,
) -> list[str]:
    variables = _locally_allocated_c_variables(task)
    if not variables:
        return []
    claims: list[tuple[str, str]] = []
    for risk in result.risks:
        claims.append((risk.risk_id, "\n".join((
            risk.title,
            risk.trigger,
            risk.system_result,
            risk.exclusion_condition,
            risk.upstream_semantics.reachability,
            risk.upstream_semantics.caller_constraints,
        ))))
    for case in result.test_cases:
        claims.append((case.test_case_id, "\n".join(
            text
            for step in case.steps
            for text in (step.action, step.expected_result, step.failure_observation or "")
        )))
    invalid: list[str] = []
    for artifact_id, claim in claims:
        if not re.search(r"concurr|across\s+requests?|cross[- ]request|并发|跨请求", claim, re.IGNORECASE):
            continue
        if not re.search(
            r"shared|stale|dangling|use[- ]after[- ]free|freed|共享|悬空|释放后|已释放",
            claim,
            re.IGNORECASE,
        ):
            continue
        if any(re.search(rf"\b{re.escape(variable)}\b", claim) for variable in variables):
            invalid.append(artifact_id)
    return invalid


def _task_c_sources(task: WorkerTask) -> list[str]:
    repository = next(
        (item for item in task.repositories if item.repo_id == task.unit.repo_id),
        None,
    )
    if repository is None:
        return []
    sources: list[str] = []
    for relative_path in dict.fromkeys((*task.unit.source_scope, *task.unit.context_scope)):
        if not relative_path.lower().endswith((".c", ".h", ".cc", ".cpp", ".cxx", ".hpp")):
            continue
        try:
            sources.append(
                (Path(repository.source_root) / relative_path).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError):
            continue
    return sources


def _known_c_precheck_order_artifact_ids(
    task: WorkerTask,
    result: WorkerResult,
) -> list[str]:
    """Reject effects attributed before the source reaches its enqueue/callback step."""

    sources = _task_c_sources(task)
    has_guarded_enqueue = any(
        re.search(
            r"spdk_vtophys\s*\([^;]+;.*?"
            r"SPDK_VTOPHYS_ERROR.*?return\s+-EFAULT\s*;.*?"
            r"seg_len\s*=.*?if\s*\(\s*seg_len\s*==\s*0\s*\).*?"
            r"return\s+-EINVAL\s*;.*?"
            r"ring_buff_count.*?return\s+1\s*;.*?"
            r"ae4dma_prep_copy\s*\(",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_error_callback = any(
        re.search(
            r"desc_status\s*!=\s*AE4DMA_DMA_DESC_COMPLETED.*?"
            r"desc_err_code\s*=.*?\}.*?"
            r"callback_fn\s*\).*?callback_fn\s*\([^;]*desc_err_code\s*\)",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_start_failure_cleanup = any(
        re.search(
            r"if\s*\(\s*ae4dma_channel_start\s*\([^;]+?!=\s*0\s*\)\s*\{.*?"
            r"ae4dma_channel_destruct\s*\([^;]+;.*?free\s*\(\s*ae4dma\s*\)",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_lockstep_event_count = any(
        re.search(
            r"sub_desc_cnt\s*=\s*cmd_q->ring_buff_count\s*;.*?"
            r"while\s*\(\s*sub_desc_cnt\s*\).*?"
            r"assert\s*\(\s*cmd_q->ring_buff_count\s*>\s*0\s*\)\s*;.*?"
            r"cmd_q->ring_buff_count--\s*;.*?sub_desc_cnt--\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_vtophys_size_assignment = any(
        re.search(
            r"size\s*=\s*cmd_q->queue_size\s*;.*?"
            r"spdk_vtophys\s*\(\s*cmd_q->qbase_addr\s*,\s*&size\s*\)",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_submitted_retry_without_count_drift = any(
        re.search(
            r"if\s*\(\s*desc_status\s*==\s*AE4DMA_DMA_DESC_SUBMITTED\s*\)\s*\{?\s*"
            r"break\s*;.*?ring_buff_count--\s*;.*?sub_desc_cnt--\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_guarded_callback = any(
        re.search(
            r"if\s*\(\s*cmd_q->ring\s*\[\s*tail\s*\]\.callback_fn\s*\)\s*\{?\s*"
            r"cmd_q->ring\s*\[\s*tail\s*\]\.callback_fn\s*\(",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_unmap_return_propagation = any(
        re.search(
            r"ae4dma_unmap_pci_bar\s*\([^)]*\).*?"
            r"rc\s*=\s*spdk_pci_device_unmap_bar\s*\([^;]+;.*?return\s+rc\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_callback_assignment_after_iteration = any(
        re.search(
            r"return\s+-EFAULT\s*;.*?last_desc\s*=\s*cb_desc\s*;.*?"
            r"if\s*\(\s*last_desc\s*\).*?callback_fn\s*=\s*cb_fn\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_queue_count_passthrough = any(
        re.search(
            r"ae4dma_config_queues_per_device\s*\([^)]*\).*?"
            r"num_hw_queues\s*<=\s*AE4DMA_MAX_HW_QUEUES.*?return\s+false\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    ) and any(
        re.search(
            r"if\s*\(\s*!ae4dma_config_queues_per_device\s*\(\s*hw_queues\s*\)\s*\)"
            r"\s*\{.*?q_per_eng\s*=\s*hw_queues\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_lost_unmap_result = _has_lost_unmap_result(task)
    claims: list[tuple[str, str]] = []
    for failure_path in result.analysis_checkpoint.failure_paths:
        claims.append((failure_path.path_id, "\n".join((
            failure_path.trigger,
            failure_path.side_effects,
            failure_path.failure,
            failure_path.caller_handling,
            failure_path.final_states,
        ))))
    for risk in result.risks:
        claims.append((risk.risk_id, "\n".join((
            risk.title,
            risk.trigger,
            risk.system_result,
            risk.external_observation,
            risk.exclusion_condition,
        ))))
    for case in result.test_cases:
        claims.append((case.test_case_id, "\n".join(
            text
            for step in case.steps
            for text in (step.action, step.expected_result, step.failure_observation or "")
        )))

    invalid: list[str] = []
    for artifact_id, claim in claims:
        identified_claim = f"{artifact_id}\n{claim}"
        proven_unmap_return_loss = has_lost_unmap_result and bool(
            re.search(r"unmap", claim, re.IGNORECASE)
            and re.search(r"destruct|析构|detach", claim, re.IGNORECASE)
            and re.search(
                r"(?:ignore|drop|忽略|丢弃|未检查).{0,80}(?:return|返回值|rc)|"
                r"(?:return|返回值|rc).{0,80}(?:ignore|drop|忽略|丢弃|未检查)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        explicitly_preserves_order = bool(re.search(
            r"(?:当前|current).{0,80}(?:描述符|descriptor).{0,80}"
            r"(?:未|没有|not).{0,24}(?:入队|写入|enqueue|written)",
            claim,
            re.IGNORECASE | re.DOTALL,
        ))
        guarded_current_enqueue = has_guarded_enqueue and (
            (not explicitly_preserves_order and re.search(
                r"(?:vtophys|seg_len\s*=\s*0).{0,260}"
                r"(?:当前|current|零长度).{0,100}"
                r"(?:描述符|descriptor).{0,100}"
                r"(?:已入队|写入\s*ring|enqueued|written\s+to\s+(?:the\s+)?ring)",
                claim,
                re.IGNORECASE | re.DOTALL,
            ))
            or (not explicitly_preserves_order and re.search(
                r"(?:零长度|zero[- ]length).{0,100}(?:描述符|descriptor).{0,100}"
                r"(?:已入队|enqueued|written)",
                claim,
                re.IGNORECASE | re.DOTALL,
            ))
            or re.search(
                r"(?:ring\s*(?:full|满)|环满).{0,260}"
                r"(?:retry|重新调用|再次调用).{0,160}(?:overwrite|覆盖).{0,120}"
                r"(?:descriptor|描述符)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        guarded_invalid_address = has_guarded_enqueue and bool(
            re.search(r"vtophys", claim, re.IGNORECASE)
            and re.search(
                r"(?:无效物理地址|invalid\s+physical\s+address).{0,100}"
                r"(?:描述符|descriptor)|(?:描述符|descriptor).{0,100}"
                r"(?:无效物理地址|invalid\s+physical\s+address)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        guarded_ring_overwrite = has_guarded_enqueue and bool(
            re.search(r"(?:ring\s*(?:full|满)|环满)", claim, re.IGNORECASE)
            and re.search(
                r"(?:retry|重新调用|再次调用).{0,300}(?:overwrite|覆盖).{0,160}"
                r"(?:descriptor|描述符)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        expected_zero_length_guard = has_guarded_enqueue and bool(
            re.search(r"seg_len\s*=\s*0", claim, re.IGNORECASE)
            and re.search(r"(?:未|无|not|no).{0,30}(?:入队|残留|enqueue|residual)", claim, re.IGNORECASE)
            and re.search(r"(?:-EINVAL|return(?:s)?\s+-EINVAL|返回\s+-EINVAL)", claim, re.IGNORECASE)
        )
        skipped_error_callback = has_error_callback and bool(re.search(
            r"(?:error|错误|异常).{0,260}"
            r"(?:callback\s+(?:is\s+)?not\s+(?:called|invoked)|"
            r"不执行\s*callback|callback\s*未(?:被)?(?:调用|执行))",
            claim,
            re.IGNORECASE | re.DOTALL,
        ))
        callback_already_propagates_error = has_error_callback and bool(
            re.search(
                r"(?:return|返回值).{0,180}(?:不含|无法|does\s+not|without).{0,80}"
                r"(?:error|错误)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
            and re.search(
                r"callback.{0,160}(?:err_code|error\s+code|错误码)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        hypothetical_unchecked_return = bool(re.search(
            r"(?:返回|return(?:s|ed)?)\s+NULL.{0,180}"
            r"(?:若|if)\s*(?:调用方|caller).{0,80}(?:未|不|does\s+not|fails?\s+to)"
            r".{0,40}(?:检查|check).{0,180}(?:空指针|null pointer|SIGSEGV|crash|崩溃)",
            claim,
            re.IGNORECASE | re.DOTALL,
        ))
        cleanup_ignored = (
            has_start_failure_cleanup
            and not proven_unmap_return_loss
            and bool(
                re.search(r"(?:leak|泄漏|未释放|not\s+free|isn't\s+free)", claim, re.IGNORECASE)
                and (
                    (
                        re.search(r"qbase_addr|queue\s*(?:base|buffer)|队列内存", claim, re.IGNORECASE)
                        and re.search(
                            r"(?:dma_zmalloc|vtophys|ring|calloc|分配).{0,160}(?:fail|失败)|"
                            r"(?:fail|失败).{0,160}(?:dma_zmalloc|vtophys|ring|calloc|分配)",
                            claim,
                            re.IGNORECASE | re.DOTALL,
                        )
                    )
                    or (
                        re.search(r"(?:PCI\s*)?BAR|pci.{0,40}map|BAR\s*映射", claim, re.IGNORECASE)
                        and re.search(
                            r"(?:dma_zmalloc|vtophys|ring|calloc|分配).{0,160}(?:fail|失败)|"
                            r"(?:fail|失败).{0,160}(?:dma_zmalloc|vtophys|ring|calloc|分配)",
                            claim,
                            re.IGNORECASE | re.DOTALL,
                        )
                    )
                )
            )
        )
        lockstep_assert_ignored = has_lockstep_event_count and bool(
            re.search(r"(?:assert|断言)", claim, re.IGNORECASE)
            and re.search(
                r"(?:ring_buff_count|underflow|下溢|计数).{0,180}"
                r"(?:未|无|not|without).{0,40}(?:检查|guard|check)|"
                r"(?:未|无|not|without).{0,40}(?:检查|guard|check).{0,180}"
                r"(?:ring_buff_count|underflow|下溢|计数)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        assigned_vtophys_size_ignored = has_vtophys_size_assignment and bool(
            re.search(r"spdk_vtophys|vtophys", claim, re.IGNORECASE)
            and re.search(r"(?:size|长度)", claim, re.IGNORECASE)
            and re.search(
                r"(?:未赋值|未初始化|错误|需确认|uninitialized|not\s+(?:set|initialized)|incorrect|wrong)",
                claim,
                re.IGNORECASE,
            )
        )
        guarded_callback_ignored = has_guarded_callback and bool(
            re.search(r"callback", claim, re.IGNORECASE)
            and re.search(r"(?:NULL|空指针|为空)", claim, re.IGNORECASE)
            and re.search(r"(?:crash|崩溃|assert|断言|SIGSEGV|解引用)", claim, re.IGNORECASE)
        )
        submitted_count_drift_ignored = has_submitted_retry_without_count_drift and bool(
            re.search(r"SUBMITTED", claim, re.IGNORECASE)
            and re.search(
                r"(?:永久|永远|无法恢复|deadlock|lock|stuck|never).{0,100}"
                r"(?:ring_buff_count|sub_desc_cnt|计数|队列)|"
                r"(?:ring_buff_count|sub_desc_cnt|计数|队列).{0,100}"
                r"(?:永久|永远|无法恢复|deadlock|lock|stuck|never|不一致|mismatch)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        empty_ring_loop_misread = has_lockstep_event_count and bool(
            re.search(
                r"(?:empty\s+ring|ring\s+is\s+empty|空\s*ring|空队列|"
                r"ring_buff_count\s*(?:is|==|为|等于)?\s*0)",
                claim,
                re.IGNORECASE,
            )
            and re.search(
                r"(?:assert|断言|underflow|下溢|UINT_MAX|4294967295)",
                claim,
                re.IGNORECASE,
            )
        )
        unmap_return_misread = has_unmap_return_propagation and bool(
            re.search(r"ae4dma_unmap_pci_bar|BAR\s+unmap", claim, re.IGNORECASE)
            and re.search(
                r"(?:always|总是|始终).{0,50}(?:return|返回|success|成功).{0,30}0|"
                r"(?:return|返回).{0,20}0.{0,50}(?:unconditionally|无条件|总是|始终)|"
                r"returns?\s+0\s*\(success\)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        partial_callback_misread = has_callback_assignment_after_iteration and bool(
            re.search(r"vtophys|partial.{0,30}(?:batch|descriptor)|部分.{0,30}(?:批次|描述符)", claim, re.IGNORECASE)
            and re.search(
                r"callback.{0,80}(?:attach|attached|invoke|invoked|stale|调用|挂接|残留)|"
                r"(?:attach|attached|invoke|invoked|stale|调用|挂接|残留).{0,80}callback",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        wrong_final_callback_misread = has_callback_assignment_after_iteration and bool(
            (
                re.search(r"cb_desc", identified_claim)
                and re.search(r"last_desc", identified_claim)
                and re.search(
                    r"(?:错误|wrong).{0,30}(?:desc(?:riptor)?|描述符|callback|回调)",
                    identified_claim,
                    re.IGNORECASE,
                )
            )
            or re.search(
                r"callback.{0,40}wrong.{0,20}desc(?:riptor)?|"
                r"wrong.{0,20}desc(?:riptor)?.{0,40}callback",
                identified_claim,
                re.IGNORECASE,
            )
        )
        queue_count_inversion_misread = has_queue_count_passthrough and bool(
            re.search(r"queue|队列", claim, re.IGNORECASE)
            and re.search(
                r"(?:invert|inverted|反置|反转|颠倒)|"
                r"(?:always|始终|总是).{0,40}(?:16|maximum|最大)|"
                r"(?:<=|小于等于).{0,20}16.{0,40}(?:true|真)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        attach_cleanup_misread = (
            has_start_failure_cleanup
            and not proven_unmap_return_loss
            and bool(
                re.search(r"attach.{0,80}(?:fail|失败)|(?:fail|失败).{0,80}attach", claim, re.IGNORECASE)
                and re.search(
                    r"(?:no|without|没有|无).{0,30}(?:cleanup|清理)|"
                    r"(?:cleanup|清理).{0,30}(?:missing|缺失|不存在)",
                    claim,
                    re.IGNORECASE,
                )
            )
        )
        if (
            guarded_current_enqueue
            or guarded_invalid_address
            or guarded_ring_overwrite
            or expected_zero_length_guard
            or skipped_error_callback
            or callback_already_propagates_error
            or hypothetical_unchecked_return
            or cleanup_ignored
            or lockstep_assert_ignored
            or assigned_vtophys_size_ignored
            or guarded_callback_ignored
            or submitted_count_drift_ignored
            or empty_ring_loop_misread
            or unmap_return_misread
            or partial_callback_misread
            or wrong_final_callback_misread
            or queue_count_inversion_misread
            or attach_cleanup_misread
        ):
            invalid.append(artifact_id)
    return invalid


def _known_c_review_misread_finding_ids(task: WorkerTask, findings) -> list[str]:
    """Identify review findings whose stated ordering or cleanup contradicts source."""

    sources = _task_c_sources(task)
    has_guarded_enqueue = any(
        re.search(
            r"ring_buff_count.*?return\s+1\s*;.*?ae4dma_prep_copy\s*\(",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_start_failure_cleanup = any(
        re.search(
            r"if\s*\(\s*ae4dma_channel_start\s*\([^;]+?!=\s*0\s*\)\s*\{.*?"
            r"ae4dma_channel_destruct\s*\([^;]+;.*?free\s*\(\s*ae4dma\s*\)",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_lockstep_event_count = any(
        re.search(
            r"sub_desc_cnt\s*=\s*cmd_q->ring_buff_count\s*;.*?"
            r"while\s*\(\s*sub_desc_cnt\s*\).*?"
            r"assert\s*\(\s*cmd_q->ring_buff_count\s*>\s*0\s*\)\s*;.*?"
            r"cmd_q->ring_buff_count--\s*;.*?sub_desc_cnt--\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_vtophys_size_assignment = any(
        re.search(
            r"size\s*=\s*cmd_q->queue_size\s*;.*?"
            r"spdk_vtophys\s*\(\s*cmd_q->qbase_addr\s*,\s*&size\s*\)",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_submitted_retry_without_count_drift = any(
        re.search(
            r"if\s*\(\s*desc_status\s*==\s*AE4DMA_DMA_DESC_SUBMITTED\s*\)\s*\{?\s*"
            r"break\s*;.*?ring_buff_count--\s*;.*?sub_desc_cnt--\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_void_mmio_write = any(
        re.search(r"static\s+inline\s+void\s+spdk_mmio_write_4\s*\(", source)
        for source in sources
    )
    has_bar_zero_equivalence = (
        any(re.search(r"#define\s+AE4DMA_PCIE_BAR\s+0\b", source) for source in sources)
        and any(
            re.search(r"spdk_pci_device_unmap_bar\s*\([^,]+,\s*0\s*,", source)
            for source in sources
        )
    )
    has_terminal_error_callback = any(
        re.search(
            r"desc_status\s*!=\s*AE4DMA_DMA_DESC_COMPLETED.*?"
            r"desc_err_code\s*=.*?ring_buff_count--\s*;.*?callback_fn.*?"
            r"desc_err_code.*?tail\s*=\s*\(\s*tail\s*\+\s*1\s*\)",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_async_flush_contract = any(
        re.search(
            r"Flush previously built descriptors.*?"
            r"flush(?:es)? the descriptor to hardware for further processing.*?"
            r"void\s+spdk_ae4dma_flush",
            source,
            re.IGNORECASE | re.DOTALL,
        )
        for source in sources
    )
    has_internal_map_status = any(
        re.search(
            r"static\s+int\s+ae4dma_map_pci_bar.*?return\s+-1\s*;",
            source,
            re.DOTALL,
        )
        and re.search(
            r"if\s*\(\s*ae4dma_channel_start\s*\([^;]+?!=\s*0\s*\)",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_matching_ring_full_threshold = (
        any(
            re.search(
                r"ae4dma_desc_cmdq_full.*?count\s*>=\s*"
                r"\(\s*AE4DMA_DESCRIPTORS_PER_CMDQ\s*-\s*4\s*\)",
                source,
                re.DOTALL,
            )
            for source in sources
        )
        and any(
            re.search(
                r"ring_buff_count\s*>=\s*"
                r"\(\s*AE4DMA_DESCRIPTORS_PER_CMDQ\s*-\s*4\s*\)",
                source,
                re.DOTALL,
            )
            for source in sources
        )
    )
    has_success_only_final_callback = any(
        re.search(
            r"cb_desc\s*=\s*ae4dma_prep_copy\s*\([^;]+;.*?"
            r"if\s*\(\s*!cb_desc\s*\)\s*\{.*?return\s+-ENOMEM\s*;.*?"
            r"last_desc\s*=\s*cb_desc\s*;.*?"
            r"if\s*\(\s*last_desc\s*\)\s*\{.*?cb_desc->callback_fn\s*=",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_queue_count_passthrough = any(
        re.search(
            r"ae4dma_config_queues_per_device\s*\([^)]*\).*?"
            r"num_hw_queues\s*<=\s*AE4DMA_MAX_HW_QUEUES.*?return\s+false\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    ) and any(
        re.search(
            r"if\s*\(\s*!ae4dma_config_queues_per_device\s*\(\s*hw_queues\s*\)\s*\)"
            r"\s*\{.*?q_per_eng\s*=\s*hw_queues\s*;.*?else\s*\{.*?"
            r"q_per_eng\s*=\s*AE4DMA_MAX_HW_QUEUES\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    invalid: list[str] = []
    for finding in findings:
        claim = "\n".join((finding.check_id or "", finding.finding, *finding.evidence))
        order_misread = has_guarded_enqueue and bool(re.search(
            r"(?:检查|check).{0,80}(?:在|is).{0,30}(?:递增|increment).{0,30}(?:之后|after)|"
            r"(?:递增|increment).{0,80}(?:之后|after).{0,80}(?:检查|check)",
            claim,
            re.IGNORECASE | re.DOTALL,
        ))
        cleanup_misread = has_start_failure_cleanup and bool(
            re.search(r"qbase_addr", claim, re.IGNORECASE)
            and re.search(r"(?:ring|calloc|分配).{0,120}(?:fail|失败)", claim, re.IGNORECASE | re.DOTALL)
            and re.search(r"(?:leak|泄漏|未释放|not\s+free)", claim, re.IGNORECASE)
        )
        lockstep_misread = has_lockstep_event_count and bool(
            re.search(r"(?:assert|断言)", claim, re.IGNORECASE)
            and re.search(r"(?:未|无|not|without).{0,40}(?:检查|guard|check)", claim, re.IGNORECASE)
        )
        size_misread = has_vtophys_size_assignment and bool(
            re.search(r"spdk_vtophys|vtophys", claim, re.IGNORECASE)
            and re.search(r"(?:size|长度)", claim, re.IGNORECASE)
            and re.search(
                r"(?:未赋值|未初始化|错误|需确认|uninitialized|not\s+(?:set|initialized)|incorrect|wrong)",
                claim,
                re.IGNORECASE,
            )
        )
        submitted_stop_misread = has_submitted_retry_without_count_drift and bool(
            re.search(r"SUBMITTED", claim, re.IGNORECASE)
            and re.search(r"(?:永久|永远|停止|stuck|never|permanent)", claim, re.IGNORECASE)
        )
        mmio_failure_misread = has_void_mmio_write and bool(
            re.search(r"MMIO|qbase_(?:lo|hi)", claim, re.IGNORECASE)
            and re.search(r"(?:写|write).{0,30}(?:失败|fail)|(?:失败|fail).{0,30}(?:写|write)", claim, re.IGNORECASE)
        )
        bar_constant_misread = has_bar_zero_equivalence and bool(
            re.search(r"(?:hardcod|硬编码).{0,80}(?:BAR|0)|(?:BAR|0).{0,80}(?:hardcod|硬编码)", claim, re.IGNORECASE)
        )
        terminal_error_misread = has_terminal_error_callback and bool(
            re.search(r"ERROR", claim, re.IGNORECASE)
            and re.search(r"(?:推进|advance).{0,80}(?:混淆|错误|confus)|(?:处理不明确|unclear)", claim, re.IGNORECASE)
        )
        lost_error_channel_misread = has_terminal_error_callback and bool(
            re.search(r"(?:error|错误|异常)", claim, re.IGNORECASE)
            and re.search(
                r"(?:只|仅).{0,20}(?:日志|log)|"
                r"(?:不|未|无法|没有|not|never|without).{0,40}(?:传播|感知|propagat|observe)|"
                r"(?:caller|调用方).{0,40}(?:无法|不能|cannot).{0,20}(?:感知|observe)|"
                r"(?:lost|丢失)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
            and not re.search(
                r"callback.{0,100}(?:err_code|error\s+code|错误码)|"
                r"(?:err_code|error\s+code|错误码).{0,100}callback",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        async_flush_misread = has_async_flush_contract and bool(
            re.search(r"spdk_ae4dma_flush|flush", claim, re.IGNORECASE)
            and re.search(r"(?:不等待|does\s+not\s+wait|未等待).{0,80}(?:完成|completion)", claim, re.IGNORECASE)
        )
        attach_cleanup_misread = has_start_failure_cleanup and bool(
            re.search(r"attach.{0,80}(?:失败|fail)|(?:失败|fail).{0,80}attach", claim, re.IGNORECASE)
            and re.search(r"(?:无|没有|缺少|without|no).{0,30}(?:清理|cleanup)", claim, re.IGNORECASE)
        )
        partial_count_misread = has_start_failure_cleanup and bool(
            re.search(r"cmd_q_count", claim, re.IGNORECASE)
            and re.search(r"(?:部分|partial).{0,80}(?:失败|fail|初始化)|(?:失败|fail).{0,80}(?:计数|count)", claim, re.IGNORECASE)
        )
        internal_errno_style_misread = has_internal_map_status and bool(
            re.search(r"ae4dma_map_pci_bar", claim, re.IGNORECASE)
            and re.search(r"-1", claim)
            and re.search(r"errno|约定|convention", claim, re.IGNORECASE)
        )
        public_api_shape_misread = has_async_flush_contract and bool(
            re.search(r"(?:公共|public).{0,30}API|API", claim, re.IGNORECASE)
            and re.search(r"(?:不一致|inconsistent)", claim, re.IGNORECASE)
            and re.search(r"(?:void|-1|errno)", claim, re.IGNORECASE)
            and not _finding_records_positive_ring_contract_violation(finding)
        )
        ring_threshold_misread = has_matching_ring_full_threshold and bool(
            re.search(r"ring", claim, re.IGNORECASE)
            and re.search(
                r">\s*(?:vs|与|而)\s*>=|>=\s*(?:vs|与|而)\s*>|"
                r"(?:阈值|boundary).{0,50}(?:不一致|inconsistent)|"
                r"(?:允许|allow).{0,30}29.{0,30}(?:descriptor|描述符)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        partial_callback_misread = has_guarded_enqueue and bool(
            re.search(r"vtophys", claim, re.IGNORECASE)
            and re.search(r"callback|回调", claim, re.IGNORECASE)
            and re.search(
                r"(?:已|仍|already|still).{0,30}(?:挂|带|attach|carry|trigger|触发)|"
                r"(?:callback|回调).{0,50}(?:残留|触发|remain|invoke)",
                claim,
                re.IGNORECASE | re.DOTALL,
            )
        )
        wrong_final_callback_misread = has_success_only_final_callback and bool(
            re.search(r"cb_desc", claim)
            and re.search(r"last_desc", claim)
            and re.search(
                r"(?:错误|wrong).{0,30}(?:desc(?:riptor)?|描述符|callback|回调)",
                claim,
                re.IGNORECASE,
            )
        )
        queue_count_inversion_misread = has_queue_count_passthrough and bool(
            re.search(r"queue|队列|q_per_eng", claim, re.IGNORECASE)
            and re.search(r"(?:反置|反转|颠倒|inverted|矛盾)", claim, re.IGNORECASE)
        )
        if (
            order_misread
            or cleanup_misread
            or lockstep_misread
            or size_misread
            or submitted_stop_misread
            or mmio_failure_misread
            or bar_constant_misread
            or terminal_error_misread
            or lost_error_channel_misread
            or async_flush_misread
            or attach_cleanup_misread
            or partial_count_misread
            or internal_errno_style_misread
            or public_api_shape_misread
            or ring_threshold_misread
            or partial_callback_misread
            or wrong_final_callback_misread
            or queue_count_inversion_misread
        ):
            invalid.append(finding.check_id or "")
    return invalid


def _has_lost_unmap_result(task: WorkerTask) -> bool:
    sources = _task_c_sources(task)
    return any(
        re.search(
            r"ae4dma_unmap_pci_bar\s*\([^)]*\)\s*;.*?"
            r"spdk_free\s*\([^;]*qbase_addr.*?free\s*\([^;]*ring",
            source,
            re.DOTALL,
        )
        and re.search(
            r"spdk_ae4dma_detach\s*\([^)]*\).*?"
            r"ae4dma_channel_destruct\s*\([^;]+;.*?free\s*\(\s*ae4dma\s*\)",
            source,
            re.DOTALL,
        )
        for source in sources
    )


def _known_c_unmap_finding_ids(task: WorkerTask, findings) -> list[str]:
    """Keep a proven detach-time unmap failure from being dismissed as process-exit cleanup."""

    if not _has_lost_unmap_result(task):
        return []
    return [
        finding.check_id or ""
        for finding in findings
        if re.search(r"unmap", finding.finding, re.IGNORECASE)
        and re.search(
            r"(?:返回值|return|rc).{0,100}(?:未检查|忽略|unchecked|ignored)|"
            r"(?:未检查|忽略|unchecked|ignored).{0,100}(?:返回值|return|rc)",
            finding.finding,
            re.IGNORECASE | re.DOTALL,
        )
    ]


def _has_positive_ring_full_contract_violation(task: WorkerTask) -> bool:
    sources = _task_c_sources(task)
    has_positive_ring_full = any(
        re.search(
            r"ring_buff_count.*?(?:AE4DMA_DESCRIPTORS_PER_CMDQ|RING_LIMIT).*?"
            r"return\s+1\s*;",
            source,
            re.DOTALL,
        )
        for source in sources
    )
    has_negative_errno_contract = any(
        re.search(r"spdk_ae4dma_build_copy", source, re.IGNORECASE)
        and re.search(
            r"return\s+0\s+on\s+success,\s*negative\s+errno\s+on\s+failure",
            source,
            re.IGNORECASE,
        )
        for source in sources
    )
    return has_positive_ring_full and has_negative_errno_contract


def _claims_positive_ring_contract_violation(claim: str) -> bool:
    return bool(
        re.search(r"ring.{0,40}(?:full|满)", claim, re.IGNORECASE)
        and re.search(r"(?:return|返回).{0,20}(?:positive|正数)?\s*1\b", claim, re.IGNORECASE)
        and re.search(r"negative\s+errno|负\s*errno|contract|契约|约定", claim, re.IGNORECASE)
    )


def _finding_records_positive_ring_contract_violation(finding) -> bool:
    claim = "\n".join((finding.finding, *finding.evidence))
    if _claims_positive_ring_contract_violation(claim):
        return True
    check_id = finding.check_id or ""
    return bool(
        re.search(r"return.*contract|contract.*return", check_id, re.IGNORECASE)
        and re.search(r"ring|AE4DMA", "\n".join((check_id, claim)), re.IGNORECASE)
    )


def _validate_known_c_required_failure_paths(task: WorkerTask, result: WorkerResult) -> None:
    """Require directly proven contract failures to survive the source checkpoint."""

    claims = [
        (path.disposition, "\n".join((
            path.path_id,
            path.trigger,
            path.side_effects,
            path.failure,
            path.caller_handling,
            path.final_states,
        )))
        for path in result.analysis_checkpoint.failure_paths
    ]
    ring_contract_recorded = any(
        disposition in {"risk", "unresolved"}
        and _claims_positive_ring_contract_violation(claim)
        for disposition, claim in claims
    )
    if _has_positive_ring_full_contract_violation(task) and not ring_contract_recorded:
        raise ArtifactRejected(
            "源码 checkpoint 不得把 ring-full 分支整体视为正常保护：保护动作本身有效，"
            "但 public API 明确约定失败返回 negative errno，而实现返回正数 1。"
            "必须保留一条 risk/unresolved failure path，区分容量保护与返回值契约违例"
        )

    unmap_loss_recorded = any(
        disposition in {"risk", "unresolved"}
        and re.search(r"unmap", claim, re.IGNORECASE)
        and re.search(
            r"(?:destruct|析构|detach).{0,100}(?:ignore|drop|忽略|丢弃|未检查).{0,60}"
            r"(?:return|返回值|rc)|"
            r"(?:return|返回值|rc).{0,60}(?:ignore|drop|忽略|丢弃|未检查)",
            claim,
            re.IGNORECASE | re.DOTALL,
        )
        for disposition, claim in claims
    )
    if _has_lost_unmap_result(task) and not unmap_loss_recorded:
        raise ArtifactRejected(
            "源码 checkpoint 必须保留 detach/destruct 丢弃非零 unmap 返回值的真实路径；"
            "helper 会 return rc，风险是调用方忽略该 rc 并释放重试句柄，不是 helper 总返回 0"
        )


def _validate_known_c_container_semantics(task: WorkerTask, result: WorkerResult) -> None:
    invalid = _known_c_container_semantic_artifact_ids(task, result)
    if invalid:
        raise ArtifactRejected(
            "TAILQ_REMOVE 不会把未链入元素当作静默 no-op；"
            "该输入的链指针无效，不得写成成功且无副作用："
            f"{sorted(invalid)}"
        )
    retained_realloc_errors = _known_retained_realloc_artifact_ids(task, result)
    if retained_realloc_errors:
        raise ArtifactRejected(
            "realloc 结果先写入临时指针、失败分支保留旧指针且函数随后返回旧指针时，"
            "不得把该失败路径写成旧分配泄漏；break 后的计数递增也不可达。"
            "可分析部分结果等实际后果："
            f"{sorted(retained_realloc_errors)}"
        )
    local_allocation_errors = _known_local_allocation_concurrency_artifact_ids(task, result)
    if local_allocation_errors:
        raise ArtifactRejected(
            "函数每次调用内部独立 calloc/malloc 的局部缓冲区，不会因另一次调用"
            "重分配其同名局部变量而变成跨请求共享或悬空指针；必须先证明内存对象实际共享："
            f"{sorted(local_allocation_errors)}"
        )
    precheck_order_errors = _known_c_precheck_order_artifact_ids(task, result)
    if precheck_order_errors:
        raise ArtifactRejected(
            "不得把源码检查之前尚未发生的入队、覆盖或 callback 结果写进风险："
            "vtophys、零长度和 ring-full 分支均在当前失败段调用 ae4dma_prep_copy 前返回；"
            "可以保留此前成功段已入队且无法回滚的实际后果，但必须明确区分当前段；"
            "错误描述符会把 err_code 传给已注册 callback，该 callback 是异步错误通道；"
            "events_count 只计处理数量、单独不含错误码不是缺陷，除非现行契约明确要求返回错误；"
            "ring-full 保护本身不会让重试覆盖未消费描述符，正常 -EINVAL 防护也不是风险；"
            "channel_start 失败会由 ae4dma_attach 调用 destruct 回收已分配队列；"
            "事件循环的本地计数与 ring_buff_count 锁步递减，size 也在 vtophys 前赋值；"
            "也不得用未提供真实调用点的“调用方若不检查 NULL”构造缺陷："
            f"{sorted(precheck_order_errors)}"
        )
    _validate_known_c_required_failure_paths(task, result)


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
    if not checkpoint.lifecycle_stages_checked:
        raise ArtifactRejected("当前阶段必须填写 lifecycle_stages_checked")
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
        _validate_known_c_container_semantics(task, result)
        return

    if expected_stage == "risk_analysis":
        if not result.evidence or not result.business_flows:
            raise ArtifactRejected("风险分析必须包含真实证据和业务流程")
        if not checkpoint.risk_set_frozen:
            raise ArtifactRejected("风险分析尚未冻结风险集合")
        if result.test_cases:
            raise ArtifactRejected("风险分析阶段不能提前生成测试用例")
        _validate_risk_source_scope(task, result)
        _validate_semantic_check_closure(task, result)
        _validate_known_c_container_semantics(task, result)
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
    _validate_risk_source_scope(task, result)
    _validate_semantic_check_closure(task, result)
    _validate_known_c_container_semantics(task, result)
    _validate_test_basis_closure(task, result)
    _require_confirmed_evidence(task, result)
    _validate_visual_findings(task, result)
    if task.task_type == "rework":
        expected_issues = {issue.issue_id for issue in task.review_issues}
        if set(result.addressed_review_issue_ids) != expected_issues:
            raise ArtifactRejected("返工结果未逐项回应 review issue")
        unmap_issues = [
            issue.issue_id
            for issue in task.review_issues
            if re.search(r"unmap", f"{issue.reason}\n{issue.required_change}", re.IGNORECASE)
            and re.search(
                r"(?:返回值|return|rc).{0,100}(?:未检查|忽略|unchecked|ignored)|"
                r"(?:未检查|忽略|unchecked|ignored).{0,100}(?:返回值|return|rc)",
                f"{issue.reason}\n{issue.required_change}",
                re.IGNORECASE | re.DOTALL,
            )
        ]
        if unmap_issues and _has_lost_unmap_result(task):
            unmap_risks = [
                risk.risk_id
                for risk in result.risks
                if re.search(
                    r"unmap",
                    "\n".join((
                        risk.title,
                        risk.trigger,
                        risk.system_result,
                        risk.external_observation,
                    )),
                    re.IGNORECASE,
                )
            ]
            if not unmap_risks:
                raise ArtifactRejected(
                    "返工 issue 已确认 detach 丢弃 unmap 返回值；"
                    "仅记录 addressed_review_issue_ids 不能替代 RiskCard 与测试闭环："
                    f"issues={sorted(unmap_issues)}"
                )


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
    worker_tasks_by_unit: dict[str, WorkerTask] = {}
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
        worker_tasks_by_unit[task_ref.unit_id] = worker_task
    missing_ring_contract_units = [
        unit_id
        for unit_id, worker_task in worker_tasks_by_unit.items()
        if _has_positive_ring_full_contract_violation(worker_task)
        and not any(
            finding.unit_id == unit_id
            and finding.worker_disposition != "reasonably_excluded"
            and _finding_records_positive_ring_contract_violation(finding)
            for finding in result.independent_findings
        )
    ]
    if missing_ring_contract_units:
        raise ArtifactRejected(
            "comparison review 遗漏源码可直接证明的 public API 返回值契约违例："
            "ring-full 容量保护返回正数 1，但接口约定失败返回 negative errno。"
            "请新增 COMPARISON- finding 并进入 worker 返工，不要把容量保护本身误判为缺陷："
            f"{sorted(missing_ring_contract_units)}"
        )
    known_c_review_misreads: list[tuple[str, str]] = []
    for unit_id, worker_task in worker_tasks_by_unit.items():
        unit_findings = [
            finding for finding in result.independent_findings
            if finding.unit_id == unit_id
        ]
        misread_ids = set(_known_c_review_misread_finding_ids(worker_task, unit_findings))
        known_c_review_misreads.extend(
            (unit_id, finding.check_id or "")
            for finding in unit_findings
            if (finding.check_id or "") in misread_ids
            and finding.worker_disposition != "reasonably_excluded"
        )
    if known_c_review_misreads:
        raise ArtifactRejected(
            "独立复核结论与冻结源码的检查顺序、失败清理或参数赋值直接矛盾；"
            "comparison/rework verification 的 worker_disposition 只允许 reasonably_excluded；"
            "missing、covered、contradiction 都仍会失败。"
            "这条要求只适用于末尾列出的 finding，不要修改其他 finding。"
            "不得派发或维持 worker 返工："
            f"{sorted(known_c_review_misreads)}"
        )
    wrongly_excluded_unmap_findings: list[tuple[str, str]] = []
    for unit_id, worker_task in worker_tasks_by_unit.items():
        unit_findings = [
            finding for finding in result.independent_findings
            if finding.unit_id == unit_id
        ]
        unmap_ids = set(_known_c_unmap_finding_ids(worker_task, unit_findings))
        wrongly_excluded_unmap_findings.extend(
            (unit_id, finding.check_id or "")
            for finding in unit_findings
            if (finding.check_id or "") in unmap_ids
            and finding.worker_disposition == "reasonably_excluded"
        )
    if wrongly_excluded_unmap_findings:
        raise ArtifactRejected(
            "detach 在进程运行期丢弃 unmap 返回值后继续释放 channel，失败映射会残留且失去重试句柄；"
            "不得仅以进程退出时内核最终清理为由 reasonably_excluded："
            f"{sorted(wrongly_excluded_unmap_findings)}"
        )
    retained_realloc_findings = sorted(
        (finding.unit_id, finding.check_id or "")
        for finding in result.independent_findings
        if finding.unit_id in worker_tasks_by_unit
        and _retained_realloc_source_paths(worker_tasks_by_unit[finding.unit_id])
        and _claims_failed_realloc_leaks(finding.finding)
        and finding.worker_disposition != "reasonably_excluded"
    )
    if retained_realloc_findings:
        raise ArtifactRejected(
            "保留并最终返回旧指针的 realloc 失败路径不是旧分配泄漏；"
            "comparison review 必须将该独立误判记录为 reasonably_excluded，"
            "不得派发 worker 返工："
            f"{retained_realloc_findings}"
        )
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
    non_actionable_issues = _non_actionable_review_issue_ids(result.issues)
    if non_actionable_issues:
        raise ArtifactRejected(
            "review issue 的 required_change 必须是可直接验证的确定动作，"
            "不得只写考虑、建议或可能修改："
            f"{non_actionable_issues}"
        )
    reviewer_owned_issues = _reviewer_owned_field_issue_ids(result.issues)
    if reviewer_owned_issues:
        raise ArtifactRejected(
            "review issue 不得要求 worker 修改 reviewer 自己的 test_case_checks 字段："
            f"{reviewer_owned_issues}"
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
        if len(compared) != len(result.independent_findings):
            raise ArtifactRejected("复核 finding 的 check_id 必须存在且不得重复")
        missing_original = set(original) - set(compared)
        if missing_original:
            raise ArtifactRejected(
                f"复核必须逐项保留独立复核 findings：missing={sorted(missing_original)}"
            )
        comparison_only = set(compared) - set(original)
        invalid_comparison_ids = sorted(
            key for key in comparison_only
            if not key[1].startswith("COMPARISON-")
        )
        if invalid_comparison_ids:
            raise ArtifactRejected(
                "对照阶段补充的 finding 必须使用 COMPARISON- 前缀，"
                "以免冒充独立复核发现："
                f"{invalid_comparison_ids}"
            )
        rewritten_originals = sorted(
            key
            for key, finding in original.items()
            if compared[key].finding != finding.finding
            or compared[key].evidence != finding.evidence
        )
        if rewritten_originals:
            raise ArtifactRejected(
                "复核不能改写独立复核结论或证据。请重新读取 review-independent.json，"
                "将下列 finding 与 evidence 原样复制回 independent_findings；"
                "原 finding 只允许调整 worker_disposition。对照阶段的新发现另用 COMPARISON- 前缀："
                f"{rewritten_originals}"
            )

        if task.stage == "comparison_review":
            excluded_finding_issues = sorted(
                issue.issue_id
                for issue in result.issues
                if any(
                    finding.unit_id == issue.unit_id
                    and finding.worker_disposition == "reasonably_excluded"
                    and finding.check_id in f"{issue.reason}\n{issue.required_change}"
                    for finding in result.independent_findings
                )
            )
            if excluded_finding_issues:
                raise ArtifactRejected(
                    "reasonably_excluded finding 已由当前 Reviewer 确认不应进入 Worker 返工；"
                    "请删除仍引用这些 check_id 的 issue，不要改回 missing/contradiction："
                    f"{excluded_finding_issues}"
                )
            unassigned_blockers = sorted(
                (finding.unit_id, finding.check_id)
                for finding in result.independent_findings
                if finding.worker_disposition in {"missing", "contradiction"}
                and not any(
                    issue.unit_id == finding.unit_id
                    and finding.check_id in f"{issue.reason}\n{issue.required_change}"
                    for issue in result.issues
                )
            )
            if unassigned_blockers:
                raise ArtifactRejected(
                    "comparison_review 中每条 missing/contradiction finding 都必须由同单元 "
                    "issue 明确引用 check_id，确保返工任务不会漏项："
                    f"{unassigned_blockers}"
                )

        worker_risks: set[tuple[str, str]] = set()
        worker_tests: set[tuple[str, str]] = set()
        worker_test_expectations: dict[tuple[str, str], list[str]] = {}
        worker_test_failures: dict[tuple[str, str], list[str | None]] = {}
        worker_risk_objects: dict[tuple[str, str], object] = {}
        risks_without_confirmed_evidence: list[tuple[str, str]] = []
        known_container_semantic_errors: list[tuple[str, str]] = []
        known_retained_realloc_errors: list[tuple[str, str]] = []
        known_local_allocation_errors: list[tuple[str, str]] = []
        known_precheck_order_errors: list[tuple[str, str]] = []
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
            worker_risk_objects.update({
                (result_ref.unit_id, risk.risk_id): risk
                for risk in worker_result.risks
            })
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
            known_container_semantic_errors.extend(
                (result_ref.unit_id, artifact_id)
                for artifact_id in _known_c_container_semantic_artifact_ids(
                    worker_tasks_by_unit[result_ref.unit_id],
                    worker_result,
                )
            )
            known_retained_realloc_errors.extend(
                (result_ref.unit_id, artifact_id)
                for artifact_id in _known_retained_realloc_artifact_ids(
                    worker_tasks_by_unit[result_ref.unit_id],
                    worker_result,
                )
            )
            known_local_allocation_errors.extend(
                (result_ref.unit_id, artifact_id)
                for artifact_id in _known_local_allocation_concurrency_artifact_ids(
                    worker_tasks_by_unit[result_ref.unit_id],
                    worker_result,
                )
            )
            known_precheck_order_errors.extend(
                (result_ref.unit_id, artifact_id)
                for artifact_id in _known_c_precheck_order_artifact_ids(
                    worker_tasks_by_unit[result_ref.unit_id],
                    worker_result,
                )
            )

        known_finding_link_errors: list[tuple[str, str, list[str]]] = []
        for finding in result.independent_findings:
            finding_claim = "\n".join((
                finding.check_id or "",
                finding.finding,
                *finding.evidence,
            ))
            matching_risk_ids: list[str] = []
            if _finding_records_positive_ring_contract_violation(finding):
                matching_risk_ids = sorted(
                    risk_id
                    for (unit_id, risk_id), risk in worker_risk_objects.items()
                    if unit_id == finding.unit_id
                    and _claims_positive_ring_contract_violation("\n".join((
                        risk.risk_id,
                        risk.title,
                        risk.trigger,
                        risk.system_result,
                        risk.external_observation,
                        risk.exclusion_condition,
                    )))
                )
            elif (
                re.search(r"unmap", finding_claim, re.IGNORECASE)
                and re.search(
                    r"(?:返回值|return|rc).{0,100}(?:未检查|忽略|unchecked|ignored)|"
                    r"(?:未检查|忽略|unchecked|ignored).{0,100}(?:返回值|return|rc)",
                    finding_claim,
                    re.IGNORECASE | re.DOTALL,
                )
            ):
                matching_risk_ids = sorted(
                    risk_id
                    for (unit_id, risk_id), risk in worker_risk_objects.items()
                    if unit_id == finding.unit_id
                    and re.search(r"unmap", "\n".join((risk.risk_id, risk.title, risk.trigger, risk.system_result)), re.IGNORECASE)
                    and re.search(
                        r"(?:返回值|return|rc).{0,100}(?:未检查|忽略|丢弃|unchecked|ignored|discard)|"
                        r"(?:未检查|忽略|丢弃|unchecked|ignored|discard).{0,100}(?:返回值|return|rc)",
                        "\n".join((risk.risk_id, risk.title, risk.trigger, risk.system_result)),
                        re.IGNORECASE | re.DOTALL,
                    )
                )
            if not matching_risk_ids:
                continue
            if (
                finding.worker_disposition != "covered"
                or not set(matching_risk_ids).intersection(finding.linked_worker_risk_ids)
            ):
                known_finding_link_errors.append((
                    finding.unit_id,
                    finding.check_id or "",
                    matching_risk_ids,
                ))
        if known_finding_link_errors:
            raise ArtifactRejected(
                "源码已证明的 ring-full 返回值契约或 unmap 返回值丢失风险已经存在于 Worker RiskCard；"
                "对应 finding 必须标记 covered，并在 linked_worker_risk_ids 链接末尾给出的已有 risk。"
                "不要新增 issue、COMPARISON finding 或 worker 返工："
                f"{known_finding_link_errors}"
            )

        unassigned_container_errors = sorted(
            key
            for key in known_container_semantic_errors
            if not any(
                issue.unit_id == key[0]
                and key[1] in f"{issue.reason}\n{issue.required_change}"
                for issue in result.issues
            )
        )
        if unassigned_container_errors:
            raise ArtifactRejected(
                "Worker 对 C/C++ 容器宏的已知语义结论错误，review issue 必须"
                "明确点名对应 risk/test 以便返工："
                f"{unassigned_container_errors}"
            )

        unassigned_realloc_errors = sorted(
            key
            for key in known_retained_realloc_errors
            if not any(
                issue.unit_id == key[0]
                and key[1] in f"{issue.reason}\n{issue.required_change}"
                for issue in result.issues
            )
        )
        if unassigned_realloc_errors:
            raise ArtifactRejected(
                "Worker 把保留并最终返回旧指针的 realloc 失败路径误判为内存泄漏，"
                "review issue 必须明确点名对应 risk/test，PASS 前必须移除："
                f"{unassigned_realloc_errors}"
            )

        unassigned_local_allocation_errors = sorted(
            key
            for key in known_local_allocation_errors
            if not any(
                issue.unit_id == key[0]
                and key[1] in f"{issue.reason}\n{issue.required_change}"
                for issue in result.issues
            )
        )
        if unassigned_local_allocation_errors:
            raise ArtifactRejected(
                "Worker 把每次调用独立分配的局部缓冲区误判为跨请求共享内存，"
                "review issue 必须明确点名对应 risk/test，PASS 前必须移除："
                f"{unassigned_local_allocation_errors}"
            )

        unassigned_precheck_order_errors = sorted(
            key
            for key in known_precheck_order_errors
            if not any(
                issue.unit_id == key[0]
                and key[1] in f"{issue.reason}\n{issue.required_change}"
                for issue in result.issues
            )
        )
        if unassigned_precheck_order_errors:
            raise ArtifactRejected(
                "Worker 对 C/C++ 提交前检查、ring-full 或 callback 错误传播的结论"
                "与源码顺序不符，review issue 必须明确点名对应 risk/test 以便返工："
                f"{unassigned_precheck_order_errors}"
            )

        unassigned_leak_contradictions: list[tuple[str, str, str]] = []
        for finding in result.independent_findings:
            if finding.worker_disposition != "covered":
                continue
            for risk_id in finding.linked_worker_risk_ids:
                risk = worker_risk_objects.get((finding.unit_id, risk_id))
                if risk is None:
                    continue
                risk_claim = "\n".join((
                    risk.title,
                    risk.trigger,
                    risk.system_result,
                    risk.external_observation,
                ))
                if not _finding_excludes_linked_leak(finding.finding, risk_claim):
                    continue
                if any(
                    issue.unit_id == finding.unit_id
                    and risk_id in f"{issue.reason}\n{issue.required_change}"
                    for issue in result.issues
                ):
                    continue
                unassigned_leak_contradictions.append((
                    finding.unit_id,
                    finding.check_id or "",
                    risk_id,
                ))
        if unassigned_leak_contradictions:
            raise ArtifactRejected(
                "independent finding 已明确记录无泄漏或资源全部释放，"
                "关联的 leak RiskCard 不得无 issue 地标记为 covered："
                f"{sorted(unassigned_leak_contradictions)}"
            )

        if result.status == "PASS" and risks_without_confirmed_evidence:
            raise ArtifactRejected(
                "PASS 的风险必须全部使用当前 Run 已确认的证据："
                f"{sorted(risks_without_confirmed_evidence)}"
            )

        stale_restoration_issues = _stale_artifact_restoration_issue_ids(
            result.issues,
            worker_risks,
            worker_tests,
        )
        if task.stage == "rework_verification" and stale_restoration_issues:
            raise ArtifactRejected(
                "返工验证不得要求恢复当前 Worker 结果已删除的旧 risk/test；"
                "Graph 骨架中的当前产物集合是本轮事实："
                f"{stale_restoration_issues}"
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
        else:
            unassigned_test_blockers = sorted(
                key
                for key, item in actual_test_checks.items()
                if item.verdict in {"invalid", "unresolved"}
                and not any(
                    issue.unit_id == key[0]
                    and key[1] in f"{issue.reason}\n{issue.required_change}"
                    for issue in result.issues
                )
            )
            if unassigned_test_blockers:
                raise ArtifactRejected(
                    "invalid/unresolved TestCase 必须由同单元 issue 明确点名，"
                    "否则返工 Worker 收不到该修复项："
                    f"{unassigned_test_blockers}"
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
                    "worker 保留同一路径且结论相反时必须标 contradiction 并生成 issue；"
                    "若 COMPARISON- finding 确认 Worker 如实保留了源码不确定性，"
                    "即使 RiskCard 的 upstream_semantics.conclusion=unresolved 也应标 covered"
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
                "linked_worker_test_case_ids；若现有独立 finding 不描述该路径，不得硬挂到"
                "不相关或 reasonably_excluded finding，须按 review-worker 规则追加一条"
                "有冻结源码证据的 COMPARISON- finding；"
                f"missing_risks={missing_risks}, missing_tests={missing_tests}"
            )


def validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)
