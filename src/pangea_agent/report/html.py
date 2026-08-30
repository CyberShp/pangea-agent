from __future__ import annotations

import html
import json
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .markdown import (
    _boundary_text,
    _coverage_rows,
    _contract_rows,
    _items,
    _methodology_rows,
    _parse_failures,
    _quality_summary,
    _report_title,
    _repository_rows,
    _risk_dimension_rows,
    _scope_rows,
    _status,
    _text,
    render_report,
)


def _escape(value: Any, default: str = "未提供") -> str:
    return html.escape(_text(value, default))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return slug or "section"


def _list(values: Any, empty: str = "无") -> str:
    items = _items(values)
    if not items:
        return f'<p class="muted">{html.escape(empty)}</p>'
    rows = []
    for item in items:
        if isinstance(item, Mapping):
            title = item.get("title") or item.get("name") or item.get("path") or item.get("id") or "记录"
            details = "".join(
                f"<dt>{_escape(key)}</dt><dd>{_escape(value)}</dd>"
                for key, value in item.items()
                if key not in {"title", "name"}
            )
            rows.append(f"<li><strong>{_escape(title)}</strong><dl>{details}</dl></li>")
        else:
            rows.append(f"<li>{_escape(item)}</li>")
    return "<ul>" + "".join(rows) + "</ul>"


def _evidence(values: Any) -> str:
    rows = []
    for item in _items(values):
        if isinstance(item, Mapping):
            location = item.get("location") or item.get("source") or item.get("path") or "位置未提供"
            observation = item.get("observation") or item.get("summary") or item.get("reason") or "未提供说明"
            pending = ""
            if item.get("status") == "pending_confirmation":
                pending = f' <span class="muted">（证据待确认：{_escape(item.get("pending_reason"))}）</span>'
            rows.append(f"<li><code>{_escape(location)}</code> — {_escape(observation)}{pending}</li>")
        else:
            rows.append(f"<li>{_escape(item)}</li>")
    return "<ul>" + "".join(rows or ["<li>未提供源码证据</li>"]) + "</ul>"


def _table(headers: tuple[str, ...], rows: list[tuple[Any, ...]], code_columns: set[int] | None = None) -> str:
    if not rows:
        return '<p class="muted">无</p>'
    code_columns = code_columns or set()
    head = "".join(f"<th>{_escape(header)}</th>" for header in headers)
    body = []
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            if isinstance(value, (list, tuple, set)):
                rendered = "<br>".join(_escape(item) for item in value) or "无"
            else:
                rendered = _escape(value)
            if index in code_columns:
                rendered = f"<code>{rendered}</code>"
            cells.append(f"<td>{rendered}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _upstream_semantics(value: Any) -> str:
    labels = {
        "reachability": "入口可达性",
        "caller_constraints": "调用方约束",
        "documented_behavior": "资料定义",
        "existing_tests": "已有测试",
        "conclusion": "核对结论",
    }
    rows = []
    for item in _items(value):
        if isinstance(item, Mapping):
            rows.extend((labels.get(str(key), key), detail) for key, detail in item.items())
        else:
            rows.append(("核对记录", item))
    return _table(("核对项", "结论"), rows) if rows else '<p class="muted">未提供上游语义核对。</p>'


def _flow_diagram(value: Any, diagram_id: str) -> str:
    if not isinstance(value, Mapping):
        return ""
    nodes = [item for item in _items(value.get("nodes")) if isinstance(item, Mapping)]
    edges = [item for item in _items(value.get("edges")) if isinstance(item, Mapping)]
    if not nodes:
        return ""
    positions = {str(node.get("id")): 50 + index * 96 for index, node in enumerate(nodes)}
    height = positions[str(nodes[-1].get("id"))] + 70
    colors = {
        "entry": "#6ca0dc",
        "main": "#d89b6e",
        "branch": "#d7b85b",
        "error": "#e07a6b",
        "propagation": "#c26b91",
        "recovery": "#70ad8f",
        "exit": "#8d99a6",
    }
    lines = []
    for edge in edges:
        source = positions.get(str(edge.get("source_step_key")))
        target = positions.get(str(edge.get("target_step_key")))
        if source is None or target is None:
            continue
        condition = str(edge.get("condition") or "")
        label = (
            f'<text x="410" y="{(source + target) / 2 - 5:.0f}" class="flow-edge-label">'
            f'{html.escape(condition[:48])}</text>'
            if condition
            else ""
        )
        lines.append(
            f'<line x1="380" y1="{source + 29}" x2="380" y2="{target - 29}" '
            f'marker-end="url(#{diagram_id}-arrow)" />{label}'
        )
    boxes = []
    for node in nodes:
        node_id = str(node.get("id"))
        y = positions[node_id]
        kind = str(node.get("kind") or "main")
        color = colors.get(kind, colors["main"])
        label = str(node.get("label") or node_id)
        boxes.append(
            f'<rect x="120" y="{y - 28}" width="520" height="56" rx="8" '
            f'style="stroke:{color}" />'
            f'<text x="140" y="{y - 5}" class="flow-kind">{html.escape(kind)}</text>'
            f'<text x="140" y="{y + 16}" class="flow-label">{html.escape(label[:76])}</text>'
        )
    return (
        f'<div class="flow-svg"><svg viewBox="0 0 760 {height}" role="img" '
        f'aria-label="结构化流程图"><defs><marker id="{diagram_id}-arrow" markerWidth="8" '
        f'markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" '
        f'fill="#777168"/></marker></defs>{"".join(lines)}{"".join(boxes)}</svg></div>'
    )


def _shell(title: str, incomplete: bool, navigation: str, body: str) -> str:
    badge = "INCOMPLETE" if incomplete else "COMPLETE"
    tone = "danger" if incomplete else "ok"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#171615;--panel:#1e1d1b;--panel-2:#24221f;--ink:#ece7df;--muted:#aaa39a;--line:#3a3732;--accent:#d97757;--accent-soft:#39251f;--danger:#ef8b7a;--danger-bg:#34201d;--ok:#d89b6e;--ok-bg:#30241d}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth;color-scheme:dark}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 "Cascadia Mono","SFMono-Regular",Consolas,"Microsoft YaHei",monospace}}
header{{max-width:1240px;margin:0 auto;padding:44px 28px 26px;border-bottom:1px solid var(--line)}}header h1{{margin:0 0 14px;max-width:900px;font:600 30px/1.28 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;letter-spacing:-.02em}}.eyebrow{{margin-bottom:10px;color:var(--accent);font-size:12px;letter-spacing:.12em;text-transform:uppercase}}.badge{{display:inline-block;padding:3px 9px;border:1px solid;border-radius:4px;font-size:12px;font-weight:700;letter-spacing:.05em}}.badge.danger{{background:var(--danger-bg);color:var(--danger);border-color:#714038}}.badge.ok{{background:var(--ok-bg);color:var(--ok);border-color:#684830}}
.layout{{max-width:1240px;margin:0 auto;display:grid;grid-template-columns:210px minmax(0,1fr);gap:34px;padding:28px}}nav{{position:sticky;top:18px;align-self:start;padding:4px 0 12px;border-right:1px solid var(--line)}}nav::before{{content:"CONTENTS";display:block;margin:0 18px 10px 0;color:#777168;font-size:11px;letter-spacing:.12em}}nav a{{display:block;margin-right:18px;padding:6px 9px;color:var(--muted);text-decoration:none;border-left:2px solid transparent}}nav a:hover{{color:var(--ink);border-left-color:var(--accent);background:#211f1c}}main{{min-width:0}}section{{padding:8px 0 34px;margin-bottom:28px;border-bottom:1px solid var(--line);scroll-margin-top:18px}}section:last-child{{border-bottom:0}}h2{{margin:0 0 20px;font:600 21px/1.35 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}h3{{margin:28px 0 12px;color:#d8d2c9;font:600 16px/1.4 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}h4{{color:#c9c2b8}}p,li{{max-width:92ch}}details{{border:1px solid var(--line);border-radius:6px;margin:10px 0;background:var(--panel)}}summary{{cursor:pointer;padding:12px 14px;color:#e5dfd7;font-weight:700}}summary::marker{{color:var(--accent)}}.detail-body{{padding:2px 16px 16px;border-top:1px solid var(--line)}}dl{{display:grid;grid-template-columns:minmax(130px,190px) 1fr;gap:7px 14px}}dt{{color:var(--muted);font-weight:400}}dd{{margin:0;overflow-wrap:anywhere}}code,pre{{font-family:inherit}}code{{background:#292723;color:#f0c1a4;padding:2px 5px;border-radius:3px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#11100f;color:#d8d2c9;padding:14px;border:1px solid #302d29;border-radius:5px}}.table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:6px}}table{{width:100%;border-collapse:collapse;background:var(--panel)}}th,td{{padding:10px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);overflow-wrap:anywhere}}th{{background:var(--panel-2);color:#bdb5ab;font-size:12px;font-weight:600;letter-spacing:.04em}}tr:last-child td{{border-bottom:0}}tbody tr:hover{{background:#22201e}}.summary{{margin:18px 0 0;padding:13px 15px;border-left:3px solid var(--accent);background:var(--accent-soft);color:#d8d2c9}}.summary.danger{{border-left-color:var(--danger);background:var(--danger-bg)}}.muted{{color:var(--muted)}}.mermaid-note{{color:#c8a386;background:#2c241e;padding:9px;border-radius:5px}}.back{{float:right;color:#888078;font-size:12px;font-weight:400;text-decoration:none}}.back:hover{{color:var(--accent)}}ul{{padding-left:22px}}@media(max-width:820px){{header{{padding:28px 18px 20px}}header h1{{font-size:24px}}.layout{{display:block;padding:18px}}nav{{position:static;border-right:0;border-bottom:1px solid var(--line);margin-bottom:28px;padding-bottom:14px}}nav a{{display:inline-block;margin:0 8px 4px 0}}dl{{grid-template-columns:1fr}}}}
.detail-body code{{overflow-wrap:anywhere;word-break:break-word}}.table-wrap code{{white-space:normal;overflow-wrap:anywhere;word-break:normal}}.table-wrap th:first-child,.table-wrap td:first-child{{white-space:nowrap}}
.flow-svg{{margin:14px 0;overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:#151412}}.flow-svg svg{{display:block;width:100%;min-width:620px}}.flow-svg line{{stroke:#777168;stroke-width:1.5}}.flow-svg rect{{fill:#24221f;stroke-width:2}}.flow-svg text{{fill:#ece7df;font-family:inherit}}.flow-svg .flow-kind{{fill:#aaa39a;font-size:11px;text-transform:uppercase}}.flow-svg .flow-label{{font-size:14px}}.flow-svg .flow-edge-label{{fill:#c8a386;font-size:11px}}
</style>
</head>
<body id="top">
<header><div class="eyebrow">TEST ANALYSIS</div><h1>{html.escape(title)}</h1><span class="badge {tone}">{badge}</span></header>
<div class="layout"><nav aria-label="报告目录">{navigation}</nav><main>{body}</main></div>
</body></html>"""


def render_html_report(state: Mapping[str, Any]) -> str:
    _, incomplete = _status(state)
    nav_labels = (
        ("contract", "任务契约"), ("scope", "范围与排除"), ("flows", "业务流程"),
        ("alignment", "资料与代码"), ("mechanisms", "缺陷机理"),
        ("risks", "六维风险"), ("coverage", "覆盖率缺口"), ("cases", "测试用例"),
        ("incomplete", "不完整项"), ("quality", "质量门禁"),
    )
    navigation = "".join(f'<a href="#{key}">{label}</a>' for key, label in nav_labels)
    contract = state.get("task_contract") or {}
    title = _report_title(state)
    contract_html = _table(("项目", "内容"), _contract_rows(state, contract)) if isinstance(contract, Mapping) else _list(contract)
    expansion = state.get("scope_expansion") or {}
    scope_html = _table(
        ("类别", "源码仓", "路径", "纳入原因"),
        _scope_rows(state, contract) if isinstance(contract, Mapping) else [],
        {1, 2},
    )
    boundary_html = ""
    if isinstance(expansion, Mapping) and expansion.get("boundary"):
        boundary_html = f'<p class="muted">扩展边界：{_escape(_boundary_text(expansion.get("boundary")))}</p>'
    repositories_html = _table(("源码仓", "本地目录", "版本", "状态"), _repository_rows(state), {0, 1, 2})
    manifest = state.get("source_manifest") or {}
    inventory = state.get("inventory") or {}
    manifest_html = _table(("项目", "结果"), [
        ("源码文件数", inventory.get("file_count", 0) if isinstance(inventory, Mapping) else 0),
        ("结构化解析", "完整" if isinstance(inventory, Mapping) and inventory.get("structural_parse_complete") else "存在缺口"),
        ("文档告警", len(_items(manifest.get("warnings"))) if isinstance(manifest, Mapping) else 0),
        ("缺少依赖", len(_items(manifest.get("missing_dependencies"))) if isinstance(manifest, Mapping) else 0),
        ("图片附件", len(_items(manifest.get("attachments"))) if isinstance(manifest, Mapping) else 0),
        ("Coverage 记录", len(_items(manifest.get("coverage_records"))) if isinstance(manifest, Mapping) else 0),
    ])
    summary_rows = [
        (item.get("unit_id"), item.get("worker_id"), item.get("summary"))
        for item in _items(state.get("analysis_summaries"))
        if isinstance(item, Mapping)
    ]
    summaries_html = _table(("分析单元", "Worker", "结论"), summary_rows, {0, 1})
    methodology_html = _table(
        ("分析单元", "单元名称", "方法论", "选择依据"),
        _methodology_rows(state),
        {0, 2},
    )
    material_decision_rows = [
        (item.get("unit_id"), item.get("path"), item.get("decision"), item.get("reason"))
        for item in _items(state.get("material_decisions"))
        if isinstance(item, Mapping)
    ]
    material_decisions_html = _table(("分析单元", "资料", "处理", "理由"), material_decision_rows, {0, 1, 2})
    material_evidence_rows = [
        (item.get("unit_id"), item.get("location") or item.get("chunk_id"), item.get("observation"), item.get("status"))
        for item in _items(state.get("material_evidence"))
        if isinstance(item, Mapping)
    ]
    material_evidence_html = _table(("分析单元", "引用位置", "使用结论", "状态"), material_evidence_rows, {0, 1, 3})
    body = [
        f'<section id="contract"><a class="back" href="#top">回到顶部</a><h2>1. 任务契约</h2>{contract_html}</section>',
        f'<section id="scope"><a class="back" href="#top">回到顶部</a><h2>2. 分析范围与排除项</h2><h3>纳入范围</h3>{scope_html}{boundary_html}<h3>源码仓</h3>{repositories_html}<h3>明确排除</h3>{_list(state.get("excluded_scope") or state.get("exclusions"))}<h3>已使用方法论</h3>{methodology_html}<h3>资料采用与排除结论</h3>{material_decisions_html}<h3>资料引用</h3>{material_evidence_html}<h3>分析结论摘要</h3>{summaries_html}<h3>源码清单摘要</h3>{manifest_html}</section>',
    ]

    flow_parts = []
    flows = state.get("business_flows") or state.get("flows") or state.get("flow_diagrams")
    for index, flow in enumerate(_items(flows), 1):
        if not isinstance(flow, Mapping):
            flow_parts.append(f"<p>{_escape(flow)}</p>")
            continue
        flow_title = _escape(flow.get("title") or flow.get("name") or f"流程 {index}")
        steps = "".join(f"<li>{_escape(step)}</li>" for step in _items(flow.get("steps") or flow.get("business_steps")))
        diagram_html = _flow_diagram(flow.get("diagram"), f"flow-{index}")
        flow_parts.append(f'<details open><summary>{flow_title}</summary><div class="detail-body"><p>{_escape(flow.get("description"), "")}</p><ol>{steps}</ol>{diagram_html}<h4>关键函数证据</h4>{_evidence(flow.get("evidence") or flow.get("function_evidence"))}</div></details>')
    rendered_flows = "".join(flow_parts) or '<p class="muted">未提供业务流程产物。</p>'
    visual_parts = []
    for finding in _items(state.get("visual_findings")):
        if isinstance(finding, Mapping):
            attachment = finding.get("attachment_path") or finding.get("path") or finding.get("source")
            observation = finding.get("observation") or finding.get("summary")
            pending = ""
            if finding.get("status") == "pending_confirmation":
                pending = f' <span class="muted">（证据待确认：{_escape(finding.get("pending_reason"))}）</span>'
            visual_parts.append(f'<li><code>{_escape(attachment, "附件路径未提供")}</code> — {_escape(observation)}{pending}</li>')
        else:
            visual_parts.append(f"<li>{_escape(finding)}</li>")
    visual_html = f'<h3>图片分析发现</h3><ul>{"".join(visual_parts)}</ul>' if visual_parts else ""
    body.append(f'<section id="flows"><a class="back" href="#top">回到顶部</a><h2>3. 业务流程与关键函数证据</h2>{rendered_flows}{visual_html}</section>')

    alignment_rows = []
    for item in _items(state.get("input_decisions")):
        if isinstance(item, Mapping):
            alignment_rows.append((
                item.get("unit_id"),
                item.get("item_id"),
                item.get("disposition"),
                item.get("conclusion"),
            ))
    body.append(
        '<section id="alignment"><a class="back" href="#top">回到顶部</a>'
        '<h2>4. 资料与代码一致性</h2>'
        + _table(("分析单元", "资料条目", "结论", "说明"), alignment_rows, {0, 1, 2})
        + '</section>'
    )

    mechanism_parts = []
    for item in _items(state.get("mechanism_decisions")):
        if not isinstance(item, Mapping):
            continue
        facts = _table(("项目", "内容"), [
            ("分析单元", item.get("unit_id")),
            ("判断", item.get("disposition")),
            ("当前因果链", item.get("current_causal_chain")),
            ("关联用例", item.get("test_case_ids")),
            ("结论", item.get("conclusion")),
        ], {1})
        mechanism_parts.append(
            f'<details><summary>{_escape(item.get("mechanism_id"))}</summary>'
            f'<div class="detail-body">{facts}<h4>源码证据</h4>{_evidence(item.get("evidence"))}</div></details>'
        )
    body.append(
        '<section id="mechanisms"><a class="back" href="#top">回到顶部</a>'
        '<h2>5. 缺陷机理检查</h2>'
        + ("".join(mechanism_parts) or '<p class="muted">当前范围没有关联的已确认缺陷机理。</p>')
        + '</section>'
    )

    risks = _items(state.get("risks"))
    cards = []
    for risk in risks:
        if not isinstance(risk, Mapping):
            cards.append(f"<p>{_escape(risk)}</p>")
            continue
        fields = (
            ("DFX 维度", "、".join(_text(item) for item in _items(risk.get("dfx"))) or "未提供"),
            ("严重度", risk.get("severity")), ("置信度", risk.get("confidence")),
            ("风险状态", risk.get("status")), ("测试转化状态", risk.get("translation_status")),
            ("测试处置", risk.get("test_disposition")),
            ("复现条件", risk.get("trigger") or risk.get("reproduction_condition")),
            ("系统结果", risk.get("system_result")), ("外部观测", risk.get("external_observation")),
            ("排除条件", risk.get("exclusion_condition")),
        )
        facts = "".join(f"<dt>{label}</dt><dd>{_escape(value)}</dd>" for label, value in fields)
        semantics_html = _upstream_semantics(risk.get("upstream_semantics"))
        unreachable_html = ""
        if risk.get("translation_status") == "Unreachable":
            unreachable_html = (
                f'<h4>不可达原因</h4><p>{_escape(risk.get("unreachable_reason"))}</p>'
                f'<h4>不可达证据</h4>{_evidence(risk.get("unreachable_evidence"))}'
            )
        cards.append(f'<details><summary>{_escape(risk.get("risk_id"), "未编号")} · {_escape(risk.get("title"))}</summary><div class="detail-body"><dl>{facts}</dl><h4>上游语义核对</h4>{semantics_html}{unreachable_html}<h4>证据</h4>{_evidence(risk.get("evidence"))}</div></details>')
    rendered_cards = "".join(cards) or '<p class="muted">未发现有源码或资料证据支撑的风险。</p>'
    dimension_summary = _table(("DFX 维度", "风险数", "风险编号"), _risk_dimension_rows(risks))
    body.append(f'<section id="risks"><a class="back" href="#top">回到顶部</a><h2>6. 六维 DFX 风险</h2>{dimension_summary}<h3>风险明细</h3>{rendered_cards}</section>')

    coverage = state.get("coverage_report") or state.get("coverage")
    if isinstance(coverage, Mapping) and any(key in coverage for key in ("matched", "ambiguous", "unmatched")):
        coverage_html = _table(("状态", "类型", "函数/分支", "执行次数", "Coverage 来源", "源码位置"), _coverage_rows(coverage), {2, 4, 5})
        coverage_html += '<p class="muted">未出现在 Coverage 文件中的函数或分支状态为“未提供/未知”，不按 0 次执行处理。</p>'
    else:
        coverage_html = _list(coverage, "未提供覆盖率文件或匹配结果。")
    coverage_decision_rows = [
        (item.get("unit_id"), item.get("coverage_id"), item.get("disposition"), item.get("test_case_ids"), item.get("reason"))
        for item in _items(state.get("coverage_decisions"))
        if isinstance(item, Mapping)
    ]
    coverage_html += '<h3>用例闭环</h3>' + _table(
        ("分析单元", "Coverage", "处理", "关联用例", "说明"),
        coverage_decision_rows,
        {0, 1, 2, 3},
    )
    body.append(f'<section id="coverage"><a class="back" href="#top">回到顶部</a><h2>7. 覆盖率缺口</h2>{coverage_html}</section>')

    case_parts = []
    for case in _items(state.get("test_cases")):
        if not isinstance(case, Mapping):
            case_parts.append(f"<p>{_escape(case)}</p>")
            continue
        steps = _items(case.get("steps")); expected = _items(case.get("expected_results"))
        if len(expected) == 1 and len(steps) > 1:
            rows = "".join(
                f"<tr><td>{index}</td><td>{_escape(step)}</td><td>{_escape(expected[0]) if index == 1 else '同一组共同预期'}</td></tr>"
                for index, step in enumerate(steps, 1)
            )
        else:
            rows = "".join(f"<tr><td>{index}</td><td>{_escape(step)}</td><td>{_escape(expected[index-1] if index-1 < len(expected) else '未提供对应预期结果（语义缺口）')}</td></tr>" for index, step in enumerate(steps, 1))
        case_parts.append(f'<details><summary>{_escape(case.get("test_case_id"), "未编号")} · {_escape(case.get("title"))}</summary><div class="detail-body"><dl><dt>用例层级</dt><dd>{_escape(case.get("case_type") or case.get("type") or case.get("test_type"))}</dd><dt>设计依据</dt><dd>{_escape("、".join(str(v) for v in _items(case.get("basis"))) or "未提供")}</dd><dt>关联输入</dt><dd>{_escape("、".join(str(v) for v in _items(case.get("linked_input_ids"))) or "无")}</dd><dt>关联风险</dt><dd>{_escape("、".join(str(v) for v in _items(case.get("linked_risk_ids"))) or "无")}</dd><dt>前置条件</dt><dd>{_list(case.get("preconditions"))}</dd></dl><table><thead><tr><th>#</th><th>操作目标</th><th>预期结果</th></tr></thead><tbody>{rows}</tbody></table><h4>观测方式</h4>{_list(case.get("observability"))}<h4>清理/恢复</h4>{_list(case.get("cleanup"))}</div></details>')
    rendered_cases = "".join(case_parts) or '<p class="muted">未生成测试用例。</p>'
    body.append(f'<section id="cases"><a class="back" href="#top">回到顶部</a><h2>8. 测试用例与风险映射</h2>{rendered_cases}</section>')

    unresolved_units = [unit for unit in _items(state.get("analysis_units")) if isinstance(unit, Mapping) and str(unit.get("status", "")).upper() not in {"", "PASS", "COMPLETED", "COMPLETE"}]
    incomplete_html = f'<h3>解析失败</h3>{_list(_parse_failures(state))}<h3>未读图片</h3>{_list(state.get("unread_images") or state.get("unparsed_images"))}<h3>运行错误</h3>{_list(state.get("errors"))}<h3>未完成分析单元</h3>{_list(unresolved_units)}'
    body.append(f'<section id="incomplete"><a class="back" href="#top">回到顶部</a><h2>9. 不完整项与未解析证据</h2>{incomplete_html}</section>')
    quality = state.get("quality_report") or {}
    final_tone = "danger" if incomplete else "ok"
    completeness = "INCOMPLETE" if incomplete else "COMPLETE"
    checks_html = _list(quality.get("checks"), "未记录检查项。") if isinstance(quality, Mapping) else ""
    unresolved_html = ""
    if incomplete and isinstance(quality, Mapping):
        semantic = quality.get("semantic_unresolved")
        diagnostics = quality.get("workflow_diagnostics")
        if semantic or diagnostics:
            unresolved_html = (
                f'<h3>语义待确认</h3>{_list(semantic)}'
                f'<h3>结构诊断</h3>{_list(diagnostics)}'
            )
        else:
            unresolved_html = f'<h3>未完成项</h3>{_list(quality.get("unresolved"))}'
    advisory_html = ""
    if isinstance(quality, Mapping) and quality.get("advisories"):
        advisory_html = f'<h3>建议告警</h3>{_list(quality.get("advisories"))}'
    body.append(f'<section id="quality"><a class="back" href="#top">回到顶部</a><h2>10. 质量门禁</h2><p class="summary {final_tone}">{_escape(_quality_summary(state, incomplete))}</p><h3>已完成检查</h3>{checks_html}{unresolved_html}{advisory_html}<p><span class="badge {final_tone}">{completeness}</span></p></section>')
    return _shell(title, incomplete, navigation, "".join(body))


def markdown_to_minimal_html(markdown: str) -> str:
    """Compatibility renderer for callers that only have Markdown text."""
    escaped = html.escape(markdown)
    return _shell("分析报告", True, "", f'<section><pre>{escaped}</pre></section>')


def write_reports(run_dir: str | Path, state: Mapping[str, Any]) -> tuple[Path, Path]:
    """Write the fixed V1 report filenames and return their paths."""
    output_dir = Path(run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "report.md"
    html_path = output_dir / "report.html"
    markdown = render_report(state)
    rendered_html = render_html_report(state)
    _atomic_write_text(markdown_path, markdown)
    _atomic_write_text(html_path, rendered_html)
    marker = {
        "files": ["report.md", "report.html"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_text(output_dir / "report-complete.json", json.dumps(marker, ensure_ascii=False, indent=2) + "\n")
    return markdown_path, html_path


def reports_are_complete(run_dir: str | Path) -> bool:
    output_dir = Path(run_dir)
    marker_path = output_dir / "report-complete.json"
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        return marker.get("files") == ["report.md", "report.html"] and all(
            (output_dir / name).is_file() for name in marker["files"]
        )
    except (OSError, ValueError, TypeError):
        return False


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
