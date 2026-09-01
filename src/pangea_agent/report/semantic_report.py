from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .html import (
    _atomic_write_text,
    _escape,
    _evidence,
    _list,
    _table,
    render_html_report as _render_base_html,
    reports_are_complete,
)
from .markdown import (
    _evidence_lines,
    _items,
    _markdown_table,
    _text,
    render_report as _render_base_markdown,
)


def _cases_by_scenario(state: Mapping[str, Any]) -> dict[str, list[str]]:
    links: dict[str, list[str]] = defaultdict(list)
    for case in _items(state.get("test_cases")):
        if not isinstance(case, Mapping):
            continue
        case_id = _text(case.get("test_case_id"), "未编号")
        for scenario_id in _items(case.get("scenario_ids")):
            key = _text(scenario_id, "")
            if key and case_id not in links[key]:
                links[key].append(case_id)
    return links


def _markdown_semantic_section(state: Mapping[str, Any]) -> str:
    branches = [
        item
        for item in _items(state.get("branch_decisions"))
        if isinstance(item, Mapping)
    ]
    scenarios = [
        item
        for item in _items(state.get("scenarios"))
        if isinstance(item, Mapping)
    ]
    cases_by_scenario = _cases_by_scenario(state)

    lines = ["### Branch 处置", ""]
    lines.extend(
        _markdown_table(
            ("分析单元", "Branch", "Flow", "处置", "关联 Scenario", "说明"),
            [
                (
                    item.get("unit_id"),
                    item.get("branch_id"),
                    item.get("flow_key"),
                    item.get("disposition"),
                    item.get("scenario_ids"),
                    item.get("reason"),
                )
                for item in branches
            ],
        )
    )

    lines.extend(["", "### 测试场景与追溯", ""])
    if not scenarios:
        lines.append("- 未形成测试场景。")
        return "\n".join(lines)

    for scenario in scenarios:
        scenario_id = _text(scenario.get("scenario_id"), "未编号")
        lines.extend(
            [
                f"#### {scenario_id} · {_text(scenario.get('title'))}",
                "",
                f"- **分析单元**：{_text(scenario.get('unit_id'))}",
                f"- **Readiness**：{_text(scenario.get('readiness'))}",
                f"- **业务入口**：{_text(scenario.get('business_entry'))}",
                f"- **关联 Branch**：{'、'.join(_text(item) for item in _items(scenario.get('branch_ids'))) or '无'}",
                f"- **关联 Coverage**：{'、'.join(_text(item) for item in _items(scenario.get('coverage_ids'))) or '无'}",
                f"- **关联风险**：{'、'.join(_text(item) for item in _items(scenario.get('linked_risk_ids'))) or '无'}",
                f"- **关联用例**：{'、'.join(cases_by_scenario.get(scenario_id, [])) or '无'}",
                "- **前置条件**：",
            ]
        )
        for item in _items(scenario.get("preconditions")) or ["未提供"]:
            lines.append(f"  - {_text(item)}")
        lines.append("- **测试人员动作**：")
        for item in _items(scenario.get("actions")) or ["未提供"]:
            lines.append(f"  - {_text(item)}")
        lines.append("- **外部判定**：")
        for item in _items(scenario.get("external_oracles")) or ["未提供"]:
            lines.append(f"  - {_text(item)}")
        lines.append("- **恢复**：")
        for item in _items(scenario.get("recovery")) or ["未提供"]:
            lines.append(f"  - {_text(item)}")
        lines.append("- **证据**：")
        lines.extend(_evidence_lines(scenario.get("evidence")))
        lines.append("")
    return "\n".join(lines).rstrip()


def render_report(state: Mapping[str, Any]) -> str:
    base = _render_base_markdown(state)
    marker = "## 7. 覆盖率缺口\n\n"
    section = _markdown_semantic_section(state)
    if marker not in base:
        return base + "\n\n" + section + "\n"
    return base.replace(marker, marker + section + "\n\n", 1)


def _html_semantic_section(state: Mapping[str, Any]) -> str:
    branches = [
        item
        for item in _items(state.get("branch_decisions"))
        if isinstance(item, Mapping)
    ]
    scenarios = [
        item
        for item in _items(state.get("scenarios"))
        if isinstance(item, Mapping)
    ]
    cases_by_scenario = _cases_by_scenario(state)

    branch_rows = [
        (
            item.get("unit_id"),
            item.get("branch_id"),
            item.get("flow_key"),
            item.get("disposition"),
            item.get("scenario_ids"),
            item.get("reason"),
        )
        for item in branches
    ]
    branch_html = _table(
        ("分析单元", "Branch", "Flow", "处置", "关联 Scenario", "说明"),
        branch_rows,
        {0, 1, 2, 3, 4},
    )

    scenario_parts: list[str] = []
    for scenario in scenarios:
        scenario_id = _text(scenario.get("scenario_id"), "未编号")
        facts = _table(
            ("项目", "内容"),
            [
                ("分析单元", scenario.get("unit_id")),
                ("Readiness", scenario.get("readiness")),
                ("业务入口", scenario.get("business_entry")),
                ("关联 Branch", scenario.get("branch_ids")),
                ("关联 Coverage", scenario.get("coverage_ids")),
                ("关联风险", scenario.get("linked_risk_ids")),
                ("关联用例", cases_by_scenario.get(scenario_id, [])),
            ],
            {1},
        )
        scenario_parts.append(
            f'<details><summary>{_escape(scenario_id)} · {_escape(scenario.get("title"))}</summary>'
            f'<div class="detail-body">{facts}'
            f'<h4>前置条件</h4>{_list(scenario.get("preconditions"))}'
            f'<h4>测试人员动作</h4>{_list(scenario.get("actions"))}'
            f'<h4>外部判定</h4>{_list(scenario.get("external_oracles"))}'
            f'<h4>恢复</h4>{_list(scenario.get("recovery"))}'
            f'<h4>证据</h4>{_evidence(scenario.get("evidence"))}'
            f'</div></details>'
        )
    scenarios_html = "".join(scenario_parts) or '<p class="muted">未形成测试场景。</p>'

    return (
        '<section id="scenarios"><a class="back" href="#top">回到顶部</a>'
        '<h2>Branch 处置与测试场景</h2>'
        '<h3>Branch 处置</h3>'
        f'{branch_html}'
        '<h3>测试场景与追溯</h3>'
        f'{scenarios_html}'
        '</section>'
    )


def render_html_report(state: Mapping[str, Any]) -> str:
    base = _render_base_html(state)
    nav_marker = '<a href="#coverage">覆盖率缺口</a>'
    if nav_marker in base:
        base = base.replace(
            nav_marker,
            '<a href="#scenarios">场景追溯</a>' + nav_marker,
            1,
        )
    section_marker = '<section id="coverage">'
    section = _html_semantic_section(state)
    if section_marker not in base:
        return base.replace("</main>", section + "</main>", 1)
    return base.replace(section_marker, section + section_marker, 1)


def write_reports(
    run_dir: str | Path,
    state: Mapping[str, Any],
) -> tuple[Path, Path]:
    output_dir = Path(run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "report.md"
    html_path = output_dir / "report.html"

    _atomic_write_text(markdown_path, render_report(state))
    _atomic_write_text(html_path, render_html_report(state))
    marker = {
        "files": ["report.md", "report.html"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_text(
        output_dir / "report-complete.json",
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
    )
    return markdown_path, html_path
