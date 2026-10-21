"""Readable report projections of Agent-authored sequence and call-graph facts.

These are display-only extensions of a flow record. Missing or malformed data
never rejects the flow or invents relationships. The Desktop owns SVG layout;
the portable report keeps the declared facts, conditions, evidence and raw data.
"""

from __future__ import annotations

import json
from typing import Any


_STATUSES = {
    "ready": "Agent 已声明就绪（未表示已实测）",
    "partial": "部分数据，仍有待确认内容",
    "not_applicable": "Agent 声明不适用",
    "unknown": "待确认",
}
_MESSAGE_KINDS = {"call": "请求 / 调用", "return": "返回 / 响应", "async": "异步通知", "self": "内部动作"}
_FRAGMENT_KINDS = {"alt": "互斥分支", "opt": "可选条件", "loop": "循环", "par": "并发片段"}


def _text(value: Any) -> str:
    if value is None:
        return "—"
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _cell(value: Any) -> str:
    # The report HTML renderer escapes cell text once. Keep source expressions
    # readable in Markdown (for example, req->user must not become &amp;gt;).
    return _text(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["未提供可结构化条目；不从业务流程节点推测。", ""]
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers),
            *["| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows], ""]


def _raw(value: Any) -> list[str]:
    return ["<details><summary>Agent 原始结构化数据</summary>", "", "```json",
            json.dumps(value, ensure_ascii=False, indent=2), "```", "", "</details>", ""]


def _intro(value: Any, version: str) -> tuple[list[str], bool]:
    if value is None:
        return ["本条流程尚未提供这类结构化数据；旧记录继续保留，不自动推导或启动生图任务。", ""], False
    if not isinstance(value, dict):
        return ["数据无法按对象展示，原文保留；由原分析 Agent 核实。", "", *_raw(value)], False
    status = value.get("status")
    lines = [f"状态：{_cell(_STATUSES.get(status) if isinstance(status, str) and status in _STATUSES else status or '未标注完整性')}。", ""]
    if value.get("format_version") != version:
        lines += [f"> 格式提示：预期 {_cell(version)}，实际 {_cell(value.get('format_version'))}；以下按已提供字段展示，不代表通过校验。", ""]
    notes = value.get("notes")
    if notes:
        lines += ["**范围与待确认事项**", "", *[f"- {_cell(note)}" for note in (_array(notes) or [notes])], ""]
    return lines, True


def _sequence(value: Any) -> list[str]:
    lines, usable = _intro(value, "sequence-v1")
    if not usable:
        return lines
    if value.get("title"):
        lines += [f"标题：{_cell(value['title'])}", ""]
    participants = _array(value.get("participants"))
    lines += ["**参与者**", "", *_table(["ID", "名称", "职责"],
        [[row.get("id"), row.get("label"), row.get("description")] for row in participants if isinstance(row, dict)])]
    known = {row.get("id") for row in participants if isinstance(row, dict) and isinstance(row.get("id"), str)}
    messages = _array(value.get("messages"))
    message_ids = {row.get("id") for row in messages if isinstance(row, dict) and isinstance(row.get("id"), str)}
    rows = []
    warnings = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            warnings.append(f"messages[{index}] 不是对象，见原始数据。")
            continue
        kind = message.get("kind")
        rows.append([message.get("id"), message.get("from"), message.get("to"),
                     _MESSAGE_KINDS.get(kind, kind) if isinstance(kind, str) else kind,
                     message.get("label"), message.get("source_evidence")])
        for endpoint in ("from", "to"):
            ref = message.get(endpoint)
            if not isinstance(ref, str) or ref not in known:
                warnings.append(f"messages[{index}].{endpoint}={_text(ref)} 未对应已声明参与者，原引用保留。")
    lines += ["**交互消息**", "", "顺序按 Agent 声明；互斥分支不是同一次连续执行，并发片段不表示确定的全局先后。", "",
              *_table(["消息 ID", "发送方", "接收方", "类型", "消息 / 动作", "源码依据"], rows)]
    fragments = []
    for index, fragment in enumerate(_array(value.get("fragments"))):
        if not isinstance(fragment, dict):
            warnings.append(f"fragments[{index}] 不是对象，见原始数据。")
            continue
        kind = fragment.get("kind")
        operands = _array(fragment.get("operands"))
        for operand in operands:
            if not isinstance(operand, dict):
                warnings.append(f"fragments[{index}] 含无法读取的分支，见原始数据。")
                continue
            fragments.append([fragment.get("id"), _FRAGMENT_KINDS.get(kind, kind) if isinstance(kind, str) else kind,
                              fragment.get("label"), operand.get("condition"), operand.get("message_ids")])
            for ref in _array(operand.get("message_ids")):
                if not isinstance(ref, str) or ref not in message_ids:
                    warnings.append(f"fragments[{index}] 引用了未声明消息 {_text(ref)}，原引用保留。")
    if value.get("fragments"):
        lines += ["**条件、循环与并发**", "", *_table(["片段 ID", "类型", "说明", "条件", "消息 ID"], fragments)]
    if warnings:
        lines += ["**显示提示**", "", *[f"- {_cell(warning)}" for warning in warnings], ""]
    return [*lines, *_raw(value)]


def _call_graph(value: Any) -> list[str]:
    lines, usable = _intro(value, "call-graph-v1")
    if not usable:
        return lines
    if value.get("title"):
        lines += [f"标题：{_cell(value['title'])}", ""]
    lines += [f"Agent 声明的入口：{_cell(value.get('root'))}。以下为静态源码关系，不表示已验证运行轨迹。", ""]
    functions = _array(value.get("functions"))
    known = {row.get("id") for row in functions if isinstance(row, dict) and isinstance(row.get("id"), str)}
    lines += ["**函数与宏**", "", *_table(["ID", "类别", "名称", "签名", "源码文件", "行号"],
        [[row.get(key) for key in ("id", "kind", "label", "signature", "file", "line")]
         for row in functions if isinstance(row, dict)])]
    calls = _array(value.get("calls"))
    variables = _array(value.get("variables"))
    lines += ["**已声明调用点**", "", *_table(["调用方", "被调用方", "参数映射", "返回值接收", "文件", "行号"],
        [[row.get(key) for key in ("from", "to", "args", "returns", "file", "line")]
         for row in calls if isinstance(row, dict)])]
    lines += ["**变量读取、计算与写入**", "", *_table(["所属函数", "变量", "读取", "计算", "写入", "行号"],
        [[row.get(key) for key in ("function", "name", "reads", "compute", "writes", "line")]
         for row in variables if isinstance(row, dict)])]
    warnings = []
    for collection, keys in (("calls", ("from", "to")), ("variables", ("function",))):
        for index, row in enumerate(_array(value.get(collection))):
            if not isinstance(row, dict):
                warnings.append(f"{collection}[{index}] 不是对象，见原始数据。")
                continue
            for key in keys:
                ref = row.get(key)
                if not isinstance(ref, str) or ref not in known:
                    warnings.append(f"{collection}[{index}].{key}={_text(ref)} 未对应已声明函数，原引用保留。")
    if warnings:
        lines += ["**显示提示**", "", *[f"- {_cell(warning)}" for warning in warnings], ""]
    return [*lines, *_raw(value)]


def native_diagram_sections(flow: dict[str, Any]) -> list[str]:
    """Keep optional facts visible without adding a result-write contract."""
    return ["**时序交互**", "", *_sequence(flow.get("sequence")),
            "**函数与变量关系**", "", *_call_graph(flow.get("call_graph"))]
