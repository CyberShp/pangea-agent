"""Report assembly for source-first notes.

The renderer presents the Agent's original records and workflow facts.  It
does not turn prose into risks, tests, or a quality verdict.
"""

from __future__ import annotations

import html
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json
from pangea_agent.graph.result_store import active_diagram_diagnostics, read_result, supersession_map
from pangea_agent.graph.workflow_store import run_directory
from pangea_agent.report.flow_section import flow_section_markdown
from pangea_agent.report.branch_coverage import coverage_body, coverage_markdown


def diagram_diagnostic_markdown(delivery: list[tuple[dict, Any]]) -> list[str]:
    """Publish current persisted format facts, with exact authored identities."""
    lines: list[str] = []
    for action, result in delivery:
        warnings = active_diagram_diagnostics(result)
        if not warnings:
            continue
        if not lines:
            lines.extend(["## 三图格式诊断", "", "以下提示保留已保存内容，不改变审核结论或运行状态。", ""])
        unit_id = str(action.get("task", {}).get("unit_id") or "unit")
        records = {record.record_id: record for record in result.records}
        for warning in warnings:
            record = records[warning["record_id"]]
            if record.kind != "flow":
                continue
            lines.extend([f"### `{unit_id}` · `{record.record_id}` · 修订 `{record.created_revision}`", "",
                          _body_text({key: warning.get(key) for key in (
                              "result_path", "action_id", "task_id", "record_id", "record_revision",
                              "diagram", "field", "code", "classification", "severity", "message")}), ""])
    return lines


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _body_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)


_CASE_LEVELS = {
    "business_blackbox": "业务黑盒",
    "interface_contract": "接口契约",
    "developer_assisted": "业务灰盒",
    "whitebox_support": "白盒辅助",
}

_CASE_PURPOSES = {
    "branch": "分支用例",
    "coverage": "未覆盖用例",
    "risk": "风险用例",
}

_FIELD_LABELS = {
    "evidence_quotes": "源码引文", "check_id": "检查编号", "lens": "检查镜头",
    "object": "检查对象", "hypothesis": "假设", "conclusion": "结论", "reasoning": "依据",
    "sites": "位置", "signal_ids": "关联信号", "risk_ids": "关联风险",
    "action_id": "Action",
    "analysis_profile": "分析模式",
    "associated_cases": "关联用例",
    "content": "说明",
    "coverage": "Coverage 说明",
    "current_behavior_evidence": "当前行为证据",
    "deterministic_consequence": "确定后果",
    "execution_status": "执行状态",
    "missing_to_resolve": "仍缺少的信息",
    "note_id": "说明编号",
    "recommended_action": "建议补充",
    "records_navigation": "记录导航",
    "risk_id": "问题编号",
    "risk_refs": "关联风险",
    "coverage_refs": "关联 Coverage",
    "flow_refs": "关联流程",
    "purpose": "测试目的",
    "scope_notes": "范围说明",
    "severity": "严重程度",
    "source_evidence": "源码证据",
    "test_levels": "用例层级说明",
    "unit": "分析单元",
    "unresolved_id": "待确认编号",
    "what_is_known": "已确认事实",
    "attach_to": "挂接位置",
    "case_ids": "对应用例",
    "condition": "条件",
    "description": "说明",
    "edges": "连线",
    "evidence": "证据",
    "expected": "预期",
    "explanation": "解释",
    "flow_id": "流程编号",
    "format_version": "格式版本",
    "kind": "类型",
    "label": "名称",
    "node_ids": "节点序列",
    "nodes": "节点",
    "path_id": "路径编号",
    "paths": "路径",
    "relates_to": "关联",
    "result": "结果",
    "role": "流程角色",
    "steps": "步骤",
    "text": "文字说明",
}

# Display vocabulary only. Never rewrite Agent prose or the stored decision.
# Keep these deterministic labels aligned with companion/review-presentation.js.
_REVIEW_FIELD_LABELS = {
    "disposition": "复核裁决", "verdict": "复核结果", "status": "状态", "quality_status": "质量状态",
    "summary": "复核摘要", "conclusion": "结论", "decision": "裁决说明", "decisions": "对照裁决", "reason": "原因",
    "rationale": "裁决依据", "reasoning": "推导依据", "basis": "依据", "evidence": "证据",
    "source_evidence": "源码依据", "evidence_quotes": "源码引文", "ref": "位置", "snippet": "原样引文",
    "what_is_known": "已确认事实", "missing_to_resolve": "仍缺少的信息",
    "open_issues": "未解决事项", "unresolved": "待确认事项", "unresolved_items": "待确认事项",
    "unresolved_reasons": "待确认原因", "limitations": "限制与缺口", "remaining_gaps": "剩余缺口",
    "findings": "复核发现", "issues": "复核问题", "required_change": "要求修改",
    "required_changes": "要求修改", "recommendation": "建议", "recommended_action": "建议操作",
    "corrections": "定向修正", "correction_record_ids": "需修正的记录",
    "finding_dispositions": "问题处置", "finding_record_id": "复核发现编号",
    "finding_key": "发现标识", "finding_id": "发现编号", "issue_id": "问题编号",
    "updated_record_ids": "已更新记录", "affected_records": "受影响记录",
    "affected_record_ids": "受影响记录编号", "target_record_ids": "目标记录编号",
    "record_id": "记录编号", "record_ids": "记录编号", "case_id": "用例编号", "case_ids": "用例编号",
    "variant_id": "变体编号", "unit_id": "分析单元", "unit": "分析单元",
    "action_id": "阶段动作编号", "task_id": "任务编号", "version_set_id": "本次对照版本", "format_version": "格式版本",
    "review_stage": "复核阶段", "review_mode": "复核模式", "review_status": "复核状态",
    "scope": "审查范围", "scope_notes": "范围说明", "checked_items": "已核对事项",
    "checks": "检查记录", "coverage": "覆盖说明", "coverage_summary": "覆盖说明",
    "title": "标题", "description": "说明", "content": "正文", "notes": "补充说明", "note": "补充说明",
    "text": "正文", "result": "结果", "expected": "预期", "actual": "实际结果",
    "original": "原结论", "original_conclusion": "原结论", "source_counterexample": "源码反证",
    "category": "问题类别", "severity": "严重程度", "related_record_ids": "关联记录",
    "counts": "数量汇总", "independent": "独立复核", "comparison": "对照复核",
    "confirmed": "已确认", "dismissed": "已驳回", "effective": "有效问题",
}
_REVIEW_STATUS_LABELS = {
    "pass": "通过", "passed": "通过", "fail": "未通过", "failed": "失败",
    "unresolved": "待确认", "finding": "需定向修正", "pending": "待处理",
    "accepted": "已接受", "rejected": "未采纳", "dismissed": "已驳回",
    "confirmed": "已确认", "corrected": "已修正", "resolved": "已解决",
    "completed": "已完成", "complete": "已完成", "running": "进行中",
    "not_applicable": "不适用", "excluded": "已排除", "unknown": "未知",
    "refuted": "已反证", "uncertain": "待确认", "covered": "已有对应", "missed": "遗漏",
    "agree": "同意", "disagree": "不同意",
    "independent_review": "独立复核", "comparison_review": "对照复核",
    "speed": "速度型", "depth": "标准型", "standard": "标准型",
}
_REVIEW_ENUM_FIELDS = {"disposition", "verdict", "status", "quality_status", "review_status", "review_stage", "review_mode"}
_REVIEW_LITERAL_FIELDS = {
    "version_set_id", "format_version", "unit_id", "unit", "action_id", "task_id", "record_id", "record_ids",
    "case_id", "case_ids", "variant_id", "finding_record_id", "finding_key", "finding_id", "issue_id",
    "updated_record_ids", "affected_record_ids", "target_record_ids", "correction_record_ids", "related_record_ids",
    "ref", "snippet", "source_evidence",
}
_REVIEW_FIELD_ORDER = (
    "disposition", "verdict", "status", "quality_status", "review_status", "summary", "conclusion", "decision",
    "basis", "rationale", "reasoning", "reason", "what_is_known", "open_issues", "unresolved", "unresolved_items",
    "unresolved_reasons", "missing_to_resolve", "remaining_gaps", "limitations", "findings", "issues", "corrections",
    "required_change", "required_changes", "correction_record_ids", "finding_dispositions",
)


def _review_markdown(body: Any) -> str:
    """Render exact review fields recursively; keep arbitrary English text raw."""
    value = body
    if isinstance(value, str):
        candidate = value.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", candidate, re.DOTALL | re.IGNORECASE)
        candidate = fenced.group(1).strip() if fenced else candidate
        if candidate.startswith(("{", "[")):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, (dict, list)):
                    value = parsed
            except (TypeError, ValueError):
                pass
    lines: list[str] = []

    def fence_text(text: str, language: str = "text") -> list[str]:
        fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", text)), default=0) + 1)
        return [fence + language, text, fence, ""]

    def heading(label: str, depth: int) -> None:
        # Each property stays a separate block, including dictionaries in lists.
        # Do not use a JSON paragraph or nested Markdown lists the HTML renderer
        # cannot distinguish. Retain unknown field names exactly as declared.
        lines.extend([f"{'#' * min(5 + depth, 6)} {label.replace(chr(10), ' ')}", ""])

    def walk(item: Any, key: str = "", depth: int = 0) -> None:
        if key in _REVIEW_LITERAL_FIELDS:
            # Evidence and identifiers are opaque subtrees, not review fields.
            # An embedded {"status": "pass"} must remain the original evidence.
            lines.extend(fence_text(_body_text(item)))
            return
        if isinstance(item, dict):
            if not item:
                lines.extend(["空对象（原记录）", ""])
            ordered = sorted(item, key=lambda name: _REVIEW_FIELD_ORDER.index(name) if name in _REVIEW_FIELD_ORDER else len(_REVIEW_FIELD_ORDER))
            for name in ordered:
                heading(_REVIEW_FIELD_LABELS.get(name, name), depth)
                walk(item[name], name, depth + 1)
        elif isinstance(item, list):
            if not item:
                lines.extend(["空列表（原记录）", ""])
            for index, member in enumerate(item, 1):
                heading(f"第 {index} 项", depth)
                walk(member, key, depth + 1)
        else:
            text = _body_text(item)
            if isinstance(item, str) and key in _REVIEW_ENUM_FIELDS:
                text = _REVIEW_STATUS_LABELS.get(item.strip().lower(), item)
            lines.extend([_nested_record_text(text), ""])

    walk(value)
    lines.extend(["<details><summary>查看原始复核记录</summary>", ""])
    lines.extend(fence_text(_body_text(body), "text" if isinstance(body, str) else "json"))
    lines.extend(["</details>", ""])
    return "\n".join(lines).rstrip()


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _case_level(record: Any) -> str:
    body = _body_mapping(record.body)
    if (
        isinstance(body, dict)
        and body.get("format_version") == "behavior-test-case-v1"
        and body.get("test_level") in _CASE_LEVELS
    ):
        return str(body["test_level"])
    return "unclassified"


def _case_purpose(record: Any) -> str:
    body = _body_mapping(record.body)
    if (
        body is not None
        and body.get("format_version") == "behavior-test-case-v1"
        and body.get("purpose") in _CASE_PURPOSES
    ):
        return str(body["purpose"])
    return "unclassified"


def _coverage_summary(state: dict, case_records: list) -> dict:
    """Count explicit IDs only; never infer execution or semantic coverage."""
    path = run_directory(state) / "inputs" / "coverage-gaps.json"
    try:
        gaps = read_json(path)
        known = {row["coverage_id"] for row in gaps if isinstance(row, dict) and isinstance(row.get("coverage_id"), str) and row["coverage_id"]} if isinstance(gaps, list) else None
    except (OSError, ValueError):
        known = None
    refs = set()
    linked_cases = 0
    linked_coverage_cases = 0
    for record in case_records:
        body = _body_mapping(record.body) or {}
        case_refs = set()
        for ref in _items(body.get("coverage_refs")):
            value = ref.get("coverage_id") if isinstance(ref, dict) else ref
            if isinstance(value, str) and value:
                case_refs.add(value)
        refs.update(case_refs)
        if known is not None and case_refs & known:
            linked_cases += 1
            linked_coverage_cases += body.get("purpose") == "coverage"
    return {"valid_gaps": len(known) if known is not None else None,
            "coverage_cases": sum((_body_mapping(record.body) or {}).get("purpose") == "coverage" for record in case_records),
            "linked_cases": linked_cases if known is not None else None,
            "linked_coverage_cases": linked_coverage_cases if known is not None else None,
            "linked_valid_gaps": len(refs & known) if known is not None else None,
            "unverified_refs": len(refs - known) if known is not None else len(refs)}


def _markdown_list(value: Any, empty: str = "无") -> list[str]:
    values = _items(value)
    if not values:
        return [f"- {empty}"]
    return [f"- {_body_text(item).replace(chr(10), ' ')}" for item in values]


def _markdown_cell(value: Any) -> str:
    return str(value if value not in (None, "") else "未提供").replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _nested_record_text(body: Any) -> str:
    """Nest Agent headings below report headings without changing their words."""

    text = _body_text(body)
    return re.sub(
        r"^(#{1,5})(\s+)",
        lambda match: "#" * min(6, len(match.group(1)) + 2) + match.group(2),
        text,
        flags=re.MULTILINE,
    )


def _body_mapping(body: Any) -> dict[str, Any] | None:
    if isinstance(body, dict):
        return body
    if not isinstance(body, str) or not body.lstrip().startswith("{"):
        return None
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _record_title(record: Any, *, review_context: bool = False) -> str:
    body = _body_mapping(record.body)
    if body is not None:
        if coverage_body(record) is not None:
            return "业务覆盖清单"
        identifier = next((
            str(body.get(key))
            for key in ("case_id", "flow_id", "risk_id", "unresolved_id", "note_id")
            if body.get(key)
        ), "")
        title = str(body.get("title") or "")
        if identifier and title:
            return f"`{identifier}` · {title}"
        if title:
            return title
        if identifier:
            return f"`{identifier}`"
        if body.get("format_version") == "review-note-v1":
            return "复核说明"
        if review_context:
            return {"summary": "复核摘要", "note": "复核说明", "review_decision": "复核结论",
                    "review_finding": "复核发现", "finding": "复核发现", "unresolved": "待确认事项"}.get(record.kind, "复核记录")
        return {"summary": "分析说明", "note": "分析记录", "review_decision": "复核结论",
                "review_finding": "复核发现", "unresolved": "待确认事项"}.get(record.kind, "结构化记录")
    if review_context or record.kind in {"review_decision", "review_finding", "finding"}:
        return {"summary": "复核摘要", "note": "复核说明", "review_decision": "复核结论",
                "review_finding": "复核发现", "finding": "复核发现", "unresolved": "待确认事项"}.get(record.kind, "复核记录")
    if record.kind == "summary":
        return "分析说明"
    text = _body_text(record.body).strip().splitlines()[0] if record.body is not None else ""
    if text:
        return text[:100] + ("…" if len(text) > 100 else "")
    return f"`{record.kind}`"


def _record_anchor(unit_id: str, record_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"record-{unit_id}-{record_id}")
    return value.strip("-") or "record"


def _variant_anchor(unit_id: str, record_id: str, variant_id: str) -> str:
    suffix = hashlib.sha256(variant_id.encode("utf-8")).hexdigest()[:16]
    return _record_anchor(unit_id, record_id) + "-variant-" + suffix


def _record_identifiers(record: Any) -> dict[str, str]:
    body = _body_mapping(record.body)
    if body is None:
        return {}
    return {
        key: str(body[key])
        for key in ("case_id", "flow_id", "risk_id", "coverage_id", "unresolved_id", "note_id")
        if body.get(key)
    }


def _reference_markdown(
    value: Any,
    *,
    current_unit: str,
    identifier_key: str,
    anchors: dict[tuple[str, str, str], str],
) -> str:
    if isinstance(value, dict):
        identifier = str(value.get(identifier_key) or "")
        target_unit = str(value.get("unit_id") or value.get("unit") or current_unit)
        suffix = ""
        if value.get("path_id"):
            suffix = f" / {value['path_id']}"
    else:
        identifier = str(value or "")
        target_unit = current_unit
        suffix = ""
    anchor = anchors.get((target_unit, identifier_key, identifier))
    ambiguous = (target_unit, identifier_key, identifier) in anchors and not anchor
    if ambiguous:
        suffix += "（编号不唯一）"
    if identifier_key == "case_id" and isinstance(value, dict) and value.get("variant_id"):
        variant_id = str(value["variant_id"])
        variant_key = (target_unit, "variant_id", f"{identifier}/{variant_id}")
        variant_anchor = anchors.get(variant_key)
        suffix += f" / {variant_id}" + ("" if variant_anchor else "（编号不唯一）" if variant_key in anchors else "（未找到有效变体）")
        anchor = variant_anchor or anchor
    label = f"{identifier}{suffix}" if identifier else _body_text(value).replace(chr(10), " ")
    return f"[{label}](#{anchor})" if anchor else label


def _mapping_markdown(body: dict[str, Any]) -> str:
    title = str(body.get("title") or "")
    lines: list[str] = []
    for key, value in body.items():
        if key == "title" or (key in {"risk_id", "unresolved_id", "note_id"} and title):
            continue
        label = _FIELD_LABELS.get(key, key.replace("_", " "))
        if key == 'evidence_quotes':
            from pangea_agent.report.risk_method import quotes_markdown
            lines.extend([f'##### {label}', '', quotes_markdown(value), ''])
            continue
        if isinstance(value, list):
            lines.extend([f"##### {label}", "", *_markdown_list(value), ""])
        elif isinstance(value, dict):
            lines.extend([f"##### {label}", ""])
            for nested_key, nested_value in value.items():
                nested_label = _FIELD_LABELS.get(nested_key, nested_key.replace("_", " "))
                lines.append(
                    f"- **{nested_label}**：{_body_text(nested_value).replace(chr(10), ' ')}"
                )
            lines.append("")
        else:
            lines.append(f"- **{label}**：{_body_text(value).replace(chr(10), ' ')}")
    return "\n".join(lines).rstrip()


def _record_markdown(
    record: Any,
    *,
    unit_id: str = "unit",
    anchors: dict[tuple[str, str, str], str] | None = None,
    review_context: bool = False,
) -> str:
    if record.kind in {"test_case", "test_case_group"}:
        return _case_markdown(record, unit_id=unit_id, anchors=anchors or {})
    mapping = _body_mapping(record.body)
    if (review_context or record.kind in {"review_decision", "review_finding", "finding"}
            or (mapping or {}).get("format_version") in {"review-note-v1", "behavior-review-check-v1"}):
        return _review_markdown(record.body)
    ledger = coverage_body(record)
    if ledger is not None:
        return coverage_markdown(ledger, lambda value, key: _reference_markdown(
            value, current_unit=unit_id, identifier_key=key, anchors=anchors or {},
        ))
    if record.kind == "flow" and mapping and mapping.get("format_version") == "module-flow-text-v1":
        return str(mapping.get("text") or _body_text(mapping)) + "\n\n源码依据：\n" + _body_text(mapping.get("source_evidence", []))
    return _mapping_markdown(mapping) if mapping is not None else _nested_record_text(record.body)


def _case_markdown(
    record: Any,
    *,
    unit_id: str = "unit",
    anchors: dict[tuple[str, str, str], str] | None = None,
) -> str:
    anchors = anchors or {}
    body = _body_mapping(record.body)
    if body is None or body.get("format_version") != "behavior-test-case-v1":
        return _nested_record_text(record.body)

    level = str(body.get("test_level") or "unclassified")
    purpose = str(body.get("purpose") or "unclassified")
    lines = [
        f"- **用例层级**：`{level}`（{_CASE_LEVELS.get(level, '未分类')}）",
        f"- **测试目的**：`{purpose}`（{_CASE_PURPOSES.get(purpose, '归属未声明')}）",
        f"- **入口**：{body.get('entry') or '未提供'}",
        f"- **执行条件**：{ {'ready': '具备执行条件（不代表实测通过）', 'needs_instrumentation': '待补注入设施', 'unknown': '待补执行条件'}.get(str(body.get('execution_readiness')), '未标注')}",
        *(_markdown_list(body["readiness_reason"]) if body.get("readiness_reason") else []),
        f"- **执行状态**：`{body.get('execution_status') or '未声明'}`",
        "",
        "##### 前置条件",
        "",
        *_markdown_list(body.get("preconditions", body.get("precondition")), "未提供"),
        "",
        "##### 操作与预期",
        "",
    ]
    steps = [item for item in _items(body.get("steps", body.get("test_steps"))) if isinstance(item, dict)]
    if steps:
        lines.extend([
            "| # | 测试人员动作 | 对应预期 |",
            "|---:|---|---|",
            *[
                f"| {index} | {_markdown_cell(step.get('action', step.get('step')))} | "
                f"{_markdown_cell(step.get('expected', step.get('expected_result')))} |"
                for index, step in enumerate(steps, 1)
            ],
        ])
    else:
        lines.append("- 未提供可执行步骤。")
    if body.get("expected_results"):
        lines.extend(["", "##### 整体预期（原文单独提供）", "", *_markdown_list(body["expected_results"], "未提供")])
    variants = _items(body.get("variants"))
    if variants:
        lines.extend([
            "",
            "##### 输入变体",
            "",
            "| 变体编号 | 输入或条件 | 预期 |",
            "|---|---|---|",
        ])
        variant_counts = Counter(str(item.get("variant_id")) for item in variants if isinstance(item, dict) and item.get("variant_id"))
        case_key = (unit_id, "case_id", str(body.get("case_id") or ""))
        ambiguous_case = case_key in anchors and not anchors[case_key]
        for index, item in enumerate(variants, 1):
            if not isinstance(item, dict):
                lines.append(f"| 第 {index} 项（原文） | {_markdown_cell(_body_text(item))} | 未提供 |")
                continue
            variant_id = str(item.get("variant_id") or "")
            anchor = _variant_anchor(unit_id, record.record_id, variant_id) if variant_id and variant_counts[variant_id] == 1 and not ambiguous_case else ""
            marker = f"<!--pangea-anchor:{anchor}-->" if anchor else ""
            label = variant_id + ("（编号不唯一）" if variant_counts[variant_id] > 1 or ambiguous_case else "") if variant_id else f"第 {index} 项（未声明编号）"
            lines.append(f"| {marker}{_markdown_cell(label)} | "
                         f"{_markdown_cell(item.get('input'))} | {_markdown_cell(item.get('expected'))} |")
        raw_variants = json.dumps(body.get("variants"), ensure_ascii=False, indent=2)
        fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", raw_variants)), default=0) + 1)
        lines.extend(["", "<details><summary>参数变体原始声明（含扩展字段）</summary>", "", fence + "json", raw_variants, fence, "", "</details>"])
    lines.extend([
        "",
        "##### 外部观测",
        "",
        *_markdown_list(body.get("external_observations"), "未提供"),
        "",
        "##### 清理与恢复",
        "",
        *_markdown_list(body.get("cleanup"), "未提供"),
    ])
    evidence = _items(body.get("source_evidence"))
    references = [
        ("关联流程", "flow_id", body.get("flow_refs")),
        ("关联 Coverage", "coverage_id", body.get("coverage_refs")),
        ("关联风险", "risk_id", body.get("risk_refs")),
    ]
    for label, identifier_key, values in references:
        if _items(values):
            lines.extend([
                "",
                f"##### {label}",
                "",
                *[
                    f"- {_reference_markdown(item, current_unit=unit_id, identifier_key=identifier_key, anchors=anchors)}"
                    for item in _items(values)
                ],
            ])
    if evidence:
        lines.extend(["", "##### 源码证据", "", *_markdown_list(evidence)])
    return "\n".join(lines)


def _action_records(state: dict, progress: dict) -> list[tuple[dict, Any]]:
    items: list[tuple[dict, Any]] = []
    for action_id, action in progress.get("actions", {}).items():
        task_path = action.get("task_path") if isinstance(action, dict) else None
        if not isinstance(task_path, str):
            raise ValueError(f"source-first action 缺少 task_path：{action_id}")
        try:
            task = read_json(Path(task_path))
            result_path = task.get("result_path")
            if not isinstance(result_path, str) or not Path(result_path).is_file():
                raise ValueError(f"source-first action 缺少可读取 result：{action_id}")
            items.append((
                {"action_id": action_id, **action, "task": task},
                read_result(Path(result_path)),
            ))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"source-first action 产物不可读取：{action_id}: {exc}") from exc
    return items


def _markdown(state: dict, progress: dict, records: list[tuple[dict, Any]]) -> str:
    lines = [
        "# PANGEA Source-First Report",
        "",
        f"- Run: `{progress.get('run_id', state.get('run_id', ''))}`",
        f"- Workflow: `{progress.get('workflow_version') or 'source-first-v1'}`",
        f"- Lifecycle: `{progress.get('lifecycle_status', 'running')}`",
        f"- Stage: `{progress.get('stage', 'preparing')}`",
        f"- Quality: `{progress.get('quality_status') or 'UNRESOLVED'}`",
        f"- Needs user: `{bool(progress.get('needs_user', False))}`",
        "",
        "## Revision ledger",
        "",
    ]
    first = progress.get("first_finish_revisions", {})
    accepted = progress.get("accepted_revisions", {})
    if first or accepted:
        for action_id in sorted(set(first) | set(accepted)):
            lines.append(
                f"- `{action_id}`: first finish `{first.get(action_id, 'pending')}`, "
                f"accepted `{accepted.get(action_id, 'pending')}`"
            )
    else:
        lines.append("- No accepted Agent revision recorded yet.")
    comparison_sets = [
        item[0].get("task", {}).get("version_set_id")
        for item in records
        if item[0].get("stage") == "comparison_review"
        and item[0].get("task", {}).get("version_set_id")
    ]
    if comparison_sets:
        lines.extend(["", "## Review binding", ""])
        lines.extend(f"- Comparison version set: `{value}`" for value in comparison_sets)
    lines.extend(["", "## Agent records", ""])
    for action, result in records:
        lines.extend([
            f"### `{action['action_id']}` ({action.get('stage', 'unknown')})",
            "",
            f"- Result revision: `{result.revision}`",
            f"- Completion: `{result.completion.complete if result.completion else 'not declared'}`",
            "",
        ])
        if action.get("stage") == "targeted_closure":
            lines.extend([
                "> This correction was written by the original worker after comparison and has not received an additional independent review.",
                "",
            ])
        superseded_by = supersession_map(result)
        active = [
            record for record in result.records
            if record.record_id not in superseded_by
        ]
        retired = [
            record for record in result.records
            if record.record_id in superseded_by
        ]
        lines.extend([
            f"- Active records: `{len(active)}`",
            f"- Superseded records: `{len(retired)}`",
            "",
            "#### Effective records",
            "",
        ])
        for record in active:
            lines.extend([
                f"##### `{record.record_id}` · `{record.kind}`",
                "",
                (_review_markdown(record.body)
                 if action.get("stage") in {"independent_review", "comparison_review"} or record.kind in {"review_decision", "review_finding", "finding"}
                 else _body_text(record.body)),
                "",
            ])
            if record.evidence:
                lines.extend(["Evidence:", "", _body_text(record.evidence), ""])
            if record.relates_to:
                lines.extend(["Relates to:", "", _body_text(record.relates_to), ""])
            if record.supersedes:
                lines.extend(["Supersedes:", "", _body_text(record.supersedes), ""])
        if retired:
            lines.extend(["#### Superseded records (audit only)", ""])
        for record in retired:
            lines.extend([
                f"##### `{record.record_id}` · `{record.kind}` · superseded",
                "",
                f"Superseded by: `{', '.join(superseded_by[record.record_id])}`",
                "",
                (_review_markdown(record.body)
                 if action.get("stage") in {"independent_review", "comparison_review"} or record.kind in {"review_decision", "review_finding", "finding"}
                 else _body_text(record.body)),
                "",
            ])
            if record.evidence:
                lines.extend(["Evidence:", "", _body_text(record.evidence), ""])
            if record.relates_to:
                lines.extend(["Relates to:", "", _body_text(record.relates_to), ""])
    degradations = progress.get("degradations", [])
    blocking_reason = progress.get("blocking_reason")
    if blocking_reason:
        lines.extend(["## Attention", "", _body_text(blocking_reason), ""])
    if degradations:
        lines.extend(["## Deterministic diagnostics", ""])
        lines.extend(f"- {item}" for item in degradations)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _behavior_markdown(
    state: dict,
    progress: dict,
    records: list[tuple[dict, Any]],
    diagram_assets: dict[str, str] | None = None,
    scene: dict | None = None,
) -> str:
    if diagram_assets is None:
        diagram_assets = {}
    lifecycle = str(progress.get("lifecycle_status", "running"))
    quality = str(progress.get("quality_status") or "UNRESOLVED")
    if progress.get("partial_delivery"):
        delivery_message = "用户结束定向修正，交付截至停止时已保存的结果；部分修正未完成，未解决事项和未复核内容保留，质量状态为 UNRESOLVED。"
    elif lifecycle == "complete" and quality == "PASS":
        delivery_message = "用例分析与审核已完成；该状态不代表被测系统已经执行通过。"
    elif lifecycle == "complete":
        delivery_message = (
            "分析流程已经结束；已保存当前冻结范围内的分析结果，仍有待确认事项，"
            "整体质量状态保持 UNRESOLVED。"
        )
    else:
        delivery_message = "分析仍在进行，当前报告不是最终交付。"
    lines = [
        f"# PANGEA {scene['label'] if scene else '业务行为测试用例'}报告",
        "",
        f"- Run: `{progress.get('run_id', state.get('run_id', ''))}`",
        f"- Analysis profile: `{'behavior-test-v2' if scene else 'behavior-test-v1'}`",
        f"- Lifecycle: `{lifecycle}`",
        f"- Quality: `{quality}`",
        "- 质量范围：业务行为用例的生成与审核；未附实际执行证据时，不代表被测产品或 Coverage 已通过。",
        "",
        "## 交付状态",
        "",
        f"> {delivery_message}",
        "",
    ]
    lines.extend(_flow_comparison_markdown(state))
    accepted_closure_units = {
        str(action.get("task", {}).get("unit_id"))
        for action, _result in records
        if action.get("stage") == "targeted_closure" and (action.get("status") == "accepted" or action.get("delivery_revision") is not None)
    }
    delivery: list[tuple[dict, Any]] = []
    for action, result in records:
        stage = action.get("stage")
        unit_id = str(action.get("task", {}).get("unit_id"))
        if stage == "targeted_closure" and (action.get("status") == "accepted" or action.get("delivery_revision") is not None):
            delivery.append((action, result))
        elif stage == "unit_analysis" and unit_id not in accepted_closure_units:
            delivery.append((action, result))

    anchors: dict[tuple[str, str, str], str] = {}
    for action, result in delivery:
        unit_id = str(action.get("task", {}).get("unit_id") or "unit")
        retired_by = supersession_map(result)
        for record in result.records:
            if record.record_id in retired_by:
                continue
            anchor = _record_anchor(unit_id, record.record_id)
            for identifier_key, identifier in _record_identifiers(record).items():
                key = (unit_id, identifier_key, identifier)
                anchors[key] = "" if key in anchors else anchor
            body = _body_mapping(record.body) or {}
            if record.kind in {"test_case", "test_case_group"} and body.get("case_id"):
                for variant in _items(body.get("variants")):
                    if isinstance(variant, dict) and variant.get("variant_id"):
                        variant_id = str(variant["variant_id"])
                        key = (unit_id, "variant_id", f"{body['case_id']}/{variant_id}")
                        anchors[key] = "" if key in anchors else _variant_anchor(unit_id, record.record_id, variant_id)
    for (unit_id, key, identifier), anchor in list(anchors.items()):
        if key == "case_id" and not anchor:
            for variant_key in list(anchors):
                if variant_key[0] == unit_id and variant_key[1] == "variant_id" and variant_key[2].startswith(identifier + "/"):
                    anchors[variant_key] = ""

    def active_and_retired(result: Any) -> tuple[list[Any], list[Any], dict[str, list[str]]]:
        retired_by = supersession_map(result)
        active = [record for record in result.records if record.record_id not in retired_by]
        retired = [record for record in result.records if record.record_id in retired_by]
        return active, retired, retired_by

    def add_records(
        title: str,
        selected: list[tuple[dict, Any]],
        kinds: set[str] | None,
        levels: set[str] | None = None,
        purposes: set[str] | None = None,
        readiness: str | None = None,
    ) -> None:
        if scene and not scene["presentation"]["risks"] and title in {"风险用例", "风险依据"}:
            return
        section_start = len(lines)
        lines.extend([f"## {title}", ""])
        found = False
        for action, result in selected:
            active, _retired, _retired_by = active_and_retired(result)
            chosen = [
                record for record in active
                if (kinds is None or record.kind in kinds)
                and (coverage_body(record) is not None if title == "业务覆盖清单" else
                     coverage_body(record) is None if title == "其他交付说明" else True)
                and (levels is None or _case_level(record) in levels)
                and (purposes is None or _case_purpose(record) in purposes)
                and (record.kind not in {"test_case", "test_case_group"}
                     or (isinstance(record.body, dict) and str(record.body.get("execution_readiness")) in {"needs_instrumentation", "unknown"}) == (readiness == "needs_setup"))
            ]
            for record in chosen:
                found = True
                unit_id = str(action.get("task", {}).get("unit_id") or "unit")
                body = _record_markdown(
                    record, unit_id=unit_id, anchors=anchors,
                    review_context=action.get("stage") in {"independent_review", "comparison_review"},
                )
                anchor = _record_anchor(unit_id, record.record_id)
                lines.extend([
                    f"### `{unit_id}` · {_record_title(record, review_context=action.get('stage') in {'independent_review', 'comparison_review'})} <!--pangea-record:{anchor}-->",
                    "",
                    body,
                    "",
                ])
                if scene and scene.get('method_version') == 'risk-method-v1':
                    lines.extend(f'> 引文与源码不符（待核对）：{w.get("message", "")}' for w in result.warnings if w.get('kind') == 'evidence_quote_mismatch' and w.get('record_id') == record.record_id)
                if record.evidence:
                    lines.extend(["证据：", "", _body_text(record.evidence), ""])
                if record.relates_to:
                    lines.extend(["关联：", "", _body_text(record.relates_to), ""])
        if not found:
            del lines[section_start:]

    case_records = []
    for _action, result in delivery:
        active, _retired, _retired_by = active_and_retired(result)
        case_records.extend(
            record for record in active
            if record.kind in {"test_case", "test_case_group"}
        )
    level_counts = {
        level: sum(_case_level(record) == level for record in case_records)
        for level in [*_CASE_LEVELS, "unclassified"]
    }
    ready_count = sum(
        _case_level(record) in {"business_blackbox", "developer_assisted"}
        and (_body_mapping(record.body) or {}).get("execution_readiness") == "ready"
        for record in case_records
    )
    ratio = f"{ready_count / len(case_records):.1%}" if case_records else "无可统计用例"
    if any(record.kind == "test_case_group" for record in case_records):
        ratio = "包含旧用例组，独立用例比例待复核"
    formal_case_records = [
        record for record in case_records
        if _case_level(record) in {"business_blackbox", "developer_assisted"}
    ]
    purpose_counts = {
        purpose: sum(_case_purpose(record) == purpose for record in formal_case_records)
        for purpose in [*_CASE_PURPOSES, "unclassified"]
    }
    if scene:
        lines.extend(["## 覆盖数据来源与匹配诊断", ""])
        try:
            match = read_json(run_directory(state) / "inputs" / "coverage-match-summary.json")
            lines.extend([
                f"- 输入来源：{len(match.get('sources', []))}；位置匹配：{len(match.get('matched', []))}；未匹配：{len(match.get('unmatched', []))}；匹配歧义：{len(match.get('ambiguous', []))}。",
                "- 位置匹配不代表数据版本已核实，也不代表实测覆盖或补测已执行。", "",
                _body_text(match.get("sources", [])), "", str(match.get("note", "")), "",
            ])
        except (OSError, ValueError, TypeError):
            lines.extend(["匹配诊断不可读取，不能据此推定没有缺口。", ""])
    coverage_summary = _coverage_summary(state, case_records)
    lines.extend([
        "## 用例总览",
        "",
        "以下数量来自 Analysis 的显式声明；Python 不根据函数名或正文猜测测试目的和层级。",
        "",
        f"- {'本次输入匹配 Coverage 缺口' if scene else '本次有效 Coverage 缺口'}：{coverage_summary['valid_gaps'] if coverage_summary['valid_gaps'] is not None else '未记录/不可读取'}",
        f"- 关联有效缺口的用例：{coverage_summary['linked_cases'] if coverage_summary['linked_cases'] is not None else '无法核实'}（其中新增补测用例：{coverage_summary['linked_coverage_cases'] if coverage_summary['linked_coverage_cases'] is not None else '无法核实'}）",
        f"- 全部层级新增补测用例：{coverage_summary['coverage_cases']}",
        f"- 全部用例显式关联的去重有效缺口：{coverage_summary['linked_valid_gaps'] if coverage_summary['linked_valid_gaps'] is not None else '无法核实'}",
        f"- 未能由冻结缺口核实的引用：{coverage_summary['unverified_refs']}",
        "- 关联仅表示作者声明，不代表执行通过或覆盖率提升。未解决原因见本报告的分析记录及待确认项。",
        f"- 分支用例：`{purpose_counts['branch']}`",
        f"- 参数变体声明：`{sum(len(_items((_body_mapping(record.body) or {}).get('variants'))) for record in case_records)}`（单独计数，不视为已执行次数）",
        f"- 未覆盖用例：`{purpose_counts['coverage']}`",
        *([f"- 风险用例：`{purpose_counts['risk']}`"] if scene is None or scene["presentation"]["risks"] else ["- 本次未开展风险分析。"]),
        f"- 测试目的未声明：`{purpose_counts['unclassified']}`",
        f"- 产品黑盒：`{level_counts['business_blackbox']}`",
        f"- 业务灰盒（含定点故障注入）：`{level_counts['developer_assisted']}`",
        f"- 可实施黑盒与灰盒（作者声明）：`{ready_count}/{len(case_records)}`，`{ratio}`。",
        ("- 风险场景不设黑盒比例目标；用例层级按实际操作如实声明。" if scene and scene.get('presentation', {}).get('blackbox_target') is False and scene.get('id') == 'risk-analysis' else "- 用户冻结目标为接口契约分析，不设产品黑盒与灰盒 70% 比例目标；用例层级及执行条件如实声明。" if scene and scene.get('presentation', {}).get('blackbox_target') is False else "- 可实施黑盒与灰盒交付目标 ≥70%。"),
        "- 上述比例只汇总有效记录的显式层级和 ready 声明，未提供声明的不计入分子；语义达标以 Reviewer 结论为准，定向修正后的记录不冒充已独立复核。",
        f"- 接口与白盒等辅助用例（含待补执行条件）：`{level_counts['interface_contract'] + level_counts['whitebox_support'] + level_counts['unclassified']}`",
        "",
    ])
    if scene and scene.get('presentation', {}).get('check_ledger'):
        from pangea_agent.report.risk_method import ledger_markdown, review_markdown
        ledger, checks = ledger_markdown(delivery, anchors)
        totals = {c: sum(r.body.get('conclusion') == c for r in checks) for c in ('confirmed', 'excluded', 'unresolved', 'not_applicable')}
        risk_count = sum(r.kind == 'risk' for _, result in delivery for r in active_and_retired(result)[0])
        lines.extend([f'- 风险记录：{risk_count}', f'- 检查账本：{len(checks)} 条（已证实 {totals["confirmed"]}／已排除 {totals["excluded"]}／待确认 {totals["unresolved"]}／不适用 {totals["not_applicable"]}）', '', *ledger, *review_markdown(records)])
    add_records("业务覆盖清单", delivery, {"note"})

    def other_flow_record(unit_id: str, record: Any) -> list[str]:
        anchor = _record_anchor(unit_id, record.record_id)
        lines_out = [
            f"### `{unit_id}` · {_record_title(record)} <!--pangea-record:{anchor}-->",
            "",
            _record_markdown(record, unit_id=unit_id, anchors=anchors),
            "",
        ]
        if record.evidence or record.relates_to:
            lines_out.extend(["<details><summary>证据与关联</summary>", ""])
            lines_out.extend(f"- 证据：{_body_text(item).replace(chr(10), ' ')}" for item in _items(record.evidence))
            lines_out.extend(f"- 关联：{_body_text(item).replace(chr(10), ' ')}" for item in _items(record.relates_to))
            lines_out.extend(["", "</details>", ""])
        return lines_out

    lines.extend(diagram_diagnostic_markdown(delivery))
    lines.extend(flow_section_markdown(
        [
            (str(action.get("task", {}).get("unit_id") or "unit"), active_and_retired(result)[0])
            for action, result in delivery
        ],
        diagram_assets=diagram_assets,
        reference=lambda value, unit_id, key: _reference_markdown(
            value, current_unit=unit_id, identifier_key=key, anchors=anchors,
        ),
        other_record_markdown=other_flow_record,
    ))
    add_records(
        "分支用例",
        delivery,
        {"test_case", "test_case_group"},
        {"business_blackbox", "developer_assisted"},
        {"branch"},
    )
    add_records(
        "未覆盖用例",
        delivery,
        {"test_case", "test_case_group"},
        {"business_blackbox", "developer_assisted"},
        {"coverage"},
    )
    add_records(
        "风险用例",
        delivery,
        {"test_case", "test_case_group"},
        {"business_blackbox", "developer_assisted"},
        {"risk"},
    )
    add_records(
        "开发辅助附录",
        delivery,
        {"test_case", "test_case_group"},
        {"interface_contract", "whitebox_support", "unclassified"},
    )
    add_records("待补执行条件的用例（不计入可执行用例）", delivery, {"test_case", "test_case_group"}, readiness="needs_setup")
    add_records("风险依据", delivery, {"risk"})
    add_records("Coverage 与分析依据", delivery, {"evidence", "blackbox_translation"})
    add_records("待确认事项", delivery, {"unresolved"})
    if scene and not scene["presentation"]["risks"]:
        add_records("超出场景职责的用例声明（原文保留，待核对）", delivery, {"test_case", "test_case_group"}, {"business_blackbox", "developer_assisted"}, {"risk"})
    add_records("其他交付说明", delivery, {"summary", "note", *({"risk"} if scene and not scene["presentation"]["risks"] else set())})
    add_records("业务用例质量复核", [item for item in records if item[0].get("stage") == "comparison_review" and item[0].get("status") == "accepted"], {"summary", "note", "review_decision", "review_finding", "unresolved"})
    if accepted_closure_units:
        lines.extend(["> 定向修正由原分析 Worker 完成；修正后的内容不代表已接受额外独立复核。", ""])
    if progress.get("blocking_reason"):
        lines.extend(["## Attention", "", _body_text(progress["blocking_reason"]), ""])
    return "\n".join(lines).rstrip() + "\n"


def _flow_comparison_markdown(state: dict) -> list[str]:
    """Report frozen exact-identity differences, not semantic stability scores."""
    run = run_directory(state)
    initial_path = run / "inputs" / "flow-comparison.json"
    delivery_path = run / "outputs" / "flow-delivery-comparison.json"
    path = delivery_path if delivery_path.is_file() else initial_path
    if not path.is_file():
        return []
    lines = ["## 流程版本对照", "",
             "> 以下为冻结输入和显式流程身份的机械对照；有差异不等于错误，无差异不证明业务覆盖完整。是否接受变更以 Reviewer 结论为准。", ""]
    if path == delivery_path:
        lines.extend(["> 当前显示最终交付修订的机械对照，不代表定向修正获得了额外语义审核。首轮审核输入仍保留在冻结记录中。", ""])
    try:
        data = read_json(path)
    except (OSError, ValueError, TypeError) as exc:
        return [*lines, f"流程对照记录暂不可读取：{_markdown_cell(str(exc))}。", ""]
    if isinstance(data, dict):
        baseline = data.get("baseline") if isinstance(data.get("baseline"), dict) else {}
        comparison = data.get("comparability") if isinstance(data.get("comparability"), dict) else {}
        statuses = {"comparable": "已记录输入可比", "different_inputs": "存在输入差异", "unknown": "可比性待确认"}
        lines.extend([
            f"- 基线 Run：{_markdown_cell(baseline.get('run_id'))}；版本：{_markdown_cell(baseline.get('version_id'))}",
            f"- 输入可比性：{statuses.get(str(comparison.get('status')), _markdown_cell(comparison.get('status')))}",
            "", "| 对照维度 | 记录结果 |", "|---|---|",
        ])
        dimensions = comparison.get("dimensions")
        names = {"source": "源码", "scope": "范围与依赖", "assets": "输入资料", "methodology": "方法版本", "model": "已声明模型与配置", "objective": "任务目标与设置"}
        for key, value in dimensions.items() if isinstance(dimensions, dict) else []:
            lines.append(f"| {_markdown_cell(names.get(key, key))} | {_markdown_cell({'same': '相同', 'changed': '不同', 'unknown': '待确认'}.get(str(value), value))} |")
        lines.extend(["", "| 流程 | 结构对照 | 节点增／删／改 | 连线增／删／改 | 路径增／删／改 | 其他变化 |", "|---|---|---|---|---|---|"])

        def changes(value: Any) -> str:
            if not isinstance(value, dict):
                return "未记录"
            return "／".join(str(len(value[key])) if isinstance(value.get(key), list) else "?" for key in ("added", "removed", "changed"))

        names = {"unchanged": "结构未变", "changed": "结构有变化", "added": "新增", "removed": "移除", "unresolved": "待确认"}
        for flow in _items(data.get("flows")):
            if not isinstance(flow, dict):
                lines.append(f"| 无法结构化 | {_markdown_cell(_body_text(flow))} | — | — | — | 原文保留 |")
                continue
            flags = [label for key, label in (("main_paths_changed", "主路径声明"), ("attachment_changed", "流程挂接"), ("content_changed", "文字或源码依据（待语义核对）"), ("presentation_changed", "原始声明")) if flow.get(key)]
            lines.append(f"| {_markdown_cell(flow.get('flow_id'))} | {_markdown_cell(names.get(str(flow.get('status')), flow.get('status')))} | "
                         f"{changes(flow.get('nodes'))} | {changes(flow.get('edges'))} | {changes(flow.get('paths'))} | {'、'.join(flags) or '未记录'} |")
        notes = [*_items(comparison.get("notes")), *_items(data.get("warnings"))]
        if notes:
            lines.extend(["", *[f"- {_markdown_cell(_body_text(note))}" for note in notes]])
    raw = _body_text(data)
    fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", raw)), default=0) + 1)
    lines.extend(["", "<details><summary>流程对照明细（含变更前后值）</summary>", "", fence + "json", raw, fence, "", "</details>", ""])
    return lines


_NAV_TITLES = {
    "业务覆盖清单", "流程版本对照",
    "风险检查账本", "风险检查复核",
    "交付状态",
    "用例总览",
    "模块流程说明",
    "分支用例",
    "未覆盖用例",
    "风险用例",
    "开发辅助附录",
    "风险依据",
    "Coverage 与分析依据",
    "待确认事项",
    "其他交付说明",
    "业务用例质量复核",
    "Revision ledger",
    "Review binding",
    "Agent records",
    "Attention",
    "Deterministic diagnostics",
}


def _inline_html(value: str) -> str:
    marker = re.match(r"^<!--pangea-anchor:([A-Za-z0-9_.-]+)-->", value)
    prefix = f'<span id="{marker.group(1)}"></span>' if marker else ""
    if marker:
        value = value[marker.end():]
    escaped = html.escape(value)
    escaped = re.sub(
        r"\[([^\]]+)\]\((#[A-Za-z0-9_.-]+)\)",
        r'<a href="\2">\1</a>',
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return prefix + re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)


def _markdown_table_cells(line: str) -> list[str]:
    value = line.strip().strip("|")
    return [item.replace("\\|", "|").strip() for item in re.split(r"(?<!\\)\|", value)]


def _is_table_divider(line: str) -> bool:
    cells = _markdown_table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _html(markdown: str, embedded_assets: dict[str, str] | None = None) -> str:
    """Render the safe Markdown subset used by source-first reports."""

    embedded_assets = embedded_assets or {}
    lines = markdown.splitlines()
    section_ids: dict[str, str] = {}
    ordered_titles: list[str] = []
    for line in lines:
        if not line.startswith("## "):
            continue
        title = line[3:].strip()
        if title in _NAV_TITLES and title not in section_ids:
            section_ids[title] = f"section-{len(section_ids) + 1}"
            ordered_titles.append(title)

    chunks: list[str] = []
    in_list = ""
    in_detail = False
    in_fold = False
    index = 0

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            chunks.append(f"</{in_list}>")
            in_list = ""

    def open_list(tag: str) -> None:
        nonlocal in_list
        if in_list != tag:
            close_list()
            chunks.append(f"<{tag}>")
            in_list = tag

    def close_fold() -> None:
        nonlocal in_fold
        close_list()
        if in_fold:
            chunks.append("</details>")
            in_fold = False

    def close_detail() -> None:
        nonlocal in_detail
        close_fold()
        if in_detail:
            chunks.append("</div></details>")
            in_detail = False

    while index < len(lines):
        line = lines[index]
        image_match = re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)", line.strip())
        if image_match:
            close_list()
            asset = embedded_assets.get(image_match.group(2))
            if asset:
                chunks.append(f'<div class="flow-svg">{asset}</div>')
            else:
                chunks.append(
                    f'<p class="muted">流程图资源：{html.escape(image_match.group(2))}</p>'
                )
            index += 1
            continue
        fold_match = re.fullmatch(r"<details><summary>([^<]*)</summary>", line.strip())
        if fold_match:
            close_fold()
            chunks.append(f'<details class="fold"><summary>{_inline_html(fold_match.group(1))}</summary>')
            in_fold = True
            index += 1
            continue
        if line.strip() == "</details>":
            close_fold()
            index += 1
            continue
        if line.startswith("```"):
            close_list()
            fence = re.match(r"^`+", line).group(0)
            language = line[len(fence):].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not re.fullmatch(r"`{" + str(len(fence)) + r",}\s*", lines[index]):
                code_lines.append(lines[index])
                index += 1
            language_attr = f' data-language="{html.escape(language)}"' if language else ""
            chunks.append(
                f"<pre{language_attr}><code>{html.escape(chr(10).join(code_lines))}</code></pre>"
            )
            index += 1
            continue
        if (
            "|" in line
            and index + 1 < len(lines)
            and _is_table_divider(lines[index + 1])
        ):
            close_list()
            headers = _markdown_table_cells(line)
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_markdown_table_cells(lines[index]))
                index += 1
            head = "".join(f"<th>{_inline_html(cell)}</th>" for cell in headers)
            body = "".join(
                "<tr>" + "".join(
                    f"<td>{_inline_html(row[column] if column < len(row) else '')}</td>"
                    for column in range(len(headers))
                ) + "</tr>"
                for row in rows
            )
            chunks.append(
                f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>"
            )
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            record_heading = re.fullmatch(
                r"(.*?)\s+<!--pangea-record:([A-Za-z0-9_.-]+)-->",
                title,
            )
            record_id = record_heading.group(2) if record_heading else ""
            if record_heading:
                title = record_heading.group(1)
            if level <= 3:
                close_detail()
            if level == 3 and title.startswith("`"):
                id_attr = f' id="{record_id}"' if record_id else ""
                open_attr = " open" if title.endswith("〔主流程〕") else ""
                chunks.append(
                    f'<details{id_attr} class="record"{open_attr}><summary>{_inline_html(title)}</summary><div class="record-body">'
                )
                in_detail = True
            else:
                identifier = section_ids.get(title)
                id_attr = f' id="{identifier}"' if identifier else ""
                chunks.append(f"<h{level}{id_attr}>{_inline_html(title)}</h{level}>")
            index += 1
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            open_list("ul")
            chunks.append(f"<li>{_inline_html(stripped[2:])}</li>")
            index += 1
            continue
        ordered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if ordered:
            open_list("ol")
            chunks.append(f"<li>{_inline_html(ordered.group(1))}</li>")
            index += 1
            continue
        close_list()
        if stripped.startswith("> "):
            chunks.append(f'<blockquote class="status">{_inline_html(stripped[2:])}</blockquote>')
        elif line:
            chunks.append(f"<p>{_inline_html(line)}</p>")
        index += 1
    close_detail()

    navigation = "".join(
        f'<a href="#{section_ids[title]}">{html.escape(title)}</a>'
        for title in ordered_titles
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>PANGEA 业务行为测试用例报告</title><style>'
        ':root{color-scheme:light;--ink:#172033;--muted:#637086;--line:#dce2ea;'
        '--panel:#f7f9fc;--accent:#3157d5;--warn:#fff5d8}'
        '*{box-sizing:border-box}body{margin:0;color:var(--ink);background:#fff;'
        'font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif}'
        '.layout{display:grid;grid-template-columns:250px minmax(0,980px);gap:36px;'
        'max-width:1320px;margin:0 auto;padding:28px 24px 64px}'
        'nav{position:sticky;top:20px;align-self:start;max-height:calc(100vh - 40px);'
        'overflow:auto;border:1px solid var(--line);border-radius:12px;padding:12px;background:var(--panel)}'
        'nav strong{display:block;padding:6px 8px 10px}nav a{display:block;color:#33415c;'
        'padding:6px 8px;border-radius:7px;text-decoration:none}nav a:hover{background:#e9eefc;color:#2446b8}'
        'main{min-width:0}h1{font-size:30px;line-height:1.25;margin:0 0 22px}'
        'h2{font-size:22px;margin:42px 0 16px;padding-top:6px;border-top:1px solid var(--line)}'
        'h3{font-size:18px;margin:26px 0 12px}h4{font-size:17px;margin:22px 0 10px}'
        'h5{font-size:15px;margin:18px 0 8px}p{margin:8px 0}ul{margin:8px 0 14px;padding-left:23px}'
        'code{font:0.92em ui-monospace,SFMono-Regular,Menlo,monospace;background:#edf1f7;'
        'padding:1px 5px;border-radius:4px}pre{overflow:auto;white-space:pre-wrap;background:#111827;'
        'color:#eef2ff;padding:14px;border-radius:9px}pre code{background:none;padding:0}'
        '.status{margin:14px 0;padding:13px 16px;border-left:4px solid #d49318;'
        'background:#fff5d8;border-radius:6px}.table-wrap{overflow:auto;margin:12px 0 18px}'
        'table{width:100%;border-collapse:collapse}th,td{border:1px solid var(--line);'
        'padding:8px 10px;text-align:left;vertical-align:top}th{background:#eef2f8}'
        'details.record{margin:12px 0;border:1px solid var(--line);border-radius:10px;background:#fff}'
        'details.record>summary{cursor:pointer;font-weight:650;padding:12px 14px;background:var(--panel);'
        'border-radius:10px}.record-body{padding:4px 16px 16px}'
        'details.fold{margin:10px 0;color:var(--muted)}details.fold>summary{cursor:pointer}ol{margin:8px 0 14px;padding-left:23px}'
        '.flow-svg{overflow-x:auto;margin:14px 0 20px;padding:14px;border:1px solid var(--line);'
        'border-radius:10px;background:#fbfcfe}.flow-svg svg{display:block;width:auto;max-width:none;min-width:680px}'
        '.flow-node rect{fill:#fff;stroke-width:2}.flow-kind{fill:#667085;font-size:11px}'
        '.flow-label{fill:#172033;font-size:14px}.flow-edge{fill:none;stroke:#667085;stroke-width:1.7}'
        '.flow-edge-label{fill:#8a5a00;font-size:12px;paint-order:stroke;stroke:#fff;stroke-width:4px;stroke-linejoin:round}'
        '@media(max-width:820px){.layout{display:block;padding:12px 14px 48px}nav{position:sticky;top:0;'
        'z-index:5;display:flex;align-items:center;gap:4px;max-height:none;margin-bottom:18px;'
        'overflow-x:auto;white-space:nowrap;border-radius:0 0 10px 10px;padding:8px}'
        'nav strong,nav a{flex:0 0 auto}nav strong{padding:6px 10px 6px 4px}nav a{display:block}'
        'h1{font-size:25px}}'
        '</style></head><body><div class="layout"><nav><strong>报告导航</strong>'
        f'{navigation or "<span>当前报告没有章节导航</span>"}</nav><main>{"".join(chunks)}'
        '</main></div><script>function openTarget(){const id=location.hash.slice(1);'
        'if(!id)return;const target=document.getElementById(id);if(target){let parent=target;'
        'while(parent){if(parent.tagName==="DETAILS")parent.open=true;parent=parent.parentElement;}'
        'target.scrollIntoView({block:"nearest"});}}addEventListener("hashchange",openTarget);openTarget();</script></body></html>'
    )


def write_source_first_reports(state: dict, *, progress: dict | None = None) -> dict[str, str]:
    if progress is None:
        progress = read_json(run_directory(state) / "progress.json")
    records = _action_records(state, progress)
    markdown_path = run_directory(state) / "report.md"
    html_path = run_directory(state) / "report.html"
    contract = state.get("task_contract", {})
    if not isinstance(contract, dict):
        contract = {}
    profile = contract.get("analysis_profile")
    if profile is None:
        contract_path = run_directory(state) / "inputs" / "task-contract.json"
        if contract_path.is_file():
            frozen_contract = read_json(contract_path)
            if isinstance(frozen_contract, dict):
                contract = frozen_contract
                profile = frozen_contract.get("analysis_profile")
    from pangea_agent.analysis_scenarios import frozen_scene
    scene = frozen_scene(run_directory(state), contract)
    from pangea_agent.flow_stability import publish_flow_baseline
    baseline = None
    baseline_warning = None
    try:
        baseline = publish_flow_baseline(state, progress, records)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Optional comparison metadata must not discard valid Agent delivery.
        baseline_warning = str(exc)
    diagram_assets: dict[str, str] = {}
    markdown = (
        _behavior_markdown(state, progress, records, diagram_assets, scene=scene)
        if profile in {"behavior-test-v1", "behavior-test-v2"}
        else _markdown(state, progress, records)
    )
    if baseline_warning:
        markdown += "\n> 流程基线暂未发布（已有分析结果保留）：" + _markdown_cell(baseline_warning) + "\n"
    review_mode = (contract.get("analysis_settings") or {}).get("mode", "depth")
    incremental = contract.get("incremental_request")
    if incremental:
        label = "定向补充" if incremental["mode"] == "supplement" else "文件变更分析"
        source = "父 Run 冻结源码" if incremental["mode"] == "supplement" else "本次冻结的当前仓库源码"
        title, separator, rest = markdown.partition("\n")
        markdown = (title + separator + f"\n本次为{label}，父 Run：`{incremental['parent_run_id']}`。源码基线：{source}。"
                    "父记录仅供参考，不计为本次已接受结果；本报告仅覆盖本次目标。历史覆盖率不是本次执行测量。\n" + rest)
    if profile in {"behavior-test-v1", "behavior-test-v2"}:
        review_label = "速度型：直接审核首轮结果，未执行独立盲审" if review_mode == "speed" else "标准型：独立盲审后对照复核"
        title, separator, rest = markdown.partition("\n")
        markdown = title + separator + "\n复核模式：" + review_label + "。执行完成情况以本报告状态为准。\n" + rest
    for relative_path, content in diagram_assets.items():
        _atomic_text(run_directory(state) / relative_path, content)
    _atomic_text(markdown_path, markdown)
    _atomic_text(html_path, _html(markdown, diagram_assets))
    _atomic_text(
        run_directory(state) / "report-complete.json",
        json.dumps(
            {
                "format_version": "pangea-report-complete-v1",
                "run_id": progress.get("run_id", state.get("run_id")),
                "lifecycle_status": progress.get("lifecycle_status"),
                "quality_status": progress.get("quality_status"),
                "analysis_profile": profile,
                "review_mode": review_mode,
                "partial_delivery": progress.get("partial_delivery", False),
                "flow_baseline": {key: baseline.get(key) for key in ("run_id", "version_id", "flow_count", "eligible")} if baseline else None,
                "flow_baseline_warning": baseline_warning,
                "first_finish_revisions": progress.get("first_finish_revisions", {}),
                "accepted_revisions": progress.get("accepted_revisions", {}),
                "files": ["report.md", "report.html", *sorted(diagram_assets)],
            },
            ensure_ascii=False,
        ) + "\n",
    )
    return {"report_path": str(markdown_path), "html_report_path": str(html_path)}
