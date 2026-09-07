"""Deterministic SVG rendering for Agent-declared behavior flows."""

from __future__ import annotations

import html
import re
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any


_NODE_COLORS = {
    "entry": "#3157d5",
    "main": "#5b6b83",
    "branch": "#b47700",
    "error": "#c53d4d",
    "propagation": "#8c4fb3",
    "recovery": "#18875d",
    "exit": "#4a667a",
}


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _wrap_lines(value: Any, *, limit: int, max_lines: int) -> list[str]:
    text = str(value or "")
    tokens = re.findall(r"[A-Za-z0-9_./:=<>()|+\-]+|\s+|.", text)
    lines: list[str] = []
    current = ""
    current_width = 0
    for token in tokens:
        if token.isspace():
            if current and not current.endswith(" "):
                current += " "
                current_width += 1
            continue
        token_width = sum(1 if ord(char) < 128 else 2 for char in token)
        if current and token in "，。；：！？、）】》」』”’":
            current += token
            current_width += token_width
            continue
        if current and current_width + token_width > limit:
            lines.append(current.rstrip())
            if len(lines) == max_lines:
                return lines
            current = ""
            current_width = 0
        current += token
        current_width += token_width
    if current and len(lines) < max_lines:
        lines.append(current.rstrip())
    return lines


def _label_lines(value: Any) -> list[str]:
    return _wrap_lines(value or "未命名步骤", limit=30, max_lines=3)


def _edge_label_lines(value: Any) -> list[str]:
    return _wrap_lines(value, limit=30, max_lines=2)


def render_flow_svg(flow: Mapping[str, Any], diagram_id: str) -> tuple[str, list[str]]:
    """Render nodes and edges without deriving or changing their meaning."""

    warnings: list[str] = []
    nodes: list[Mapping[str, Any]] = []
    known: set[str] = set()
    for item in _items(flow.get("nodes")):
        if not isinstance(item, Mapping) or not str(item.get("id") or ""):
            warnings.append("存在缺少 id 的流程节点，已保留文字说明但未绘制该节点。")
            continue
        node_id = str(item["id"])
        if node_id in known:
            warnings.append(f"流程节点 {node_id} 重复，图中只显示第一次声明。")
            continue
        known.add(node_id)
        nodes.append(item)
    if not nodes:
        return "", warnings or ["当前流程没有可绘制节点。"]

    edges: list[Mapping[str, Any]] = []
    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for item in _items(flow.get("edges")):
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source_step_key") or "")
        target = str(item.get("target_step_key") or "")
        if source not in known or target not in known:
            warnings.append(
                f"流程连线 {source or '未提供'} → {target or '未提供'} 引用了不存在的节点，未绘制。"
            )
            continue
        edges.append(item)
        incoming[target] += 1
        outgoing[source].append(target)

    roots = [str(node["id"]) for node in nodes if incoming[str(node["id"])] == 0]
    if not roots:
        roots = [str(nodes[0]["id"])]
    depths = {node_id: 0 for node_id in roots}
    queue = deque(roots)
    while queue:
        source = queue.popleft()
        for target in outgoing[source]:
            candidate = depths[source] + 1
            if target not in depths:
                depths[target] = candidate
                queue.append(target)
    next_depth = max(depths.values(), default=-1) + 1
    for node in nodes:
        node_id = str(node["id"])
        if node_id not in depths:
            depths[node_id] = next_depth
            next_depth += 1

    levels: dict[int, list[str]] = defaultdict(list)
    for node in nodes:
        levels[depths[str(node["id"])]].append(str(node["id"]))
    box_width = 270
    box_height = 92
    column_gap = 54
    row_gap = 108
    margin_x = 60
    margin_y = 48
    max_columns = max(len(level) for level in levels.values())
    width = max(720, margin_x * 2 + max_columns * box_width + (max_columns - 1) * column_gap)
    height = margin_y * 2 + (max(levels) + 1) * box_height + max(levels) * row_gap
    positions: dict[str, tuple[float, float]] = {}
    for depth, level in levels.items():
        row_width = len(level) * box_width + (len(level) - 1) * column_gap
        start_x = (width - row_width) / 2
        y = margin_y + depth * (box_height + row_gap)
        for index, node_id in enumerate(level):
            positions[node_id] = (start_x + index * (box_width + column_gap), y)

    safe_diagram_id = "".join(char if char.isalnum() else "-" for char in diagram_id)
    edge_parts: list[str] = []
    for edge_index, edge in enumerate(edges):
        source_id = str(edge["source_step_key"])
        target_id = str(edge["target_step_key"])
        source_x, source_y = positions[source_id]
        target_x, target_y = positions[target_id]
        start_x = source_x + box_width / 2
        start_y = source_y + box_height
        end_x = target_x + box_width / 2
        end_y = target_y
        if depths[target_id] <= depths[source_id]:
            outside_x = width - 22 - edge_index * 6
            path = (
                f"M {start_x:.0f} {start_y:.0f} C {outside_x:.0f} {start_y + 30:.0f}, "
                f"{outside_x:.0f} {end_y - 30:.0f}, {end_x:.0f} {end_y:.0f}"
            )
        else:
            middle_y = (start_y + end_y) / 2
            path = (
                f"M {start_x:.0f} {start_y:.0f} C {start_x:.0f} {middle_y:.0f}, "
                f"{end_x:.0f} {middle_y:.0f}, {end_x:.0f} {end_y:.0f}"
            )
        condition = str(edge.get("condition") or "")
        label = ""
        if condition:
            if len(outgoing[source_id]) > 1 and abs(end_x - start_x) > 1:
                label_x = start_x + (end_x - start_x) * 0.68
            else:
                label_x = (start_x + end_x) / 2
            label_y = (start_y + end_y) / 2 - 10
            label_lines = _edge_label_lines(condition)
            tspans = "".join(
                f'<tspan x="{label_x:.0f}" dy="{0 if index == 0 else 15}">'
                f"{html.escape(line)}</tspan>"
                for index, line in enumerate(label_lines)
            )
            label = (
                f'<text x="{label_x:.0f}" y="{label_y:.0f}" text-anchor="middle" '
                f'class="flow-edge-label">{tspans}</text>'
            )
        edge_parts.append(
            f'<path d="{path}" class="flow-edge" marker-end="url(#{safe_diagram_id}-arrow)"/>{label}'
        )

    node_parts: list[str] = []
    for node in nodes:
        node_id = str(node["id"])
        x, y = positions[node_id]
        kind = str(node.get("kind") or "main")
        color = _NODE_COLORS.get(kind, _NODE_COLORS["main"])
        lines = _label_lines(node.get("label") or node_id)
        tspans = "".join(
            f'<tspan x="{x + 18:.0f}" dy="{0 if index == 0 else 20}">{html.escape(line)}</tspan>'
            for index, line in enumerate(lines)
        )
        node_parts.append(
            f'<g class="flow-node"><rect x="{x:.0f}" y="{y:.0f}" width="{box_width}" '
            f'height="{box_height}" rx="10" style="stroke:{color}"/>'
            f'<text x="{x + 18:.0f}" y="{y + 18:.0f}" class="flow-kind">{html.escape(kind)}</text>'
            f'<text x="{x + 18:.0f}" y="{y + 43:.0f}" class="flow-label">{tspans}</text></g>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{html.escape(str(flow.get("title") or "模块行为流程图"))}">'
        f'<defs><marker id="{safe_diagram_id}-arrow" markerWidth="9" markerHeight="9" '
        'refX="8" refY="4" orient="auto"><path d="M0,0 L0,8 L9,4 z" fill="#667085"/>'
        f'</marker></defs>{"".join(edge_parts)}{"".join(node_parts)}</svg>'
    )
    return svg, warnings
