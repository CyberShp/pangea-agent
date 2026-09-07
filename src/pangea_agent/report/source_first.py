"""Report assembly for source-first notes.

The renderer presents the Agent's original records and workflow facts.  It
does not turn prose into risks, tests, or a quality verdict.
"""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json
from pangea_agent.graph.result_store import read_result, supersession_map
from pangea_agent.graph.workflow_store import run_directory
from pangea_agent.report.flow_diagram import render_flow_svg


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
    "developer_assisted": "开发协助",
    "whitebox_support": "白盒辅助",
}

_CASE_PURPOSES = {
    "branch": "分支用例",
    "coverage": "未覆盖用例",
    "risk": "风险用例",
}

_FIELD_LABELS = {
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
}


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


def _markdown_list(value: Any, empty: str = "无") -> list[str]:
    values = _items(value)
    if not values:
        return [f"- {empty}"]
    return [f"- {_body_text(item).replace(chr(10), ' ')}" for item in values]


def _markdown_cell(value: Any) -> str:
    return str(value if value not in (None, "") else "未提供").replace("|", "\\|")


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


def _record_title(record: Any) -> str:
    body = _body_mapping(record.body)
    if body is not None:
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
    if record.kind == "summary":
        return "分析说明"
    text = _body_text(record.body).strip().splitlines()[0] if record.body is not None else ""
    if text:
        return text[:100] + ("…" if len(text) > 100 else "")
    return f"`{record.kind}`"


def _record_anchor(unit_id: str, record_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"record-{unit_id}-{record_id}")
    return value.strip("-") or "record"


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
    label = f"{identifier}{suffix}" if identifier else _body_text(value).replace(chr(10), " ")
    return f"[{label}](#{anchor})" if anchor else label


def _mapping_markdown(body: dict[str, Any]) -> str:
    title = str(body.get("title") or "")
    lines: list[str] = []
    for key, value in body.items():
        if key == "title" or (key in {"risk_id", "unresolved_id", "note_id"} and title):
            continue
        label = _FIELD_LABELS.get(key, key.replace("_", " "))
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
) -> str:
    if record.kind in {"test_case", "test_case_group"}:
        return _case_markdown(record, unit_id=unit_id, anchors=anchors or {})
    mapping = _body_mapping(record.body)
    return _mapping_markdown(mapping) if mapping is not None else _nested_record_text(record.body)


def _flow_markdown(
    record: Any,
    asset_path: str,
    diagram_assets: dict[str, str],
    *,
    unit_id: str,
    anchors: dict[tuple[str, str, str], str],
) -> str:
    body = _body_mapping(record.body)
    if body is None or body.get("format_version") != "behavior-flow-v1":
        return _record_markdown(record, unit_id=unit_id, anchors=anchors)
    svg, warnings = render_flow_svg(body, asset_path)
    if svg:
        diagram_assets[asset_path] = svg
    lines = []
    description = str(body.get("description") or "")
    if description:
        lines.extend([description, ""])
    if svg:
        lines.extend([f"![{body.get('title') or '模块行为流程图'}]({asset_path})", ""])
    for warning in warnings:
        lines.append(f"> 图形提示：{warning}")
    if warnings:
        lines.append("")
    paths = [item for item in _items(body.get("paths")) if isinstance(item, dict)]
    if paths:
        lines.extend([
            "#### 流程路径说明",
            "",
            "| 路径 | 条件与准备 | 处理、结果及恢复 | 对应用例 |",
            "|---|---|---|---|",
        ])
        for path in paths:
            path_title = path.get("title") or path.get("path_id") or "未命名路径"
            cases = "、".join(
                _reference_markdown(
                    item,
                    current_unit=unit_id,
                    identifier_key="case_id",
                    anchors=anchors,
                )
                for item in _items(path.get("case_ids"))
            ) or "未关联"
            lines.append(
                f"| {_markdown_cell(path_title)} | {_markdown_cell(path.get('condition'))} | "
                f"{_markdown_cell(path.get('explanation'))} | {_markdown_cell(cases)} |"
            )
    evidence = _items(body.get("source_evidence"))
    if evidence:
        lines.extend(["", "#### 源码证据", "", *_markdown_list(evidence)])
    return "\n".join(lines).rstrip()


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
        f"- **执行状态**：`{body.get('execution_status') or '未声明'}`",
        "",
        "##### 前置条件",
        "",
        *_markdown_list(body.get("preconditions"), "未提供"),
        "",
        "##### 操作与预期",
        "",
    ]
    steps = [item for item in _items(body.get("steps")) if isinstance(item, dict)]
    if steps:
        lines.extend([
            "| # | 测试人员动作 | 对应预期 |",
            "|---:|---|---|",
            *[
                f"| {index} | {_markdown_cell(step.get('action'))} | "
                f"{_markdown_cell(step.get('expected'))} |"
                for index, step in enumerate(steps, 1)
            ],
        ])
    else:
        lines.append("- 未提供可执行步骤。")
    variants = [item for item in _items(body.get("variants")) if isinstance(item, dict)]
    if variants:
        lines.extend([
            "",
            "##### 输入变体",
            "",
            "| 输入或条件 | 预期 |",
            "|---|---|",
            *[
                f"| {_markdown_cell(item.get('input'))} | "
                f"{_markdown_cell(item.get('expected'))} |"
                for item in variants
            ],
        ])
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
                _body_text(record.body),
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
                _body_text(record.body),
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
) -> str:
    if diagram_assets is None:
        diagram_assets = {}
    lifecycle = str(progress.get("lifecycle_status", "running"))
    quality = str(progress.get("quality_status") or "UNRESOLVED")
    if lifecycle == "complete" and quality == "PASS":
        delivery_message = "用例分析与审核已完成；该状态不代表被测系统已经执行通过。"
    elif lifecycle == "complete":
        delivery_message = (
            "分析流程已经结束；当前冻结范围内的用例可以使用，但仍有待确认事项，"
            "整体质量状态保持 UNRESOLVED。"
        )
    else:
        delivery_message = "分析仍在进行，当前报告不是最终交付。"
    lines = [
        "# PANGEA 业务行为测试用例报告",
        "",
        f"- Run: `{progress.get('run_id', state.get('run_id', ''))}`",
        "- Analysis profile: `behavior-test-v1`",
        f"- Lifecycle: `{lifecycle}`",
        f"- Quality: `{quality}`",
        "- 质量范围：业务行为用例的生成与审核；未附实际执行证据时，不代表被测产品或 Coverage 已通过。",
        "",
        "## 交付状态",
        "",
        f"> {delivery_message}",
        "",
    ]
    accepted_closure_units = {
        str(action.get("task", {}).get("unit_id"))
        for action, _result in records
        if action.get("stage") == "targeted_closure" and action.get("status") == "accepted"
    }
    delivery: list[tuple[dict, Any]] = []
    for action, result in records:
        stage = action.get("stage")
        unit_id = str(action.get("task", {}).get("unit_id"))
        if stage == "targeted_closure" and action.get("status") == "accepted":
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
                anchors[(unit_id, identifier_key, identifier)] = anchor

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
    ) -> None:
        lines.extend([f"## {title}", ""])
        found = False
        for action, result in selected:
            active, _retired, _retired_by = active_and_retired(result)
            chosen = [
                record for record in active
                if (kinds is None or record.kind in kinds)
                and (levels is None or _case_level(record) in levels)
                and (purposes is None or _case_purpose(record) in purposes)
            ]
            for record in chosen:
                found = True
                unit_id = str(action.get("task", {}).get("unit_id") or "unit")
                slug = re.sub(
                    r"[^A-Za-z0-9_.-]+",
                    "-",
                    f"{unit_id}-{record.record_id}",
                ).strip("-")
                body = (
                    _flow_markdown(
                        record,
                        f"report-assets/{slug or 'flow'}.svg",
                        diagram_assets,
                        unit_id=unit_id,
                        anchors=anchors,
                    )
                    if record.kind == "flow"
                    else _record_markdown(record, unit_id=unit_id, anchors=anchors)
                )
                anchor = _record_anchor(unit_id, record.record_id)
                lines.extend([
                    f"### `{unit_id}` · {_record_title(record)} <!--pangea-record:{anchor}-->",
                    "",
                    body,
                    "",
                ])
                if record.evidence:
                    lines.extend(["证据：", "", _body_text(record.evidence), ""])
                if record.relates_to:
                    lines.extend(["关联：", "", _body_text(record.relates_to), ""])
        if not found:
            lines.extend(["- 当前没有此类有效记录。", ""])

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
    formal_case_records = [
        record for record in case_records
        if _case_level(record) in {"business_blackbox", "developer_assisted"}
    ]
    purpose_counts = {
        purpose: sum(_case_purpose(record) == purpose for record in formal_case_records)
        for purpose in [*_CASE_PURPOSES, "unclassified"]
    }
    lines.extend([
        "## 用例总览",
        "",
        "以下数量来自 Analysis 的显式声明；Python 不根据函数名或正文猜测测试目的和层级。",
        "",
        f"- 分支用例：`{purpose_counts['branch']}`",
        f"- 未覆盖用例：`{purpose_counts['coverage']}`",
        f"- 风险用例：`{purpose_counts['risk']}`",
        f"- 测试目的未声明：`{purpose_counts['unclassified']}`",
        f"- 产品黑盒：`{level_counts['business_blackbox']}`",
        f"- 产品行为（需要开发准备环境）：`{level_counts['developer_assisted']}`",
        f"- 开发辅助附录：`{level_counts['interface_contract'] + level_counts['whitebox_support'] + level_counts['unclassified']}`",
        "",
    ])
    add_records(
        "模块流程说明",
        delivery,
        {"flow", "branch", "scenario"},
    )
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
    add_records("风险依据", delivery, {"risk"})
    add_records("Coverage 与分析依据", delivery, {"evidence", "blackbox_translation"})
    add_records("待确认事项", delivery, {"unresolved"})
    add_records("其他交付说明", delivery, {"summary", "note"})
    if progress.get("blocking_reason"):
        lines.extend(["## Attention", "", _body_text(progress["blocking_reason"]), ""])
    return "\n".join(lines).rstrip() + "\n"


_NAV_TITLES = {
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
    "Revision ledger",
    "Review binding",
    "Agent records",
    "Attention",
    "Deterministic diagnostics",
}


def _inline_html(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(
        r"\[([^\]]+)\]\((#[A-Za-z0-9_.-]+)\)",
        r'<a href="\2">\1</a>',
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)


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
    in_list = False
    in_detail = False
    index = 0

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            chunks.append("</ul>")
            in_list = False

    def close_detail() -> None:
        nonlocal in_detail
        close_list()
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
        if line.startswith("```"):
            close_list()
            language = line[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
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
                chunks.append(
                    f'<details{id_attr} class="record"><summary>{_inline_html(title)}</summary><div class="record-body">'
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
            if not in_list:
                chunks.append("<ul>")
                in_list = True
            chunks.append(f"<li>{_inline_html(stripped[2:])}</li>")
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
        '.flow-svg{overflow-x:auto;margin:14px 0 20px;padding:14px;border:1px solid var(--line);'
        'border-radius:10px;background:#fbfcfe}.flow-svg svg{display:block;width:100%;min-width:680px}'
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
        'if(!id)return;const target=document.getElementById(id);if(target&&target.tagName==="DETAILS")'
        '{target.open=true;}}addEventListener("hashchange",openTarget);openTarget();</script></body></html>'
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
                profile = frozen_contract.get("analysis_profile")
    diagram_assets: dict[str, str] = {}
    markdown = (
        _behavior_markdown(state, progress, records, diagram_assets)
        if profile == "behavior-test-v1"
        else _markdown(state, progress, records)
    )
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
                "first_finish_revisions": progress.get("first_finish_revisions", {}),
                "accepted_revisions": progress.get("accepted_revisions", {}),
                "files": ["report.md", "report.html", *sorted(diagram_assets)],
            },
            ensure_ascii=False,
        ) + "\n",
    )
    return {"report_path": str(markdown_path), "html_report_path": str(html_path)}
