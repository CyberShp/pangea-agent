"""behavior-flow-v1 结构契约。

``check_flow_structure`` 是写入时的严格格式检查（字段名、id 引用），只判断结构，
不判断流程语义，也不修改记录。``normalize_flow`` 是展示侧的宽容解析，与控制台
``flow-normalize.js`` 的别名规则保持一致；它只做键名/外壳兼容，不补造节点。
"""

from __future__ import annotations

import json
import re
from typing import Any

FLOW_FORMAT = "behavior-flow-v1"
TEXT_FLOW_FORMAT = "module-flow-text-v1"
NODE_KINDS = ("entry", "main", "branch", "error", "propagation", "recovery", "exit")
FLOW_ROLES = ("main", "sub")
CANONICAL_EXAMPLE_POINTER = (
    "请参照 rubrics/builtin/scene_flow.md 中的标准样例："
    '{"format_version":"behavior-flow-v1","flow_id":"F-001","title":"…","role":"main","attach_to":null,'
    '"nodes":[{"id":"submit","kind":"entry","label":"…"},{"id":"verify","kind":"exit","label":"…"}],'
    '"edges":[{"source_step_key":"submit","target_step_key":"verify","condition":""}],'
    '"paths":[{"path_id":"P-01","node_ids":["submit","verify"]}]}'
)

_MAX_LISTED_IDS = 12
_TOP_LEVEL_FORBIDDEN = {
    "flow": "直接写流程对象，不要外套 flow",
    "flows": "一条记录只放一个流程，多个流程请写多条 flow 记录",
    "steps": "nodes 与 edges",
    "节点": "nodes",
    "连线": "edges",
    "路径": "paths",
}
_EDGE_FORBIDDEN = {
    "from": "source_step_key",
    "source": "source_step_key",
    "to": "target_step_key",
    "target": "target_step_key",
}
_NODE_FORBIDDEN = {"node_id": "id", "name": "label"}

# 展示侧别名（与 companion/src/flow-normalize.js 对齐）。
_TOP_ALIASES = {
    "title": ("标题",),
    "nodes": ("节点",),
    "edges": ("连线",),
    "paths": ("路径",),
    "source_evidence": ("源码依据",),
    "description": ("说明", "描述"),
}
_NODE_ALIASES = {
    "id": ("node_id", "step_id"),
    "label": ("name", "title", "名称"),
    "kind": ("类型",),
    "description": ("说明", "描述"),
    "source_evidence": ("源码依据",),
}
_EDGE_ALIASES = {
    "source_step_key": ("source", "from"),
    "target_step_key": ("target", "to"),
    "condition": ("条件",),
    "description": ("说明", "描述"),
}
_PATH_ALIASES = {
    "path_id": ("id",),
    "title": ("标题", "名称", "name"),
    "condition": ("条件",),
    "explanation": ("说明", "描述"),
    "node_ids": ("nodes", "节点"),
}
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _id_list(ids: list[str]) -> str:
    if len(ids) <= _MAX_LISTED_IDS:
        return ", ".join(ids)
    return ", ".join(ids[:_MAX_LISTED_IDS]) + f" 等 {len(ids)} 个"


def check_flow_structure(body: Any) -> list[str]:
    """返回 flow 记录 body 的结构错误（中文）；空列表表示结构合格。"""
    if isinstance(body, str):
        return ["body 必须是 JSON 对象，不能是字符串或代码块"]
    if not isinstance(body, dict):
        return [f"body 必须是 JSON 对象，当前是 {type(body).__name__}"]
    errors: list[str] = []
    for key, hint in _TOP_LEVEL_FORBIDDEN.items():
        if key in body:
            errors.append(f"检测到顶层键 {key}，请改为 {hint}")
    version = body.get("format_version")
    if version == TEXT_FLOW_FORMAT:
        if not _non_empty_str(body.get("text")):
            errors.append("module-flow-text-v1 必须包含非空 text")
        return errors
    if version != FLOW_FORMAT:
        errors.append(f"format_version 缺失或不是 {FLOW_FORMAT}（当前：{version!r}）")
    errors.extend(_check_role(body))
    node_ids, node_errors = _check_nodes(body.get("nodes"))
    errors.extend(node_errors)
    errors.extend(_check_edges(body.get("edges"), node_ids))
    errors.extend(_check_paths(body.get("paths"), node_ids))
    return errors


def _check_role(body: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    role = body.get("role")
    if role is not None and role not in FLOW_ROLES:
        errors.append(f"role 只能是 main 或 sub（当前：{role!r}）")
    attach = body.get("attach_to")
    if attach is None:
        return errors
    if not isinstance(attach, dict):
        errors.append('attach_to 必须是 null 或 {"flow_id": "...", "node_id": "..."}')
        return errors
    for key in ("flow_id", "node_id"):
        if not _non_empty_str(attach.get(key)):
            errors.append(f"attach_to.{key} 必须是非空字符串")
    return errors


def _check_nodes(nodes: Any) -> tuple[list[str], list[str]]:
    if not isinstance(nodes, list) or not nodes:
        return [], ["nodes 必须是非空数组"]
    errors: list[str] = []
    ids: list[str] = []
    seen: dict[str, int] = {}
    for index, node in enumerate(nodes):
        where = f"nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{where} 必须是对象")
            continue
        for key, use in _NODE_FORBIDDEN.items():
            if key in node:
                errors.append(f"检测到 {where}.{key}，请改为 {use}")
        node_id = node.get("id")
        if not _non_empty_str(node_id):
            errors.append(f"{where} 缺少 id（不要用 node_id/name/节点）")
        elif node_id in seen:
            errors.append(f"节点 id '{node_id}' 重复（nodes[{seen[node_id]}], {where}）")
        else:
            seen[node_id] = index
            ids.append(node_id)
        if not _non_empty_str(node.get("label")):
            errors.append(f"{where} 缺少 label（不要用 name/title/名称）")
        kind = node.get("kind")
        if kind is not None and kind not in NODE_KINDS:
            errors.append(f"{where}.kind={kind!r} 不合法；只能取 {' | '.join(NODE_KINDS)}")
    return ids, errors


def _check_edges(edges: Any, node_ids: list[str]) -> list[str]:
    if edges is None:
        return []
    if not isinstance(edges, list):
        return ["edges 必须是数组"]
    known = set(node_ids)
    errors: list[str] = []
    for index, edge in enumerate(edges):
        where = f"edges[{index}]"
        if not isinstance(edge, dict):
            errors.append(f"{where} 必须是对象")
            continue
        for key, use in _EDGE_FORBIDDEN.items():
            if key in edge:
                errors.append(f"检测到 {where}.{key}，请改为 {use}")
        for key in ("source_step_key", "target_step_key"):
            value = edge.get(key)
            if not _non_empty_str(value):
                errors.append(f"{where}.{key} 缺失或为空")
            elif node_ids and value not in known:
                errors.append(f"{where}.{key}='{value}' 不是节点 id；可用 id：{_id_list(node_ids)}")
    return errors


def _check_paths(paths: Any, node_ids: list[str]) -> list[str]:
    if paths is None:
        return []
    if not isinstance(paths, list):
        return ["paths 必须是数组"]
    known = set(node_ids)
    errors: list[str] = []
    for index, path in enumerate(paths):
        where = f"paths[{index}]"
        if not isinstance(path, dict):
            errors.append(f"{where} 必须是对象")
            continue
        refs = path.get("node_ids")
        if not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
            errors.append(f"{where}.node_ids 必须是字符串数组")
            continue
        for ref in refs:
            if node_ids and ref not in known:
                errors.append(f"{where}.node_ids 含未知 id '{ref}'；可用 id：{_id_list(node_ids)}")
    return errors


def format_flow_errors(offset: int, errors: list[str]) -> str:
    lines = [f"record[{offset}] flow 结构不合格，共 {len(errors)} 处，请修正后在本轮重写："]
    lines.extend(f"- {item}" for item in errors)
    lines.append(CANONICAL_EXAMPLE_POINTER)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 宽容解析（展示侧）


def normalize_flow(body: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """把 flow body 规范化为若干个 behavior-flow-v1 对象，并返回兼容提示。"""
    notes: list[str] = []
    value = body
    if isinstance(value, str):
        text = value.strip()
        match = _FENCE.match(text)
        if match:
            text = match.group(1).strip()
            notes.append("已自动兼容：去掉 ```json 代码块包裹")
        try:
            value = json.loads(text)
        except ValueError:
            return [], ["无法解析为 JSON 对象"]
        notes.append("已自动兼容：字符串 body 解析为 JSON")
    if not isinstance(value, dict):
        return [], ["body 不是 JSON 对象"]
    candidates: list[dict[str, Any]] = []
    if isinstance(value.get("flows"), list):
        candidates = [item for item in value["flows"] if isinstance(item, dict)]
        notes.append(f"已自动兼容：flows 外壳拆成 {len(candidates)} 个流程")
    elif isinstance(value.get("flow"), dict):
        candidates = [value["flow"]]
        notes.append("已自动兼容：拆开 flow 外壳")
    else:
        candidates = [value]
    flows: list[dict[str, Any]] = []
    for candidate in candidates:
        flow, flow_notes = _normalize_one(candidate)
        notes.extend(flow_notes)
        if flow is not None:
            flows.append(flow)
    return flows, notes


def _apply_aliases(source: dict[str, Any], aliases: dict[str, tuple[str, ...]], where: str, notes: list[str]) -> dict[str, Any]:
    result = dict(source)
    for canonical, names in aliases.items():
        if result.get(canonical) not in (None, ""):
            continue
        for name in names:
            if name in source and source[name] not in (None, ""):
                result[canonical] = source[name]
                notes.append(f"已自动兼容：{where}{name} 视同 {canonical}")
                break
    return result


def _normalize_one(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    notes: list[str] = []
    if raw.get("format_version") == TEXT_FLOW_FORMAT:
        return (dict(raw) if _non_empty_str(raw.get("text")) else None), notes
    flow = _apply_aliases(raw, _TOP_ALIASES, "", notes)
    nodes_raw = flow.get("nodes")
    edges_raw = flow.get("edges")
    if not isinstance(nodes_raw, list) and isinstance(flow.get("steps"), list):
        nodes_raw, inferred = _steps_to_nodes(flow["steps"])
        if not isinstance(edges_raw, list):
            edges_raw = inferred
        notes.append("已自动兼容：steps 转为 nodes，由步骤顺序推断连线")
    nodes = _normalize_nodes(nodes_raw, notes)
    if not nodes:
        return None, notes
    edges = _normalize_edges(edges_raw, nodes, notes)
    paths = _normalize_paths(flow.get("paths"), notes)
    flow["nodes"] = nodes
    flow["edges"] = edges
    flow["paths"] = paths
    flow.setdefault("format_version", FLOW_FORMAT)
    for key in ("steps", "节点", "连线", "路径", "标题", "说明", "描述", "源码依据"):
        flow.pop(key, None)
    return flow, notes


def _steps_to_nodes(steps: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if isinstance(step, dict):
            node = dict(step)
            node_id = step.get("id") or step.get("step_id") or f"s{index + 1}"
            label = step.get("label") or step.get("name") or step.get("title") or step.get("名称") or str(node_id)
            node["id"] = str(node_id)
            node["label"] = str(label)
        else:
            node = {"id": f"s{index + 1}", "label": str(step)}
        nodes.append(node)
    edges = [
        {"source_step_key": nodes[i]["id"], "target_step_key": nodes[i + 1]["id"], "condition": ""}
        for i in range(len(nodes) - 1)
    ]
    return nodes, edges


def _normalize_nodes(raw: Any, notes: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    nodes: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        node = _apply_aliases(item, _NODE_ALIASES, f"nodes[{index}].", notes)
        node_id = node.get("id")
        missing = [key for key in ("id", "label") if not _non_empty_str(node.get(key))]
        if missing:
            notes.extend(f"nodes[{index}] 缺少 {key}，未绘制；请查看原文" for key in missing)
            continue
        if node_id in seen:
            notes.append(f"节点 id '{node_id}' 重复（nodes[{seen[node_id]}], nodes[{index}]），无法可靠定位连线；原文保留")
            return []
        seen[node_id] = index
        node["id"] = node_id
        nodes.append(node)
    return nodes


def _normalize_edges(raw: Any, nodes: list[dict[str, Any]], notes: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    ids = {node["id"] for node in nodes}
    by_label: dict[str, list[str]] = {}
    for node in nodes:
        by_label.setdefault(str(node.get("label")), []).append(node["id"])
    edges: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        edge = _apply_aliases(item, _EDGE_ALIASES, f"edges[{index}].", notes)
        keep = True
        for key in ("source_step_key", "target_step_key"):
            value = edge.get(key)
            if isinstance(value, str) and value in ids:
                continue
            matches = by_label.get(value, []) if isinstance(value, str) else []
            if len(matches) == 1:
                edge[key] = matches[0]
                notes.append(f"已自动兼容：edges[{index}].{key} 由节点名 '{value}' 改写为 id '{matches[0]}'")
            elif len(matches) > 1:
                notes.append(f"已自动兼容：edges[{index}].{key}='{value}' 匹配多个节点，已丢弃该连线")
                keep = False
            else:
                notes.append(f"已自动兼容：edges[{index}].{key}='{value}' 不是节点 id，已丢弃该连线")
                keep = False
        if keep:
            for key in ("source", "from", "target", "to", "条件"):
                edge.pop(key, None)
            edges.append(edge)
    return edges


def _normalize_paths(raw: Any, notes: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    paths: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        path = _apply_aliases(item, _PATH_ALIASES, f"paths[{index}].", notes)
        refs = path.get("node_ids")
        path["node_ids"] = [str(ref) for ref in refs] if isinstance(refs, list) else []
        paths.append(path)
    return paths
