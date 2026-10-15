"""Present declared business coverage without judging its completeness."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Callable

FORMAT = "behavior-coverage-v1"
STATUSES = {"designed": "已设计", "missing": "未设计", "unresolved": "待确认", "not_applicable": "不适用"}


def coverage_body(record: Any) -> dict[str, Any] | None:
    if record.kind != "note":
        return None
    value = record.body
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) and value.get("format_version") == FORMAT else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _cell(value: Any) -> str:
    return _text(value if value not in (None, "") else "未提供").replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _values(value: Any) -> str:
    return "；".join(_cell(item) for item in value) if isinstance(value, list) else _cell(value)


def coverage_markdown(body: dict[str, Any], reference: Callable[[Any, str], str]) -> str:
    """Unknown/malformed values remain visible; this renderer is not a gate."""
    raw_items = body.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    counts = Counter(str(item.get("status")) for item in items if isinstance(item, dict))
    unknown = sum(count for status, count in counts.items() if status not in STATUSES)
    lines = [
        f"- **业务范围**：{_cell(body.get('scope'))}",
        f"- **声明单元**：{_cell(body.get('unit_id'))}",
        f"- **组合策略与取舍**：{_cell(body.get('combination_strategy'))}",
        f"- **未解决依赖**：{_values(body.get('unresolved_dependencies', [])) or '未声明'}",
        "- **作者声明数量**：" + "／".join(f"{label} {counts[status]}" for status, label in STATUSES.items())
        + f"／未知状态 {unknown}／无法结构化条目 {sum(not isinstance(item, dict) for item in items)}。",
        "> 数量仅汇总当前有效记录中的显式声明，不能据此证明场景已找全、用例可执行或测试已通过；执行条件见对应用例。",
        "",
        "| 检查项 | 业务操作与条件 | 约束 | 预期业务行为 | 声明状态与原因 | 用例／变体 | 源码依据／流程 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            lines.append(f"| 原始条目 {index} | {_cell(item)} | — | — | 无法结构化，原文保留 | — | — |")
            continue
        refs = item.get("case_refs", [])
        cases = "；".join(reference(ref, "case_id") for ref in refs) if isinstance(refs, list) else _cell(refs)
        flow_refs = item.get("flow_refs", [])
        flows = "；".join(reference(ref, "flow_id") for ref in flow_refs) if isinstance(flow_refs, list) else _cell(flow_refs)
        status = item.get("status")
        label = STATUSES.get(str(status), f"未知声明：{_cell(status)}")
        condition = f"{_cell(item.get('operation'))}；{_values(item.get('conditions', []))}"
        evidence = _values(item.get("source_evidence", []))
        lines.append(
            f"| {_cell(item.get('item_id'))} | {condition} | {_values(item.get('constraints', [])) or '未声明'} | "
            f"{_cell(item.get('expected_behavior'))} | {label}；{_cell(item.get('reason'))} | "
            f"{_cell(cases) if cases else '未关联'} | {evidence or '未提供'}" + (f"；{_cell(flows)}" if flows else "") + " |"
        )
    if not isinstance(raw_items, list):
        lines.extend(["", "> 检查项列表未提供或格式无法结构化；不能据此判定没有应测情况。"])
    # Keep all extensions and malformed fields, including nested ones. Never silently
    # discard data just because this version of the renderer does not recognize it.
    raw = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)
    fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", raw)), default=0) + 1)
    lines.extend(["", "<details><summary>业务覆盖清单原始声明（含扩展字段）</summary>", "", fence + "json", raw, fence, "", "</details>"])
    return "\n".join(lines)
