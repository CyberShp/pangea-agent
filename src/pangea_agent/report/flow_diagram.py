"""Static, deterministic SVGs for Agent-declared behavior flows.

Short edge badges are repeated in a full condition legend: exported SVGs remain
unambiguous without JavaScript, hover, or colour perception.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any

from pangea_agent.flow_stability import canonical_flow
from pangea_agent.flow_schema import uses_declared_main_paths

_NODE_COLORS = {
    "entry": "#3157d5", "main": "#5b6b83", "branch": "#b47700",
    "error": "#c53d4d", "propagation": "#8c4fb3", "recovery": "#18875d", "exit": "#4a667a",
}


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _wrap_lines(value: Any, *, limit: int, max_lines: int | None = None) -> list[str]:
    # Wrap even a single long identifier and retain the complete label.
    # max_lines remains accepted for older callers but never hides author text.
    lines: list[str] = []
    current = ""
    width = 0
    for char in str(value or ""):
        if char == "\r":
            continue
        char_width = (0 if unicodedata.combining(char) else
                      2 if unicodedata.east_asian_width(char) in {"W", "F"} else
                      1.8 if char in "MW@#%&" else 1.4 if char in "mw" else 1)
        if char == "\n" or (current and width + char_width > limit):
            lines.append(current.rstrip())
            current, width = "", 0
            if char == "\n":
                continue
        current += char
        width += char_width
    if current:
        lines.append(current.rstrip())
    return lines or [""]


def _tspans(lines: list[str], x: float, line_height: int) -> str:
    return "".join(f'<tspan x="{x:.0f}" dy="{0 if index == 0 else line_height}">{html.escape(line)}</tspan>'
                   for index, line in enumerate(lines))


def render_flow_svg(flow: Mapping[str, Any], diagram_id: str) -> tuple[str, list[str]]:
    """Display declared connectivity; canonicalization only changes stable-v1 order."""
    flow = canonical_flow(dict(flow))
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
    incoming: dict[str, list[int]] = defaultdict(list)
    outgoing: dict[str, list[int]] = defaultdict(list)
    for item in _items(flow.get("edges")):
        if not isinstance(item, Mapping):
            warnings.append("存在无法读取的流程连线，原始声明保留在报告记录中。")
            continue
        source, target = str(item.get("source_step_key") or ""), str(item.get("target_step_key") or "")
        if source not in known or target not in known:
            warnings.append(f"流程连线 {source or '未提供'} → {target or '未提供'} 引用了不存在的节点，未绘制。")
            continue
        incoming[target].append(len(edges))
        outgoing[source].append(len(edges))
        edges.append(item)

    declared_main = {str(value) for value in _items(flow.get("main_path_ids"))}
    main_edges = {str(edge_id) for path in _items(flow.get("paths")) if isinstance(path, Mapping)
                  and str(path.get("path_id")) in declared_main for edge_id in _items(path.get("edge_ids"))}
    roots = [str(node["id"]) for node in nodes if not incoming[str(node["id"])] or node.get("kind") == "entry"]
    if not roots:
        roots = [str(nodes[0]["id"])]
    # Explicit main paths guide vertical order. A shortcut must not pull a main
    # terminal above the steps preceding it. Cyclic declarations are left to
    # the ordinary edge router; no path or business claim is repaired here.
    main_pairs: set[tuple[str, str]] = set()
    edge_pairs = {(str(edge["source_step_key"]), str(edge["target_step_key"])) for edge in edges}
    if uses_declared_main_paths(flow):
        for path in _items(flow.get("paths")):
            if not isinstance(path, Mapping) or str(path.get("path_id")) not in declared_main:
                continue
            sequence = [str(value) for value in _items(path.get("node_ids"))]
            seen = set(sequence[:1])
            for source, target in zip(sequence, sequence[1:]):
                if target not in seen and (source, target) in edge_pairs:
                    main_pairs.add((source, target))
                seen.add(target)
    main_incoming: dict[str, int] = defaultdict(int)
    main_outgoing: dict[str, list[str]] = defaultdict(list)
    for source, target in sorted(main_pairs):
        main_incoming[target] += 1
        main_outgoing[source].append(target)
    main_nodes = {node_id for pair in main_pairs for node_id in pair}
    ready = deque(str(node["id"]) for node in nodes if str(node["id"]) in main_nodes and not main_incoming[str(node["id"])])
    ranks = {node_id: 0 for node_id in ready}
    depths: dict[str, int] = {}
    while ready:
        source = ready.popleft()
        depths[source] = ranks[source]
        for target in main_outgoing[source]:
            ranks[target] = max(ranks.get(target, 0), ranks[source] + 1)
            main_incoming[target] -= 1
            if not main_incoming[target]:
                ready.append(target)
    if len(depths) != len(main_nodes):
        warnings.append("多个主路径的层级约束形成环，使用入口与连线排版并保留原路径声明，待核对。")
        depths = {}
    for node_id in roots:
        depths.setdefault(node_id, 0)
    queue = deque(sorted(depths, key=lambda node_id: (depths[node_id], node_id)))
    while queue:
        source = queue.popleft()
        for edge_index in outgoing[source]:
            target = str(edges[edge_index]["target_step_key"])
            if target not in depths:
                depths[target] = depths[source] + 1
                queue.append(target)
    next_depth = max(depths.values(), default=-1) + 1
    for node in nodes:
        node_id = str(node["id"])
        if node_id not in depths:
            depths[node_id] = next_depth
            next_depth += 1

    levels: dict[int, list[str]] = defaultdict(list)
    labels: dict[str, list[str]] = {}
    for node in nodes:
        node_id = str(node["id"])
        levels[depths[node_id]].append(node_id)
        labels[node_id] = _wrap_lines(node.get("label") or node_id, limit=32)
    max_degree = max([1, *[len(value) for value in incoming.values()], *[len(value) for value in outgoing.values()]])
    box_width = max(270, (max_degree + 1) * 46)
    row_heights = {depth: max(92, max(len(labels[node_id]) for node_id in level) * 20 + 44)
                   for depth, level in levels.items()}
    column_gap, margin_x = 72, 48
    max_columns = max(len(level) for level in levels.values())
    graph_width = max(720, margin_x * 2 + max_columns * box_width + (max_columns - 1) * column_gap)

    # Each horizontal segment gets its own gutter. Skipped levels and cycles
    # route around the node area, never through unrelated business steps.
    segments: dict[int, list[tuple[int, str]]] = defaultdict(list)
    external: dict[int, int] = {}
    for index, edge in enumerate(edges):
        source_depth, target_depth = depths[str(edge["source_step_key"])], depths[str(edge["target_step_key"])]
        if target_depth == source_depth + 1:
            segments[source_depth].append((index, "forward"))
        else:
            external[index] = len(external)
            segments[source_depth].append((index, "out"))
            segments[target_depth - 1].append((index, "in"))
    gap_heights = {depth: max(84, len(values) * 26 + 60) for depth, values in segments.items()}
    top = 42 + gap_heights.get(-1, 0)
    row_y: dict[int, float] = {}
    for depth in sorted(levels):
        row_y[depth] = top
        top += row_heights[depth] + gap_heights.get(depth, 84)
    positions: dict[str, tuple[float, float]] = {}
    for depth, level in levels.items():
        row_width = len(level) * box_width + (len(level) - 1) * column_gap
        start_x = (graph_width - row_width) / 2
        for index, node_id in enumerate(level):
            positions[node_id] = (start_x + index * (box_width + column_gap), row_y[depth])
    segment_y: dict[tuple[int, str], float] = {}
    for depth, values in segments.items():
        start_y = row_y[depth] + row_heights[depth] if depth >= 0 else 30
        for offset, key in enumerate(values):
            segment_y[key] = start_y + 42 + offset * 26
    width = graph_width + (len(external) * 32 + 36 if external else 0)
    legend_y = top + 10
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "-", diagram_id) or "flow"
    node_map = {str(node["id"]): node for node in nodes}
    edge_parts: list[str] = []
    badges: list[str] = []
    legend: list[str] = []
    for edge_index, edge in enumerate(edges):
        source_id, target_id = str(edge["source_step_key"]), str(edge["target_step_key"])
        source_x, source_y = positions[source_id]
        target_x, target_y = positions[target_id]
        start_x = source_x + box_width * (outgoing[source_id].index(edge_index) + 1) / (len(outgoing[source_id]) + 1)
        end_x = target_x + box_width * (incoming[target_id].index(edge_index) + 1) / (len(incoming[target_id]) + 1)
        start_y = source_y + row_heights[depths[source_id]]
        if edge_index in external:
            outside_x = graph_width + 16 + external[edge_index] * 32
            out_y, in_y = segment_y[(edge_index, "out")], segment_y[(edge_index, "in")]
            path = f"M {start_x:.0f} {start_y:.0f} V {out_y:.0f} H {outside_x:.0f} V {in_y:.0f} H {end_x:.0f} V {target_y:.0f}"
        else:
            middle_y = segment_y[(edge_index, "forward")]
            path = f"M {start_x:.0f} {start_y:.0f} V {middle_y:.0f} H {end_x:.0f} V {target_y:.0f}"
        badge = f"E{edge_index + 1}"
        condition = str(edge.get("condition") or "未声明条件")
        identity = str(edge.get("edge_id") or badge)
        source_label, target_label = str(node_map[source_id].get("label") or source_id), str(node_map[target_id].get("label") or target_id)
        title = f"{badge} · {identity} · {source_label} → {target_label} · {condition}"
        main = bool(main_edges and identity in main_edges)
        dash = "" if main or not declared_main else ' stroke-dasharray="6 4"'
        color = "#3157d5" if main else "#667085"
        edge_parts.append(f'<g class="flow-edge-group" data-edge-id="{html.escape(identity, quote=True)}"><title>{html.escape(title)}</title>'
                          f'<path d="{path}" class="flow-edge" style="stroke:{color}"{dash} marker-end="url(#{safe_id}-arrow)"/></g>')
        # Badge sits on this edge's own source port, before any line crossing.
        badge_width = max(34, len(badge) * 8 + 12)
        badge_y = start_y + 10
        badges.append(f'<g class="flow-edge-badge"><title>{html.escape(title)}</title>'
                      f'<rect x="{start_x - badge_width / 2:.0f}" y="{badge_y:.0f}" width="{badge_width}" height="22" rx="4"/>'
                      f'<text x="{start_x:.0f}" y="{badge_y + 15:.0f}" text-anchor="middle">{badge}</text></g>')
        legend_text = f"[{badge}] {source_label} → {target_label}；条件：{condition}"
        if edge.get("edge_id"):
            legend_text += f"；连线：{identity}"
        legend_lines = _wrap_lines(legend_text, limit=max(60, int((width - 96) / 7)))
        row_height = len(legend_lines) * 20 + 18
        legend.append(f'<g class="flow-edge-legend"><rect x="32" y="{legend_y:.0f}" width="{width - 64:.0f}" height="{row_height}" rx="4"/>'
                      f'<text x="44" y="{legend_y + 20:.0f}">{_tspans(legend_lines, 44, 20)}</text></g>')
        legend_y += row_height + 6

    node_parts: list[str] = []
    for node in nodes:
        node_id = str(node["id"])
        x, y = positions[node_id]
        kind = str(node.get("kind") or "main")
        color = _NODE_COLORS.get(kind, _NODE_COLORS["main"])
        node_parts.append(f'<g class="flow-node" data-node-id="{html.escape(node_id, quote=True)}"><title>{html.escape(str(node.get("label") or node_id))}</title>'
                          f'<rect x="{x:.0f}" y="{y:.0f}" width="{box_width}" height="{row_heights[depths[node_id]]}" rx="10" style="stroke:{color}"/>'
                          f'<text x="{x + 18:.0f}" y="{y + 18:.0f}" class="flow-kind">{html.escape(kind)}</text>'
                          f'<text x="{x + 18:.0f}" y="{y + 43:.0f}" class="flow-label">{_tspans(labels[node_id], x + 18, 20)}</text></g>')
    height = legend_y + 24 if edges else top + 20
    legend_hint = ("连线编号与下方条件逐项对应；已声明主路径使用实线，其余为虚线。" if declared_main
                   else "连线编号与下方条件逐项对应，箭头表示方向；未声明主路径。")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height:.0f}" viewBox="0 0 {width} {height:.0f}" '
           f'role="img" aria-label="{html.escape(str(flow.get("title") or "模块行为流程图"), quote=True)}">'
           '<style>text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif}.flow-node rect{fill:#fff;stroke-width:2}'
           '.flow-kind{fill:#667085;font-size:11px}.flow-label{fill:#172033;font-size:14px}'
           '.flow-edge{fill:none;stroke:#667085;stroke-width:1.7}.flow-edge-badge rect{fill:#fff;stroke:#667085;stroke-width:1}'
           '.flow-edge-badge text{font-size:11px;fill:#344054}.flow-edge-legend rect{fill:#f7f9fc;stroke:#dce2ea;stroke-width:1}'
           '.flow-edge-legend text{font-size:12px;fill:#344054}</style>'
           f'<rect width="{width}" height="{height:.0f}" fill="#fff"/>'
           f'<defs><marker id="{safe_id}-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto">'
           '<path d="M0,0 L0,8 L9,4 z" fill="#667085"/></marker></defs>'
           f'<text x="32" y="22" class="flow-kind">{legend_hint}</text>'
           f'{"".join(edge_parts)}{"".join(node_parts)}{"".join(badges)}{"".join(legend)}</svg>')
    return svg, warnings
