"""“模块流程说明”章节：总览 → 每个流程（概述、主线、分支、路径、图、依据）。

所有 kind=flow 记录先经 ``flow_schema.normalize_flow`` 做键名/外壳兼容，再按
``role``/``attach_to`` 排版。这里只排版 Agent 已声明的节点和连线，不推断语义、
不补造节点；无法结构化的记录保留原文并列出结构问题。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from pangea_agent.flow_schema import TEXT_FLOW_FORMAT, check_flow_structure, normalize_flow
from pangea_agent.flow_stability import canonical_flow
from pangea_agent.report.flow_diagram import render_flow_svg

FLOW_SECTION_KINDS = {"flow", "branch", "scenario"}
_KIND_LABELS = {
    "entry": "入口",
    "main": "主线",
    "branch": "分支",
    "error": "异常",
    "propagation": "传播",
    "recovery": "恢复",
    "exit": "结束",
}
_MAIN_KINDS = {"entry", "main", "exit"}
_SUMMARY_KEYS = ("condition", "description", "expected", "result", "content", "text", "summary", "explanation")
_SUMMARY_LIMIT = 300

ReferenceFn = Callable[[Any, str, str], str]


@dataclass
class _FlowEntry:
    unit_id: str
    record: Any
    flow: dict[str, Any]
    anchor: str
    notes: list[str]
    asset_slug: str = "flow"
    linked: list[tuple[str, Any]] = field(default_factory=list)

    @property
    def flow_id(self) -> str:
        return str(self.flow.get("flow_id") or self.record.record_id)

    @property
    def title(self) -> str:
        return str(self.flow.get("title") or self.flow_id)

    @property
    def is_main(self) -> bool:
        return self.flow.get("role") == "main"

    @property
    def attach(self) -> dict[str, Any] | None:
        value = self.flow.get("attach_to")
        return value if isinstance(value, dict) and value.get("flow_id") else None


@dataclass
class _Unstructured:
    unit_id: str
    record: Any
    anchor: str
    text: str | None
    problems: list[str]


def _cell(value: Any) -> str:
    text = str(value if value not in (None, "") else "—").replace("\n", " ")
    return text.replace("|", "\\|")


def _one_line(value: Any) -> str:
    return str(value or "").replace("\n", " ").strip()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _anchor(unit_id: str, record_id: str, suffix: str = "") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"record-{unit_id}-{record_id}{suffix}")
    return value.strip("-") or "record"


def _slug(unit_id: str, record_id: str, suffix: str = "") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{unit_id}-{record_id}{suffix}").strip("-")
    return value or "flow"


def _body_mapping(body: Any) -> dict[str, Any] | None:
    if isinstance(body, dict):
        return body
    if isinstance(body, str) and body.lstrip().startswith("{"):
        try:
            value = json.loads(body)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


def record_summary(record: Any) -> str:
    """一行可读摘要；不输出 JSON。"""
    mapping = _body_mapping(record.body)
    if mapping is None:
        text = _one_line(record.body if isinstance(record.body, str) else _text(record.body))
    else:
        parts = [_one_line(mapping[key]) for key in _SUMMARY_KEYS if isinstance(mapping.get(key), str) and mapping[key].strip()]
        if not parts:
            parts = [
                _one_line(value) for key, value in mapping.items()
                if isinstance(value, str) and value.strip() and key not in {"format_version", "title"}
            ]
        text = "；".join(parts)
    if len(text) > _SUMMARY_LIMIT:
        text = text[:_SUMMARY_LIMIT] + "…"
    return text or "（无文字说明）"


def record_title(record: Any) -> str:
    mapping = _body_mapping(record.body) or {}
    for key in ("title", "name", "标题"):
        if isinstance(mapping.get(key), str) and mapping[key].strip():
            return mapping[key].strip()
    if isinstance(record.body, str) and record.body.strip():
        first = record.body.strip().splitlines()[0]
        return first[:60] + ("…" if len(first) > 60 else "")
    return {"branch": "分支说明", "scenario": "场景说明"}.get(record.kind, record.record_id)


def _collect(
    delivery: list[tuple[str, list[Any]]],
) -> tuple[list[_FlowEntry], list[_Unstructured], list[tuple[str, Any]]]:
    flows: list[_FlowEntry] = []
    broken: list[_Unstructured] = []
    others: list[tuple[str, Any]] = []
    for unit_id, records in delivery:
        for record in records:
            if record.kind != "flow":
                if record.kind in FLOW_SECTION_KINDS:
                    others.append((unit_id, record))
                continue
            anchor = _anchor(unit_id, record.record_id)
            mapping = _body_mapping(record.body)
            if mapping is not None and mapping.get("format_version") == TEXT_FLOW_FORMAT:
                broken.append(_Unstructured(unit_id, record, anchor, str(mapping.get("text") or ""), []))
                continue
            normalized, notes = normalize_flow(record.body)
            if not normalized:
                problems = check_flow_structure(record.body) or notes or ["没有可识别的节点"]
                broken.append(_Unstructured(unit_id, record, anchor, None, problems))
                continue
            for index, flow in enumerate(normalized):
                suffix = "" if index == 0 else f"-{index + 1}"
                flows.append(_FlowEntry(
                    unit_id, record, canonical_flow(flow), _anchor(unit_id, record.record_id, suffix), list(notes),
                    asset_slug=_slug(unit_id, record.record_id, suffix),
                ))
    return flows, broken, others


def _main_path(flow: dict[str, Any]) -> list[str]:
    nodes = flow.get("nodes", [])
    known = [node["id"] for node in nodes]
    paths = flow.get("paths", [])
    if flow.get("layout_version") == "stable-v1":
        groups = _declared_main_paths(flow)
        return list(groups[0].get("node_ids", [])) if groups else []
    refs = paths[0].get("node_ids", []) if paths else []
    if refs and all(ref in known for ref in refs):
        return refs
    selected = [node["id"] for node in nodes if node.get("kind") in _MAIN_KINDS]
    chosen = set(selected)
    incoming = {node_id: 0 for node_id in selected}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in selected}
    for edge in flow.get("edges", []):
        source, target = edge.get("source_step_key"), edge.get("target_step_key")
        if source in chosen and target in chosen and source != target:
            outgoing[source].append(target)
            incoming[target] += 1
    order: list[str] = []
    ready = [node_id for node_id in selected if incoming[node_id] == 0]
    while ready:
        current = ready.pop(0)
        order.append(current)
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    order.extend(node_id for node_id in selected if node_id not in order)
    return order


def _declared_main_paths(flow: dict[str, Any]) -> list[dict[str, Any]]:
    """Main paths are explicit author claims, never a concatenated sequence."""
    declared = flow.get("main_path_ids", [])
    wanted = {str(value) for value in declared} if isinstance(declared, list) else set()
    return [path for path in flow.get("paths", []) if isinstance(path, dict) and str(path.get("path_id")) in wanted]


def _find_parent(entries: list[_FlowEntry], entry: _FlowEntry) -> _FlowEntry | None:
    attach = entry.attach
    if attach is None:
        return None
    target = str(attach["flow_id"])
    candidates = [item for item in entries if item is not entry and item.flow_id == target]
    same_unit = [item for item in candidates if item.unit_id == entry.unit_id]
    return (same_unit or candidates or [None])[0]


def _order(entries: list[_FlowEntry]) -> list[_FlowEntry]:
    mains = [item for item in entries if item.is_main]
    rest = [item for item in entries if not item.is_main]
    position = {id(item): index for index, item in enumerate(mains + rest)}

    def key(item: _FlowEntry) -> tuple[int, int, int]:
        parent = _find_parent(entries, item)
        if parent is None:
            return (1, 0, position[id(item)])
        path = _main_path(parent.flow)
        node_id = str(item.attach.get("node_id") or "") if item.attach else ""
        node_pos = path.index(node_id) if node_id in path else len(path)
        return (0, position[id(parent)] * 10000 + node_pos, position[id(item)])

    return mains + sorted(rest, key=key)


def _links_to(record: Any, entry: _FlowEntry) -> bool:
    refs = list(_list(record.relates_to))
    mapping = _body_mapping(record.body) or {}
    refs.extend(_list(mapping.get("flow_refs")))
    if mapping.get("flow_id"):
        refs.append({"flow_id": mapping["flow_id"]})
    for ref in refs:
        if isinstance(ref, str):
            if ref == entry.record.record_id or ref == entry.flow_id:
                return True
        elif isinstance(ref, dict):
            ref_unit = str(ref.get("unit_id") or ref.get("unit") or "")
            if ref_unit and ref_unit != entry.unit_id:
                continue
            if ref.get("record_id") == entry.record.record_id or ref.get("flow_id") == entry.flow_id:
                return True
    return False


def _node_label(flow: dict[str, Any], node_id: Any) -> str:
    for node in flow.get("nodes", []):
        if node["id"] == node_id:
            return str(node.get("label") or node_id)
    return str(node_id or "")


def _overview(ordered: list[_FlowEntry], broken: list[_Unstructured], entries: list[_FlowEntry]) -> list[str]:
    lines = ["### 业务流程总览", ""]
    mains = [item for item in ordered if item.is_main]
    subs = [item for item in ordered if not item.is_main]
    if mains:
        for item in mains:
            summary = _one_line(item.flow.get("description"))
            lines.append(
                f"- 主流程：[{item.flow_id} {item.title}](#{item.anchor})（owner：`{item.unit_id}` 单元）"
                + (f"—— {summary}" if summary else "")
            )
    else:
        lines.append("- 主流程：未声明（各流程按子流程列出）")
    if subs:
        lines.append("- 子流程：")
        for item in subs:
            parent = _find_parent(entries, item)
            if item.attach is None:
                where = "未挂接"
            else:
                node_label = _node_label(parent.flow, item.attach.get("node_id")) if parent else str(item.attach.get("node_id") or "")
                where = f"挂接：{item.attach['flow_id']} / {node_label}" + ("" if parent else "（未找到该流程）")
            lines.append(f"  - [{item.flow_id} {item.title}](#{item.anchor})（`{item.unit_id}` 单元，{where}）")
    if broken:
        lines.append("- 未能结构化的流程：")
        for item in broken:
            label = "文字流程" if item.text is not None else "结构不合格"
            lines.append(f"  - [`{item.unit_id}` · {record_title(item.record)}](#{item.anchor})（{label}，见下文原文）")
    lines.append("")
    return lines


def _branch_rows(flow: dict[str, Any], main: list[str], linked: list[tuple[str, Any]]) -> list[str]:
    main_pairs = set(zip(main, main[1:]))
    stable = flow.get("layout_version") == "stable-v1"
    main_edge_ids = {
        str(edge_id) for path in _declared_main_paths(flow)
        for edge_id in _list(path.get("edge_ids"))
    }
    nodes = {node["id"]: node for node in flow.get("nodes", [])}
    rows: list[str] = []
    for edge in flow.get("edges", []):
        source, target = edge.get("source_step_key"), edge.get("target_step_key")
        if (str(edge.get("edge_id") or "") in main_edge_ids if stable else (source, target) in main_pairs):
            continue
        target_node = nodes.get(target, {})
        kind = _KIND_LABELS.get(str(target_node.get("kind") or ""), "")
        destination = _node_label(flow, target) + (f"（{kind}）" if kind and kind != "主线" else "")
        handling = edge.get("description") or target_node.get("description")
        rows.append(
            f"| {_cell(_node_label(flow, source))} | {_cell(edge.get('condition'))} | "
            f"{_cell(handling)} | {_cell(destination)} |"
        )
    for unit_id, record in linked:
        body = _body_mapping(record.body) or {}

        def declared(*keys: str) -> str:
            return next((body[key].strip() for key in keys if isinstance(body.get(key), str) and body[key].strip()), "")

        source = declared("source_step_key", "from_step_id", "source", "from")
        target = declared("target_step_key", "to_step_id", "target", "to", "terminal_result")
        condition = declared("condition") or "未提供"
        handling = declared("description", "processing", "explanation", "content", "text")
        if not handling and isinstance(record.body, str) and _body_mapping(record.body) is None:
            handling = record.body.strip()
        identity = f"**{record_title(record)}**（`{unit_id}/{record.record_id}` · {record.kind}）"
        rows.append(
            f"| {_cell(_node_label(flow, source) if source else '未提供')} | {_cell(condition)} | "
            f"{_cell(identity)}：{_cell(handling or '未提供')} | {_cell(_node_label(flow, target) if target else '未提供')} |"
        )
    return rows


def _evidence_block(entry: _FlowEntry) -> list[str]:
    items: list[str] = []
    for node in entry.flow.get("nodes", []):
        for evidence in _list(node.get("source_evidence")):
            items.append(f"- {_one_line(node.get('label'))}：{_one_line(_text(evidence))}")
    for evidence in _list(entry.flow.get("source_evidence")):
        items.append(f"- 流程：{_one_line(_text(evidence))}")
    for evidence in _list(entry.record.evidence):
        items.append(f"- 记录证据：{_one_line(_text(evidence))}")
    for relation in _list(entry.record.relates_to):
        items.append(f"- 关联：{_one_line(_text(relation))}")
    for unit_id, record in entry.linked:
        label = f"{record_title(record)}（`{unit_id}/{record.record_id}`）"
        body = _body_mapping(record.body) or {}
        for evidence in [*_list(body.get("source_evidence")), *_list(record.evidence)]:
            items.append(f"- {label} 证据：{_one_line(_text(evidence))}")
        for relation in _list(record.relates_to):
            items.append(f"- {label} 关联：{_one_line(_text(relation))}")
    items.extend(f"- {note}" for note in entry.notes)
    if not items:
        return []
    return ["<details><summary>源码依据</summary>", "", *items, "", "</details>", ""]


def _flow_body(
    entry: _FlowEntry,
    diagram_assets: dict[str, str],
    reference: ReferenceFn,
) -> list[str]:
    flow = entry.flow
    lines: list[str] = []
    description = _one_line(flow.get("description"))
    lines.extend([f"**概述**：{description or '未提供'}", ""])
    main = _main_path(flow)
    nodes = {node["id"]: node for node in flow.get("nodes", [])}
    lines.extend(["**主线步骤**", ""])
    groups = _declared_main_paths(flow) if flow.get("layout_version") == "stable-v1" else [{"node_ids": main}]
    if not groups:
        lines.extend(["未声明可读取的主路径；以下保留各路径，不根据数组顺序推定主线。", ""])
    for group in groups:
        if group.get("path_id"):
            lines.extend([f"**{_one_line(group.get('title') or group['path_id'])}**（{_one_line(group['path_id'])}）", ""])
        for index, node_id in enumerate(group.get("node_ids", []), start=1):
            node = nodes.get(node_id, {})
            detail = _one_line(node.get("description"))
            lines.append(f"{index}. {_one_line(node.get('label') or node_id)}" + (f" —— {detail}" if detail else ""))
        lines.append("")
    lines.append("")
    rows = _branch_rows(flow, main, entry.linked)
    lines.extend(["**分支与异常**", ""])
    if rows:
        lines.extend(["| 从 | 条件 | 处理 | 去向 |", "|---|---|---|---|", *rows, ""])
    else:
        lines.extend(["主线之外未声明分支连线。", ""])
    paths = flow.get("paths", [])
    if paths:
        lines.extend(["**路径**", ""])
        for path in paths:
            name = " ".join(str(part) for part in (path.get("path_id"), path.get("title")) if part) or "未命名路径"
            sequence = " → ".join(_node_label(flow, ref) for ref in path.get("node_ids", []))
            condition = _one_line(path.get("condition"))
            cases = "、".join(reference(case, entry.unit_id, "case_id") for case in _list(path.get("case_ids")))
            text = f"- {name}：" + (f"{condition} → " if condition else "") + (sequence or "未声明节点")
            if cases:
                text += f"（对应用例：{cases}）"
            lines.append(text)
        lines.append("")
    asset_path = f"report-assets/{entry.asset_slug}.svg"
    svg, warnings = render_flow_svg(flow, asset_path)
    if svg:
        diagram_assets[asset_path] = svg
        lines.extend(["**流程图**", "", f"![{entry.title}]({asset_path})", ""])
    for warning in warnings:
        lines.append(f"> 图形提示：{warning}")
    if warnings:
        lines.append("")
    lines.extend(_evidence_block(entry))
    return lines


def _unstructured_body(item: _Unstructured) -> list[str]:
    if item.text is not None:
        lines = [item.text.strip() or "（文字流程为空）", ""]
        mapping = _body_mapping(item.record.body) or {}
        evidence = [*_list(mapping.get("source_evidence")), *_list(item.record.evidence)]
        if evidence:
            lines.extend(["<details><summary>源码依据</summary>", "", *[f"- {_one_line(_text(value))}" for value in evidence], "", "</details>", ""])
        return lines
    raw = item.record.body if isinstance(item.record.body, str) else json.dumps(item.record.body, ensure_ascii=False, indent=2)
    return [
        "> 该流程记录无法结构化，以下列出结构问题并保留原文，待原 worker 修正。",
        "",
        *[f"- {problem}" for problem in item.problems],
        "",
        "<details><summary>原文</summary>",
        "",
        "```text",
        *str(raw).replace("```", "'''").splitlines(),
        "```",
        "",
        "</details>",
        "",
    ]


def flow_section_markdown(
    delivery: list[tuple[str, list[Any]]],
    *,
    diagram_assets: dict[str, str],
    reference: ReferenceFn,
    other_record_markdown: Callable[[str, Any], list[str]],
) -> list[str]:
    """生成“模块流程说明”章节；delivery 为 (unit_id, 有效记录) 列表。"""
    entries, broken, others = _collect(delivery)
    if not entries and not broken and not others:
        return []
    unlinked: list[tuple[str, Any]] = []
    for unit_id, record in others:
        target = next((item for item in entries if item.unit_id == unit_id and _links_to(record, item)), None)
        target = target or next((item for item in entries if _links_to(record, item)), None)
        if target is None:
            unlinked.append((unit_id, record))
        else:
            target.linked.append((unit_id, record))
    ordered = _order(entries)
    lines = ["## 模块流程说明", ""]
    if entries or broken:
        lines.extend(_overview(ordered, broken, entries))
    for entry in ordered:
        badge = "〔主流程〕" if entry.is_main else "〔子流程〕"
        lines.extend([
            f"### `{entry.unit_id}` · {entry.flow_id} {entry.title} {badge} <!--pangea-record:{entry.anchor}-->",
            "",
            *_flow_body(entry, diagram_assets, reference),
        ])
    for item in broken:
        lines.extend([
            f"### `{item.unit_id}` · {record_title(item.record)} 〔未结构化〕 <!--pangea-record:{item.anchor}-->",
            "",
            *_unstructured_body(item),
        ])
    if unlinked:
        lines.extend(["### 其他场景说明", ""])
        for unit_id, record in unlinked:
            lines.extend(other_record_markdown(unit_id, record))
    return lines
