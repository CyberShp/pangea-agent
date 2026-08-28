from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pangea_agent.graph.state import PangeaState


DFX_DIMENSIONS = (
    "功能与状态",
    "资源与规格",
    "性能与压力",
    "并发与异常",
    "升级与兼容",
    "可靠性与一致性",
)

MODE_LABELS = {
    "module_analysis": "模块分析",
    "mr_analysis": "MR 修改影响分析",
}

REASON_LABELS = {
    "target_context": "与分析对象直接相关",
}

BOUNDARY_LABELS = {
    "explicit scope + direct external callers + target-related config/docs/tests; no recursive caller expansion":
        "用户指定范围 + 直接调用者 + 与分析对象相关的配置、文档和测试；不递归扩展调用链",
    "source_scope = explicit scope + declared implementations; context_scope = direct function-pointer implementations + callers + target-related config/docs/tests":
        "源码范围 = 用户指定范围 + 声明的直接实现；上下文范围 = 函数指针的直接实现 + 直接调用者 + 相关配置、文档和测试",
    "source_scope = explicit scope + declared implementations; context_scope = called inline headers + direct function-pointer implementations + callers + target-related config/docs/tests":
        "源码范围 = 用户指定范围 + 声明的直接实现；上下文范围 = 当前源码实际调用的内联头文件 + 函数指针的直接实现 + 直接调用者 + 相关配置、文档和测试",
}


def _text(value: Any, default: str = "未提供") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _mapping_lines(value: Mapping[str, Any], prefix: str = "- ") -> list[str]:
    lines: list[str] = []
    for key, item in value.items():
        if isinstance(item, Mapping):
            lines.append(f"{prefix}**{key}**")
            lines.extend(_mapping_lines(item, prefix="  - "))
        elif isinstance(item, (list, tuple, set)):
            rendered = "、".join(_text(entry) for entry in item) or "无"
            lines.append(f"{prefix}**{key}**：{rendered}")
        else:
            lines.append(f"{prefix}**{key}**：{_text(item)}")
    return lines


def _report_title(state: Mapping[str, Any]) -> str:
    contract = state.get("task_contract") or {}
    target = contract.get("target") if isinstance(contract, Mapping) else None
    return f"{_text(target, '未命名对象')} 分析报告"


def _table_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        rendered = "<br>".join(_text(item) for item in value)
    elif isinstance(value, Mapping):
        rendered = "；".join(f"{key}={_text(item)}" for key, item in value.items())
    else:
        rendered = _text(value)
    return rendered.replace("|", "\\|").replace("\n", "<br>")


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> list[str]:
    if not rows:
        return ["- 无"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_table_text(cell) for cell in row) + " |" for row in rows)
    return lines


def _risk_dimension_rows(risks: list[Any]) -> list[tuple[Any, ...]]:
    rows = []
    for dimension in DFX_DIMENSIONS:
        risk_ids = [
            _text(risk.get("risk_id"), "未编号")
            for risk in risks
            if isinstance(risk, Mapping) and dimension in _items(risk.get("dfx"))
        ]
        rows.append((dimension, len(risk_ids), risk_ids or "未发现有证据支撑的风险"))
    return rows


def _coverage_rows(coverage: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    labels = {"matched": "已匹配", "ambiguous": "匹配不唯一", "unmatched": "未匹配"}
    rows: list[tuple[Any, ...]] = []
    for status in ("matched", "ambiguous", "unmatched"):
        for record in _items(coverage.get(status)):
            if not isinstance(record, Mapping):
                rows.append((labels[status], "未说明", record, "未说明", "未说明", "未说明"))
                continue
            coverage_type = record.get("coverage_type") or "未说明"
            subject = record.get("function") or record.get("branch_id") or "未说明"
            if coverage_type == "branch" and record.get("branch_id"):
                subject = f"{subject} / {record.get('branch_id')}"
            counts = (
                f"true={_text(record.get('true_count'))}, false={_text(record.get('false_count'))}"
                if coverage_type == "branch"
                else _text(record.get("count"))
            )
            source = " · ".join(
                part for part in (
                    _text(record.get("source"), ""),
                    f"{_text(record.get('sheet'), '')}:{_text(record.get('row'), '')}".strip(":"),
                ) if part
            ) or "未说明"
            locations = [
                f"{_text(match.get('repo_id'))}:{_text(match.get('path'))}:{_text(match.get('line'))}"
                for match in _items(record.get("matches"))
                if isinstance(match, Mapping)
            ]
            rows.append((labels[status], coverage_type, subject, counts, source, locations or "无唯一源码位置"))
    return rows


def _contract_rows(state: Mapping[str, Any], contract: Mapping[str, Any]) -> list[tuple[Any, Any]]:
    return [
        ("运行编号", state.get("run_id") or contract.get("run_id")),
        ("分析对象", contract.get("target")),
        ("分析类型", MODE_LABELS.get(str(contract.get("mode")), contract.get("mode"))),
        ("源码仓", contract.get("repositories") or contract.get("repository")),
        ("分析重点", contract.get("focus")),
        ("用户指定源码", contract.get("source_scope")),
        ("数据目录", contract.get("data_root") or state.get("data_root")),
    ]


def _reason_text(reason: Any) -> str:
    raw = _text(reason, "未说明")
    if raw.startswith("direct_caller:"):
        return f"直接调用 {raw.split(':', 1)[1]}"
    if raw.startswith("direct_reference:"):
        return f"直接引用 {raw.split(':', 1)[1]}"
    if raw.startswith("function_pointer_implementation:"):
        return f"函数指针直接实现 {raw.split(':', 1)[1]}"
    if raw.startswith("direct_inline_dependency:"):
        return f"当前源码调用的内联实现 {raw.split(':', 1)[1]}"
    return REASON_LABELS.get(raw, raw)


def _boundary_text(boundary: Any) -> str:
    raw = _text(boundary)
    return BOUNDARY_LABELS.get(raw, raw)


def _scope_rows(state: Mapping[str, Any], contract: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    repositories = _items(contract.get("repositories") or contract.get("repository"))
    rows: list[tuple[Any, ...]] = []
    for repo_id in repositories or ["未指定"]:
        rows.extend(
            ("用户指定源码", repo_id, path, "任务契约指定")
            for path in _items(contract.get("source_scope"))
        )
    expansion = state.get("scope_expansion") or {}
    if isinstance(expansion, Mapping):
        rows.extend(
            ("自动扩展源码", item.get("repo_id"), item.get("path"), _reason_text(item.get("reason")))
            for item in _items(expansion.get("added_files"))
            if isinstance(item, Mapping)
        )
        rows.extend(
            ("上游语义", item.get("repo_id"), item.get("path"), _reason_text(item.get("reason")))
            for item in _items(expansion.get("context_files"))
            if isinstance(item, Mapping)
        )
    return rows


def _repository_rows(state: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    rows = []
    for repository in _items(state.get("repositories")):
        if not isinstance(repository, Mapping):
            rows.append((repository, "未记录", "未记录", "未记录"))
            continue
        git = repository.get("git") or {}
        rows.append((
            repository.get("repo_id"),
            repository.get("source_root"),
            git.get("commit", "未记录") if isinstance(git, Mapping) else "未记录",
            "有本地修改" if isinstance(git, Mapping) and git.get("dirty") else "已记录",
        ))
    return rows


def _quality_summary(state: Mapping[str, Any], incomplete: bool) -> str:
    inventory = state.get("inventory") or {}
    expansion = state.get("scope_expansion") or {}
    context_count = len(_items(expansion.get("context_files"))) if isinstance(expansion, Mapping) else 0
    completed_units = state.get("completed_analysis_units")
    completed_count = (
        len(_items(completed_units))
        if completed_units is not None
        else len(_items(state.get("analysis_units")))
    )
    counts = (
        f"完成 {completed_count} 个分析单元，"
        f"覆盖 {inventory.get('file_count', 0) if isinstance(inventory, Mapping) else 0} 个 C/C++ 文件"
        f"和 {context_count} 个上游语义文件；"
        f"形成 {len(_items(state.get('business_flows') or state.get('flows')))} 条业务流程、"
        f"{len(_items(state.get('risks')))} 个风险、{len(_items(state.get('test_cases')))} 个测试用例。"
    )
    if incomplete:
        gaps = []
        if _items(_parse_failures(state)):
            gaps.append(f"解析失败 {len(_items(_parse_failures(state)))} 项")
        if _items(state.get("unread_images") or state.get("unparsed_images")):
            gaps.append(f"未读图片 {len(_items(state.get('unread_images') or state.get('unparsed_images')))} 项")
        if _items(state.get("errors")):
            gaps.append(f"运行错误 {len(_items(state.get('errors')))} 项")
        quality = state.get("quality_report") or {}
        semantic = _items(quality.get("semantic_unresolved"))
        diagnostics = _items(quality.get("workflow_diagnostics"))
        if semantic:
            gaps.append(f"语义待确认 {len(semantic)} 项")
        if diagnostics:
            occurrences = quality.get("diagnostic_occurrence_count", len(diagnostics))
            gaps.append(f"结构诊断 {len(diagnostics)} 项（共出现 {occurrences} 次）")
        if not semantic and not diagnostics:
            unresolved = _items(quality.get("unresolved"))
            if unresolved:
                gaps.append(f"待确认事项 {len(unresolved)} 项")
        return f"报告存在未完成范围。{counts}具体缺口：{'、'.join(gaps) or '见不完整项章节'}。"
    return f"质量门禁已通过。{counts}解析失败、未读图片和运行错误均为 0。"


def _evidence_lines(evidence: Any) -> list[str]:
    lines: list[str] = []
    for entry in _items(evidence):
        if isinstance(entry, Mapping):
            location = entry.get("location") or entry.get("source") or entry.get("path")
            observation = entry.get("observation") or entry.get("summary") or entry.get("reason")
            chunk_id = entry.get("chunk_id") or entry.get("evidence_id")
            label = " · ".join(_text(part, "") for part in (location, chunk_id) if part)
            pending = entry.get("status") == "pending_confirmation"
            pending_text = f"（证据待确认：{_text(entry.get('pending_reason'))}）" if pending else ""
            lines.append(f"  - `{label or '位置未提供'}`：{_text(observation)}{pending_text}")
        else:
            lines.append(f"  - {_text(entry)}")
    return lines or ["  - 未提供源码证据"]


def _status(state: Mapping[str, Any]) -> tuple[str, bool]:
    quality = state.get("quality_report") or {}
    quality_status = str(quality.get("status", "UNKNOWN")).upper()
    run_status = str(state.get("run_status", "")).upper()
    inventory = state.get("inventory") or {}
    has_gaps = bool(
        state.get("incomplete")
        or state.get("parse_failures")
        or state.get("parser_failures")
        or (inventory.get("parse_failures") if isinstance(inventory, Mapping) else None)
        or state.get("unread_images")
        or state.get("unparsed_images")
        or state.get("errors")
        or quality.get("unresolved")
    )
    incomplete = quality_status in {"REWORK", "UNRESOLVED", "UNKNOWN"} or run_status in {
        "INCOMPLETE",
        "PARTIAL",
        "FAILED",
        "UNRESOLVED",
    } or has_gaps
    return quality_status, incomplete


def _parse_failures(state: Mapping[str, Any]) -> Any:
    inventory = state.get("inventory") or {}
    return (
        state.get("parse_failures")
        or state.get("parser_failures")
        or (inventory.get("parse_failures") if isinstance(inventory, Mapping) else None)
    )


def _append_list(lines: list[str], values: Any, empty: str = "- 无") -> None:
    items = _items(values)
    if not items:
        lines.append(empty)
        return
    for item in items:
        if isinstance(item, Mapping):
            title = item.get("title") or item.get("name") or item.get("path") or item.get("id")
            lines.append(f"- **{_text(title)}**")
            lines.extend(_mapping_lines({k: v for k, v in item.items() if k not in {"title", "name"}}, "  - "))
        else:
            lines.append(f"- {_text(item)}")


def render_report(state: "PangeaState | Mapping[str, Any]") -> str:
    """Render the complete V1 Markdown report from workflow state.

    Unknown optional fields are omitted honestly instead of being inferred.
    """

    _, incomplete = _status(state)
    contract = state.get("task_contract") or {}
    title = _report_title(state)
    lines = [
        f"# {title}",
        "",
        f"> **{'INCOMPLETE' if incomplete else 'COMPLETE'}**",
        f"> {_quality_summary(state, incomplete)}",
        "",
        "## 1. 任务契约",
        "",
    ]
    if isinstance(contract, Mapping):
        lines.extend(_markdown_table(("项目", "内容"), _contract_rows(state, contract)))
    else:
        lines.append(f"- {_text(contract)}")

    lines.extend(["", "## 2. 分析范围与排除项", "", "### 纳入范围", ""])
    if isinstance(contract, Mapping):
        lines.extend(_markdown_table(("类别", "源码仓", "路径", "纳入原因"), _scope_rows(state, contract)))
    expansion = state.get("scope_expansion") or {}
    if isinstance(expansion, Mapping) and expansion.get("boundary"):
        lines.extend(["", f"扩展边界：{_boundary_text(expansion.get('boundary'))}"])
    lines.extend(["", "### 源码仓", ""])
    lines.extend(_markdown_table(("源码仓", "本地目录", "版本", "状态"), _repository_rows(state)))
    lines.extend(["", "### 明确排除", ""])
    exclusions = state.get("excluded_scope") or state.get("exclusions")
    _append_list(lines, exclusions)
    material_decisions = _items(state.get("material_decisions"))
    if material_decisions:
        lines.extend(["", "### 资料采用与排除结论", ""])
        lines.extend(_markdown_table(("分析单元", "资料", "处理", "理由"), [
            (item.get("unit_id"), item.get("path"), item.get("decision"), item.get("reason"))
            for item in material_decisions
            if isinstance(item, Mapping)
        ]))
    material_evidence = _items(state.get("material_evidence"))
    if material_evidence:
        lines.extend(["", "### 资料引用", ""])
        lines.extend(_markdown_table(("分析单元", "引用位置", "使用结论", "状态"), [
            (item.get("unit_id"), item.get("location") or item.get("chunk_id"), item.get("observation"), item.get("status"))
            for item in material_evidence
            if isinstance(item, Mapping)
        ]))
    summaries = _items(state.get("analysis_summaries"))
    if summaries:
        lines.extend(["", "### 分析结论摘要", ""])
        lines.extend(_markdown_table(("分析单元", "Worker", "结论"), [
            (item.get("unit_id"), item.get("worker_id"), item.get("summary"))
            for item in summaries
            if isinstance(item, Mapping)
        ]))
    if state.get("source_manifest"):
        lines.extend(["", "### 源码清单摘要", ""])
        manifest = state["source_manifest"]
        inventory = state.get("inventory") or {}
        if isinstance(manifest, Mapping):
            lines.extend(_markdown_table(("项目", "结果"), [
                ("C/C++ 文件数", inventory.get("file_count", 0) if isinstance(inventory, Mapping) else 0),
                ("结构化解析", "完整" if isinstance(inventory, Mapping) and inventory.get("structural_parse_complete") else "存在缺口"),
                ("文档告警", len(_items(manifest.get("warnings")))),
                ("缺少依赖", len(_items(manifest.get("missing_dependencies")))),
                ("图片附件", len(_items(manifest.get("attachments")))),
                ("Coverage 记录", len(_items(manifest.get("coverage_records")))),
            ]))
        else:
            lines.append(f"- {_text(manifest)}")

    lines.extend(["", "## 3. 业务流程与关键函数证据", ""])
    flows = state.get("business_flows") or state.get("flows") or state.get("flow_diagrams")
    if not flows:
        lines.append("- 未提供业务流程产物。")
    for index, flow in enumerate(_items(flows), 1):
        if not isinstance(flow, Mapping):
            lines.extend([f"### 3.{index} 流程", "", _text(flow), ""])
            continue
        lines.extend([f"### 3.{index} {_text(flow.get('title') or flow.get('name'), '业务流程')}", ""])
        if flow.get("description"):
            lines.extend([_text(flow["description"]), ""])
        steps = flow.get("steps") or flow.get("business_steps")
        for step_index, step in enumerate(_items(steps), 1):
            lines.append(f"{step_index}. {_text(step)}")
        mermaid = flow.get("mermaid") or flow.get("diagram")
        if mermaid:
            lines.extend(["", "```mermaid", _text(mermaid, ""), "```"])
        lines.extend(["", "**关键函数证据**"])
        lines.extend(_evidence_lines(flow.get("evidence") or flow.get("function_evidence")))
        lines.append("")

    visual_findings = _items(state.get("visual_findings"))
    if visual_findings:
        lines.extend(["### 图片分析发现", ""])
        for finding in visual_findings:
            if isinstance(finding, Mapping):
                attachment = finding.get("attachment_path") or finding.get("path") or finding.get("source")
                observation = finding.get("observation") or finding.get("summary")
                lines.append(f"- `{_text(attachment, '附件路径未提供')}`：{_text(observation)}")
            else:
                lines.append(f"- {_text(finding)}")
        lines.append("")

    lines.extend(["## 4. 资料与代码一致性", ""])
    input_decisions = _items(state.get("input_decisions"))
    lines.extend(_markdown_table(("分析单元", "资料条目", "结论", "说明"), [
        (item.get("unit_id"), item.get("item_id"), item.get("disposition"), item.get("conclusion"))
        for item in input_decisions
        if isinstance(item, Mapping)
    ]))

    lines.extend(["", "## 5. 缺陷机理检查", ""])
    mechanism_decisions = _items(state.get("mechanism_decisions"))
    if not mechanism_decisions:
        lines.append("- 当前范围没有关联的已确认缺陷机理。")
    for item in mechanism_decisions:
        if not isinstance(item, Mapping):
            continue
        lines.extend([
            f"### {_text(item.get('mechanism_id'))}",
            "",
            f"- **分析单元**：{_text(item.get('unit_id'))}",
            f"- **判断**：{_text(item.get('disposition'))}",
            f"- **当前因果链**：{' → '.join(_text(value) for value in _items(item.get('current_causal_chain'))) or '无'}",
            f"- **关联用例**：{'、'.join(_text(value) for value in _items(item.get('test_case_ids'))) or '无'}",
            f"- **结论**：{_text(item.get('conclusion'))}",
            "- **源码证据**：",
        ])
        lines.extend(_evidence_lines(item.get("evidence")))
        lines.append("")

    lines.extend(["## 6. 六维 DFX 风险", ""])
    risks = _items(state.get("risks"))
    lines.extend(_markdown_table(("DFX 维度", "风险数", "风险编号"), _risk_dimension_rows(risks)))
    lines.extend(["", "### 风险明细", ""])
    if not risks:
        lines.extend(["- 未发现有源码或资料证据支撑的风险。", ""])
    for risk in risks:
        if not isinstance(risk, Mapping):
            lines.extend([f"- {_text(risk)}", ""])
            continue
        risk_id = _text(risk.get("risk_id"), "未编号")
        lines.extend(
            [
                f"#### {risk_id} · {_text(risk.get('title'))}",
                "",
                f"- **DFX 维度**：{'、'.join(_text(item) for item in _items(risk.get('dfx'))) or '未提供'}",
                f"- **严重度**：{_text(risk.get('severity'))}",
                f"- **置信度**：{_text(risk.get('confidence'))}",
                f"- **风险状态**：{_text(risk.get('status'))}",
                f"- **测试转化状态**：{_text(risk.get('translation_status'))}",
                f"- **复现条件**：{_text(risk.get('trigger') or risk.get('reproduction_condition'))}",
                f"- **系统结果**：{_text(risk.get('system_result'))}",
                f"- **外部观测**：{_text(risk.get('external_observation'))}",
                f"- **排除条件**：{_text(risk.get('exclusion_condition'))}",
                "- **上游语义核对**：",
            ]
        )
        semantics = risk.get("upstream_semantics")
        if isinstance(semantics, Mapping):
            lines.extend([
                f"  - 入口可达性：{_text(semantics.get('reachability'))}",
                f"  - 调用方限制/补救：{_text(semantics.get('caller_constraints'))}",
                f"  - 规格或高层 API：{_text(semantics.get('documented_behavior'))}",
                f"  - 已有测试：{_text(semantics.get('existing_tests'))}",
                f"  - 结论：{_text(semantics.get('conclusion'))}",
            ])
        else:
            lines.append("  - 未提供。")
        lines.append("- **证据**：")
        lines.extend(_evidence_lines(risk.get("evidence")))
        lines.append("")

    lines.extend(["## 7. 覆盖率缺口", ""])
    coverage = state.get("coverage_report") or state.get("coverage") or {}
    if isinstance(coverage, Mapping):
        if coverage and any(key in coverage for key in ("matched", "ambiguous", "unmatched")):
            lines.extend(_markdown_table(("状态", "类型", "函数/分支", "执行次数", "Coverage 来源", "源码位置"), _coverage_rows(coverage)))
            lines.extend(["", "未出现在 Coverage 文件中的函数或分支状态为“未提供/未知”，不按 0 次执行处理。"])
        else:
            lines.extend(_mapping_lines(coverage) if coverage else ["- 未提供覆盖率文件或匹配结果。"])
    else:
        _append_list(lines, coverage, "- 未提供覆盖率文件或匹配结果。")
    lines.extend(["", "### 用例闭环", ""])
    lines.extend(_markdown_table(("分析单元", "Coverage", "处理", "关联用例", "说明"), [
        (
            item.get("unit_id"),
            item.get("coverage_id"),
            item.get("disposition"),
            item.get("test_case_ids"),
            item.get("reason"),
        )
        for item in _items(state.get("coverage_decisions"))
        if isinstance(item, Mapping)
    ]))

    lines.extend(["", "## 8. 测试用例与风险映射", ""])
    cases = _items(state.get("test_cases"))
    if not cases:
        lines.append("- 未生成测试用例。")
    for case in cases:
        if not isinstance(case, Mapping):
            lines.append(f"- {_text(case)}")
            continue
        case_id = _text(case.get("test_case_id"), "未编号")
        lines.extend(
            [
                f"### {case_id} · {_text(case.get('title'))}",
                "",
                f"- **用例类型**：{_text(case.get('case_type') or case.get('type') or case.get('test_type'))}",
                f"- **设计依据**：{'、'.join(_text(item) for item in _items(case.get('basis'))) or '未提供'}",
                f"- **关联输入**：{', '.join(_text(item) for item in _items(case.get('linked_input_ids'))) or '无'}",
                f"- **关联风险**：{', '.join(_text(item) for item in _items(case.get('linked_risk_ids'))) or '无'}",
                "- **前置条件**：",
            ]
        )
        for item in _items(case.get("preconditions")):
            lines.append(f"  - {_text(item)}")
        lines.append("- **步骤与预期结果**：")
        steps = _items(case.get("steps"))
        expected = _items(case.get("expected_results"))
        if steps and len(expected) == 1 and len(steps) > 1:
            for index, step in enumerate(steps, 1):
                lines.append(f"  {index}. **操作目标**：{_text(step)}")
            lines.append(f"  - **上述步骤共同预期**：{_text(expected[0])}")
        elif steps:
            for index, step in enumerate(steps, 1):
                result = expected[index - 1] if index - 1 < len(expected) else "未提供对应预期结果（语义缺口）"
                lines.append(f"  {index}. **操作目标**：{_text(step)}  ")
                lines.append(f"     **预期结果**：{_text(result)}")
        elif expected:
            lines.append(f"  - 多步骤共同预期：{'；'.join(_text(item) for item in expected)}")
        else:
            lines.append("  - 未提供")
        lines.append("- **观测方式**：")
        for item in _items(case.get("observability")) or ["未提供"]:
            lines.append(f"  - {_text(item)}")
        lines.append("- **清理/恢复**：")
        for item in _items(case.get("cleanup")) or ["未提供"]:
            lines.append(f"  - {_text(item)}")
        lines.append("")

    lines.extend(["## 9. 不完整项与未解析证据", "", "### 解析失败", ""])
    _append_list(lines, _parse_failures(state))
    lines.extend(["", "### 未读图片", ""])
    _append_list(lines, state.get("unread_images") or state.get("unparsed_images"))
    lines.extend(["", "### 运行错误与未完成分析单元", ""])
    _append_list(lines, state.get("errors"))
    unresolved_units = [
        unit
        for unit in _items(state.get("analysis_units"))
        if isinstance(unit, Mapping) and str(unit.get("status", "")).upper() not in {"", "PASS", "COMPLETED", "COMPLETE"}
    ]
    _append_list(lines, unresolved_units)

    lines.extend(["", "## 10. 质量门禁", ""])
    quality = state.get("quality_report") or {}
    lines.append(_quality_summary(state, incomplete))
    if isinstance(quality, Mapping) and quality.get("checks"):
        lines.extend(["", "### 已完成检查", ""])
        _append_list(lines, quality.get("checks"))
    if incomplete and isinstance(quality, Mapping) and quality.get("unresolved"):
        semantic = quality.get("semantic_unresolved")
        diagnostics = quality.get("workflow_diagnostics")
        if semantic:
            lines.extend(["", "### 语义待确认", ""])
            _append_list(lines, semantic)
        if diagnostics:
            lines.extend(["", "### 结构诊断", ""])
            _append_list(lines, diagnostics)
        if not semantic and not diagnostics:
            lines.extend(["", "### 未完成项", ""])
            _append_list(lines, quality.get("unresolved"))
    if isinstance(quality, Mapping) and quality.get("advisories"):
        lines.extend(["", "### 建议告警", ""])
        _append_list(lines, quality.get("advisories"))
    lines.extend(["", f"**最终状态：{'INCOMPLETE' if incomplete else 'COMPLETE'}**", ""])
    return "\n".join(lines)
