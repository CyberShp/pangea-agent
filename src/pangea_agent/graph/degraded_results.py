from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pangea_agent.models.analysis import (
    AnalysisTask,
    CodeFlow,
    CoverageDecision,
    GeneratedTestCase,
    InputDecision,
    MechanismDecision,
    PlanningResult,
    PlanningResultV2,
    PlanningTask,
    ProposedUnit,
    ProposedUnitV2,
    ReviewFindingDecision,
    RiskFinding,
    SourceEvidence,
    UnitSemanticResult,
)


CANONICAL_STEP_KINDS = {
    "entry", "main", "branch", "error", "propagation", "recovery", "exit"
}
BRANCH_LIKE_KINDS = {"assert", "check", "condition", "decision", "logic"}
MAX_FALLBACK_BRANCHES = 24


def normalize_analysis_result(
    task: AnalysisTask,
    raw_result: Any,
    inventory: Mapping[str, Any],
    selected_inputs: Mapping[str, Any],
    warnings: list[str],
) -> UnitSemanticResult:
    """Normalize deterministic structure before validating Agent semantics."""

    if not isinstance(raw_result, Mapping):
        raise ValueError("Analysis 结果必须是一个 JSON 对象")
    summary = str(raw_result.get("summary") or "").strip()
    if not summary:
        raise ValueError("Analysis 结果缺少非空 summary")
    flows = _recover_flows(task, raw_result.get("flows"), inventory, fallback=False)
    if not flows:
        raise ValueError("Analysis 结果没有可消费且带源码证据的 flow")

    result = _analysis_result_from_raw(
        task,
        raw_result,
        flows,
        selected_inputs,
        warnings,
    )
    return _apply_analysis_safety_net(
        task,
        result,
        inventory,
        selected_inputs,
        warnings,
    )


def build_degraded_planning_result(
    task: PlanningTask,
    compact_metadata: Mapping[str, Any],
    asset_inputs: Mapping[str, Any],
    coverage_gaps: list[dict],
    reason: str,
) -> PlanningResult | PlanningResultV2:
    """Create a complete source assignment when Planning cannot serialize one."""

    owned = [
        (str(item["repo_id"]), str(item["path"]))
        for item in compact_metadata.get("owned_source_paths", [])
        if isinstance(item, Mapping) and item.get("repo_id") and item.get("path")
    ]
    if not owned:
        repo_id = task.repositories[0].repo_id
        owned = [(repo_id, path) for path in task.requested_scope]
    by_repo: dict[str, list[str]] = {}
    for repo_id, path in owned:
        by_repo.setdefault(repo_id, []).append(path)

    asset_ids = [
        item_id for item_id, item in asset_inputs.items()
        if not isinstance(item, Mapping) or item.get("item_type") != "historical_defect"
    ]
    mechanism_ids = [
        item_id for item_id, item in asset_inputs.items()
        if isinstance(item, Mapping) and item.get("item_type") == "historical_defect"
    ]
    coverage_by_repo = {repo_id: [] for repo_id in by_repo}
    for gap in coverage_gaps:
        matches = gap.get("matches", []) if isinstance(gap, Mapping) else []
        match = matches[0] if len(matches) == 1 and isinstance(matches[0], Mapping) else {}
        repo_id = str(match.get("repo_id") or next(iter(by_repo)))
        if repo_id not in coverage_by_repo:
            repo_id = next(iter(by_repo))
        if gap.get("coverage_id"):
            coverage_by_repo[repo_id].append(str(gap["coverage_id"]))

    unresolved = [
        "Planning 结构化结果在一次自动修复后仍不可用；"
        f"Workflow 已按冻结源码归属生成降级单元。原因：{reason}"
    ]
    if task.result_contract_version == "2.0":
        units = []
        source_ownership = {}
        for index, (repo_id, paths) in enumerate(by_repo.items(), 1):
            unit_key = f"FALLBACK-{index:02d}"
            units.append(ProposedUnitV2(
                unit_key=unit_key,
                repo_id=repo_id,
                title=f"{task.target} · {repo_id}",
                rationale="Planning 降级后按源码仓保持完整归属",
                asset_item_ids=asset_ids if index == 1 else [],
                mechanism_ids=mechanism_ids if index == 1 else [],
                coverage_ids=coverage_by_repo[repo_id],
            ))
            source_ownership.update({f"{repo_id}:{path}": unit_key for path in paths})
        return PlanningResultV2(
            summary="按冻结源码归属生成的降级规划",
            units=units,
            source_ownership=source_ownership,
            unresolved=unresolved,
        )

    return PlanningResult(
        summary="按冻结源码归属生成的降级规划",
        units=[
            ProposedUnit(
                repo_id=repo_id,
                title=f"{task.target} · {repo_id}",
                source_scope=paths,
                rationale="Planning 降级后按源码仓保持完整归属",
                asset_item_ids=asset_ids if index == 1 else [],
                mechanism_ids=mechanism_ids if index == 1 else [],
                coverage_ids=coverage_by_repo[repo_id],
            )
            for index, (repo_id, paths) in enumerate(by_repo.items(), 1)
        ],
        unresolved=unresolved,
    )


def build_degraded_analysis_result(
    task: AnalysisTask,
    raw_result: Any,
    inventory: Mapping[str, Any],
    selected_inputs: Mapping[str, Any],
    reason: str,
) -> UnitSemanticResult:
    """Preserve usable Agent output and add structural test fallbacks."""

    raw = raw_result if isinstance(raw_result, Mapping) else {}
    warnings: list[str] = []
    flows = _recover_flows(task, raw.get("flows"), inventory, fallback=True)
    result = _analysis_result_from_raw(
        task,
        raw,
        flows,
        selected_inputs,
        warnings,
        fallback_summary=f"{task.unit.title} 的降级分析结果",
    )
    result = _apply_analysis_safety_net(
        task,
        result,
        inventory,
        selected_inputs,
        warnings,
    )

    unresolved = [
        f"{task.unit.unit_id}: Analysis 结构化结果在一次自动修复后仍不可用；"
        f"Workflow 已保留可识别内容并生成分支/Coverage 兜底用例。原因：{reason}"
    ]
    raw_unresolved = raw.get("unresolved")
    if isinstance(raw_unresolved, list):
        unresolved.extend(str(item) for item in raw_unresolved if str(item).strip())

    return result.model_copy(
        update={"unresolved": [*result.unresolved, *unresolved]},
        deep=True,
    )


def _analysis_result_from_raw(
    task: AnalysisTask,
    raw: Mapping[str, Any],
    flows: list[CodeFlow],
    selected_inputs: Mapping[str, Any],
    warnings: list[str],
    fallback_summary: str | None = None,
) -> UnitSemanticResult:
    risks = _recover_risks(task, raw.get("risks"))
    cases = _recover_cases(
        raw.get("test_cases"),
        flows,
        risks,
        selected_inputs,
        warnings,
    )
    used_keys = {case.case_key for case in cases}
    legacy_risk_cases = _legacy_risk_cases(
        raw.get("risks"),
        {risk.risk_key for risk in risks},
        flows[0].flow_key,
        used_keys,
    )
    cases.extend(legacy_risk_cases)
    return UnitSemanticResult(
        summary=str(raw.get("summary") or fallback_summary or "").strip(),
        flows=flows,
        input_decisions=_recover_models(task, raw.get("input_decisions"), InputDecision),
        coverage_decisions=_recover_models(
            task, raw.get("coverage_decisions"), CoverageDecision
        ),
        mechanism_decisions=_recover_models(
            task, raw.get("mechanism_decisions"), MechanismDecision
        ),
        risks=risks,
        test_cases=cases,
        review_finding_decisions=_recover_models(
            task,
            raw.get("review_finding_decisions"),
            ReviewFindingDecision,
        ),
        unresolved=[
            str(item) for item in raw.get("unresolved", [])
            if str(item).strip()
        ] if isinstance(raw.get("unresolved"), list) else [],
    )


def _recover_models(task: AnalysisTask, value: Any, model_type) -> list:
    recovered = []
    if not isinstance(value, list):
        return recovered
    allowed = set(model_type.model_fields)
    for item in value:
        if not isinstance(item, Mapping):
            continue
        payload = {key: item[key] for key in allowed if key in item}
        for name in ("evidence", "unreachable_evidence"):
            if name in allowed and name in item:
                payload[name] = _evidence_list(
                    task,
                    item[name],
                    str(item.get("conclusion") or "源码证据"),
                    force_task_repo=model_type is not ReviewFindingDecision,
                )
        if model_type in {CoverageDecision, MechanismDecision}:
            payload.pop("test_case_keys", None)
        try:
            recovered.append(model_type.model_validate(payload))
        except ValueError:
            continue
    return recovered


def _recover_risks(task: AnalysisTask, value: Any) -> list[RiskFinding]:
    if not isinstance(value, list):
        return []
    recovered = []
    used_keys: set[str] = set()
    allowed = set(RiskFinding.model_fields)
    for item in value:
        if not isinstance(item, Mapping):
            continue
        payload = {key: item[key] for key in allowed if key in item}
        payload.setdefault("title", item.get("description"))
        payload.setdefault("dfx", item.get("dimensions"))
        payload.setdefault("trigger", item.get("reproduction_conditions"))
        if isinstance(payload.get("dfx"), str):
            payload["dfx"] = [payload["dfx"]]
        if isinstance(payload.get("severity"), str):
            payload["severity"] = payload["severity"].title()
        if isinstance(payload.get("confidence"), str):
            payload["confidence"] = payload["confidence"].lower()
        payload["evidence"] = _evidence_list(
            task,
            item.get("evidence"),
            str(payload.get("title") or "风险源码证据"),
        )
        if "unreachable_evidence" in item:
            payload["unreachable_evidence"] = _evidence_list(
                task,
                item.get("unreachable_evidence"),
                "不可达处置源码证据",
            )
        try:
            risk = RiskFinding.model_validate(payload)
        except ValueError:
            continue
        key = _unique_key(risk.risk_key, used_keys)
        used_keys.add(key)
        recovered.append(
            risk if key == risk.risk_key else risk.model_copy(update={"risk_key": key})
        )
    return recovered


def _recover_flows(
    task: AnalysisTask,
    value: Any,
    inventory: Mapping[str, Any],
    *,
    fallback: bool,
) -> list[CodeFlow]:
    flows: list[CodeFlow] = []
    used_keys: set[str] = set()
    if isinstance(value, list):
        for index, item in enumerate(value, 1):
            flow = _recover_flow(task, item, index)
            if flow is not None:
                key = _unique_key(flow.flow_key, used_keys)
                used_keys.add(key)
                if key != flow.flow_key:
                    flow = flow.model_copy(update={"flow_key": key})
                flows.append(flow)
    return flows or (_inventory_flows(task, inventory) if fallback else [])


def _recover_flow(
    task: AnalysisTask,
    item: Any,
    index: int,
) -> CodeFlow | None:
    if not isinstance(item, Mapping):
        return None
    flow_evidence = _evidence_list(task, item.get("evidence"), "Agent 流程证据")
    steps = []
    used_step_keys: set[str] = set()
    original_to_normalized: dict[str, str] = {}
    raw_steps = item.get("steps")
    if isinstance(raw_steps, list):
        for step_index, raw_step in enumerate(raw_steps, 1):
            if not isinstance(raw_step, Mapping):
                continue
            evidence = _evidence_list(
                task,
                raw_step.get("evidence"),
                str(raw_step.get("label") or raw_step.get("description") or "Agent 步骤"),
            ) or flow_evidence
            if not evidence:
                continue
            raw_kind = str(raw_step.get("kind") or "main").lower()
            kind = (
                raw_kind
                if raw_kind in CANONICAL_STEP_KINDS
                else "branch" if raw_kind in BRANCH_LIKE_KINDS else "main"
            )
            original_key = str(raw_step.get("step_key") or f"S{step_index:03d}")
            step_key = _unique_key(original_key, used_step_keys)
            used_step_keys.add(step_key)
            original_to_normalized.setdefault(original_key, step_key)
            steps.append({
                "step_key": step_key,
                "label": str(
                    raw_step.get("label")
                    or raw_step.get("description")
                    or raw_step.get("action")
                    or f"步骤 {step_index}"
                ),
                "kind": kind,
                "evidence": evidence,
            })
    if not steps:
        return None
    if len(steps) == 1:
        steps.append({
            "step_key": f"{steps[0]['step_key']}-EXIT",
            "label": "完成当前路径观测",
            "kind": "exit",
            "evidence": steps[0]["evidence"],
        })
    step_keys = {step["step_key"] for step in steps}
    edges = []
    raw_edges = item.get("edges")
    if isinstance(raw_edges, list):
        for edge in raw_edges:
            if not isinstance(edge, Mapping):
                continue
            source = original_to_normalized.get(str(edge.get("source_step_key")))
            target = original_to_normalized.get(str(edge.get("target_step_key")))
            if source in step_keys and target in step_keys:
                edges.append({
                    "source_step_key": source,
                    "target_step_key": target,
                    "condition": edge.get("condition"),
                })
    if not edges:
        edges = [
            {"source_step_key": left["step_key"], "target_step_key": right["step_key"]}
            for left, right in zip(steps, steps[1:])
        ]
    return CodeFlow.model_validate({
        "flow_key": str(item.get("flow_key") or f"FLOW-{index:03d}"),
        "title": str(item.get("title") or item.get("description") or f"流程 {index}"),
        "entry": str(item.get("entry") or steps[0]["label"]),
        "summary": str(item.get("summary") or item.get("description") or "Agent 流程降级保留"),
        "steps": steps,
        "edges": edges,
    })


def _inventory_flows(
    task: AnalysisTask,
    inventory: Mapping[str, Any],
) -> list[CodeFlow]:
    branch_flows = _inventory_branch_flows(task, inventory)
    if branch_flows:
        return branch_flows

    files = _inventory_files(task, inventory)
    first_file = files[0] if files else {
        "path": task.unit.source_scope[0], "functions": []
    }
    first_function = next(iter(first_file.get("functions", [])), {})
    symbol = str(first_function.get("symbol") or first_file["path"])
    line = _positive_line(first_function.get("line"))
    evidence = SourceEvidence(
        repo_id=task.unit.repo_id,
        path=str(first_file["path"]),
        line_start=line,
        observation="结构化索引确认该入口位于当前冻结源码范围",
    )
    return [CodeFlow.model_validate({
        "flow_key": "FALLBACK-FLOW-001",
        "title": f"{task.unit.title} 基础覆盖路径",
        "entry": symbol,
        "summary": "结构化降级路径；具体业务分支与异常语义待复核",
        "steps": [
            {"step_key": "FB-S1", "label": f"进入 {symbol}", "kind": "entry", "evidence": [evidence]},
            {"step_key": "FB-S2", "label": "记录基础路径结果", "kind": "exit", "evidence": [evidence]},
        ],
        "edges": [{"source_step_key": "FB-S1", "target_step_key": "FB-S2"}],
    })]


def _inventory_files(
    task: AnalysisTask,
    inventory: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    allowed = set(task.unit.source_scope) | set(task.unit.context_scope)
    return [
        item for item in inventory.get("files", [])
        if isinstance(item, Mapping)
        and item.get("repo_id") == task.unit.repo_id
        and item.get("path") in allowed
    ]


def _inventory_branch_flows(
    task: AnalysisTask,
    inventory: Mapping[str, Any],
) -> list[CodeFlow]:
    files = _inventory_files(task, inventory)
    branch_flows: list[CodeFlow] = []
    for file_item in files:
        functions = [
            item for item in file_item.get("functions", []) if isinstance(item, Mapping)
        ]
        for branch_index, branch in enumerate(file_item.get("branches", []), 1):
            if len(branch_flows) >= MAX_FALLBACK_BRANCHES or not isinstance(branch, Mapping):
                break
            line = _positive_line(branch.get("line"))
            function = next((
                item for item in functions
                if _positive_line(item.get("line")) <= line
                <= _positive_line(item.get("end_line") or item.get("line"))
            ), None)
            symbol = str(function.get("symbol")) if function else str(file_item["path"])
            evidence = SourceEvidence(
                repo_id=task.unit.repo_id,
                path=str(file_item["path"]),
                line_start=line,
                line_end=_positive_line(branch.get("end_line") or line),
                observation=f"结构化索引识别到 {branch.get('kind', 'branch')} 分支点",
            )
            key = f"FALLBACK-BRANCH-{len(branch_flows) + 1:03d}"
            branch_flows.append(CodeFlow.model_validate({
                "flow_key": key,
                "title": f"{symbol} 分支覆盖路径",
                "entry": symbol,
                "summary": "结构化降级路径，仅确认分支位置；具体业务语义待复核",
                "steps": [
                    {
                        "step_key": f"{key}-ENTRY",
                        "label": f"通过受支持入口到达 {symbol}",
                        "kind": "entry",
                        "evidence": [evidence],
                    },
                    {
                        "step_key": f"{key}-BRANCH",
                        "label": f"执行 {branch.get('kind', 'branch')} 分支两侧",
                        "kind": "branch",
                        "evidence": [evidence],
                    },
                    {
                        "step_key": f"{key}-EXIT",
                        "label": "记录分支执行结果",
                        "kind": "exit",
                        "evidence": [evidence],
                    },
                ],
                "edges": [
                    {"source_step_key": f"{key}-ENTRY", "target_step_key": f"{key}-BRANCH"},
                    {"source_step_key": f"{key}-BRANCH", "target_step_key": f"{key}-EXIT"},
                ],
            }))
    return branch_flows


def _recover_cases(
    value: Any,
    flows: list[CodeFlow],
    risks: list[RiskFinding],
    selected_inputs: Mapping[str, Any],
    warnings: list[str],
) -> list[GeneratedTestCase]:
    if not isinstance(value, list):
        return []
    allowed = set(GeneratedTestCase.model_fields)
    known_flows = {flow.flow_key for flow in flows}
    known_risks = {risk.risk_key for risk in risks}
    known_inputs = {
        *selected_inputs.get("asset_items", {}),
        *selected_inputs.get("defect_mechanisms", {}),
        *[
            str(item["coverage_id"])
            for item in selected_inputs.get("coverage_gaps", [])
            if isinstance(item, Mapping) and item.get("coverage_id")
        ],
    }
    input_types = {
        item_id: item.get("item_type")
        for item_id, item in selected_inputs.get("asset_items", {}).items()
        if isinstance(item, Mapping)
    }
    input_types.update({
        item_id: "historical_defect"
        for item_id in selected_inputs.get("defect_mechanisms", {})
    })
    input_types.update({
        str(item["coverage_id"]): "coverage"
        for item in selected_inputs.get("coverage_gaps", [])
        if isinstance(item, Mapping) and item.get("coverage_id")
    })
    recovered = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            continue
        payload = {key: item[key] for key in allowed if key in item}
        payload["case_key"] = f"CASE-{index:03d}"
        payload.setdefault("title", str(item.get("description") or f"Agent 用例 {index}"))
        raw_flow_keys = _string_list(payload.get("covered_flow_keys"))
        payload["covered_flow_keys"] = [
            key for key in raw_flow_keys if key in known_flows
        ] or [flows[0].flow_key]
        raw_risk_keys = _string_list(payload.get("linked_risk_keys"))
        payload["linked_risk_keys"] = [
            key for key in raw_risk_keys if key in known_risks
        ]
        raw_input_ids = _string_list(payload.get("linked_input_ids"))
        payload["linked_input_ids"] = [
            key for key in raw_input_ids if key in known_inputs
        ]
        if set(raw_flow_keys) - known_flows:
            warnings.append(f"Workflow 清理了用例 {index} 引用的未知 flow_key")
        if set(raw_risk_keys) - known_risks:
            warnings.append(f"Workflow 清理了用例 {index} 引用的未知 risk_key")
        if set(raw_input_ids) - known_inputs:
            warnings.append(f"Workflow 清理了用例 {index} 引用的未知 input ID")
        supported_basis = {"code_flow"}
        if payload["linked_risk_keys"]:
            supported_basis.add("risk")
        for item_id in payload["linked_input_ids"]:
            supported_basis.add({
                "coverage": "coverage",
                "requirement": "requirement",
                "design": "design",
                "historical_defect": "defect_mechanism",
            }.get(input_types.get(item_id), "code_flow"))
        raw_basis = _string_list(payload.get("basis"))
        payload["basis"] = [
            name for name in raw_basis if name in supported_basis
        ] or ["code_flow"]
        payload.setdefault("level", "graybox")
        payload.setdefault("preconditions", ["已准备当前模块的受支持运行环境"])
        payload.setdefault("observability", ["记录业务结果、日志和 Coverage"])
        payload.setdefault("cleanup", ["恢复测试前环境"])
        if payload.get("steps") and isinstance(payload["steps"][0], str):
            expected = item.get("expected_results", [])
            payload["steps"] = [
                {
                    "action": action,
                    "expected_result": (
                        expected[position]
                        if isinstance(expected, list) and position < len(expected)
                        else "记录该步骤的可判定业务结果"
                    ),
                }
                for position, action in enumerate(payload["steps"])
            ]
        try:
            recovered.append(GeneratedTestCase.model_validate(payload))
        except ValueError:
            continue
    return recovered


def _apply_analysis_safety_net(
    task: AnalysisTask,
    result: UnitSemanticResult,
    inventory: Mapping[str, Any],
    selected_inputs: Mapping[str, Any],
    warnings: list[str],
) -> UnitSemanticResult:
    flows = list(result.flows)
    represented_branches = {
        (evidence.path, evidence.line_start, evidence.line_end or evidence.line_start)
        for flow in flows
        for step in flow.steps
        if step.kind == "branch"
        for evidence in step.evidence
    }
    used_flow_keys = {flow.flow_key for flow in flows}
    added_branches = 0
    for candidate in _inventory_branch_flows(task, inventory):
        evidence = next(
            evidence
            for step in candidate.steps
            if step.kind == "branch"
            for evidence in step.evidence
        )
        if any(
            path == evidence.path and start <= evidence.line_start <= end
            for path, start, end in represented_branches
        ):
            continue
        key = _unique_key(candidate.flow_key, used_flow_keys)
        used_flow_keys.add(key)
        if key != candidate.flow_key:
            candidate = candidate.model_copy(update={"flow_key": key})
        flows.append(candidate)
        represented_branches.add((
            evidence.path,
            evidence.line_start,
            evidence.line_end or evidence.line_start,
        ))
        added_branches += 1
    if added_branches:
        warnings.append(f"Workflow 从结构化索引补充了 {added_branches} 条未覆盖分支流程")

    cases = list(result.test_cases)
    used_case_keys = {case.case_key for case in cases}
    branch_cases = _branch_cases(flows, cases, used_case_keys)
    cases.extend(branch_cases)
    if branch_cases:
        warnings.append(f"Workflow 补充了 {len(branch_cases)} 条分支覆盖用例")

    linked_risks = {
        risk_key for case in cases for risk_key in case.linked_risk_keys
    }
    for risk in result.risks:
        if risk.test_disposition != "test_required" or risk.risk_key in linked_risks:
            continue
        case = _risk_case(risk, flows[0].flow_key, used_case_keys)
        used_case_keys.add(case.case_key)
        cases.append(case)
        linked_risks.add(risk.risk_key)
        warnings.append(f"Workflow 为未关联用例的风险 {risk.risk_key} 补充了测试用例")

    coverage_by_id = {
        str(item["coverage_id"]): item
        for item in selected_inputs.get("coverage_gaps", [])
        if isinstance(item, Mapping) and item.get("coverage_id")
    }
    existing_coverage = {
        item.coverage_id: item
        for item in result.coverage_decisions
        if item.coverage_id in coverage_by_id
    }
    coverage_decisions = []
    for index, (coverage_id, gap) in enumerate(coverage_by_id.items(), 1):
        decision = existing_coverage.get(coverage_id)
        linked_cases = [
            case for case in cases if coverage_id in case.linked_input_ids
        ]
        if decision and decision.disposition == "unreachable":
            coverage_decisions.append(decision.model_copy(update={"test_case_keys": []}))
            continue
        if not linked_cases:
            flow_key = _flow_key_for_gap(flows, gap)
            case = _coverage_case(gap, flow_key, index, used_case_keys)
            used_case_keys.add(case.case_key)
            cases.append(case)
            linked_cases = [case]
            warnings.append(f"Workflow 为 Coverage {coverage_id} 补充了兜底用例")
        coverage_decisions.append(CoverageDecision(
            coverage_id=coverage_id,
            disposition=(
                "covered_by_generated_case"
                if decision and decision.disposition == "covered_by_generated_case"
                else "test_generated"
            ),
            test_case_keys=[case.case_key for case in linked_cases],
            reason=(
                decision.reason
                if decision and decision.disposition != "unresolved"
                else "Workflow 已由测试用例完成该 Coverage gap 的结构化闭环"
            ),
        ))

    input_decisions = _complete_input_decisions(
        result.input_decisions,
        selected_inputs.get("asset_items", {}),
        warnings,
    )
    mechanism_decisions = _complete_mechanism_decisions(
        result.mechanism_decisions,
        selected_inputs.get("defect_mechanisms", {}),
        cases,
        warnings,
    )
    unresolved = list(result.unresolved)
    for item_id, conclusion in [
        *[
            (item.item_id, item.conclusion)
            for item in input_decisions
            if item.disposition == "unresolved"
        ],
        *[
            (item.mechanism_id, item.conclusion)
            for item in mechanism_decisions
            if item.disposition == "unresolved"
        ],
    ]:
        if not any(item_id in item for item in unresolved):
            unresolved.append(f"{item_id}: {conclusion}")
    return result.model_copy(update={
        "flows": flows,
        "input_decisions": input_decisions,
        "coverage_decisions": coverage_decisions,
        "mechanism_decisions": mechanism_decisions,
        "test_cases": cases,
        "unresolved": unresolved,
    }, deep=True)


def _complete_input_decisions(
    decisions: list[InputDecision],
    expected: Mapping[str, Any],
    warnings: list[str],
) -> list[InputDecision]:
    by_id = {item.item_id: item for item in decisions if item.item_id in expected}
    for item_id in expected:
        if item_id in by_id:
            continue
        by_id[item_id] = InputDecision(
            item_id=item_id,
            disposition="unresolved",
            conclusion="Analysis 未提供该输入的裁决，Workflow 保留为待复核",
        )
        warnings.append(f"Workflow 为未裁决输入 {item_id} 补充了 unresolved 决策")
    return list(by_id.values())


def _complete_mechanism_decisions(
    decisions: list[MechanismDecision],
    expected: Mapping[str, Any],
    cases: list[GeneratedTestCase],
    warnings: list[str],
) -> list[MechanismDecision]:
    by_id = {
        item.mechanism_id: item
        for item in decisions
        if item.mechanism_id in expected
    }
    for mechanism_id in expected:
        if mechanism_id not in by_id:
            by_id[mechanism_id] = MechanismDecision(
                mechanism_id=mechanism_id,
                disposition="unresolved",
                conclusion="Analysis 未提供该缺陷机理的裁决，Workflow 保留为待复核",
            )
            warnings.append(
                f"Workflow 为未裁决缺陷机理 {mechanism_id} 补充了 unresolved 决策"
            )
        linked = [
            case.case_key for case in cases
            if mechanism_id in case.linked_input_ids
        ]
        by_id[mechanism_id] = by_id[mechanism_id].model_copy(
            update={"test_case_keys": linked}
        )
    return list(by_id.values())


def _flow_key_for_gap(flows: list[CodeFlow], gap: Mapping[str, Any]) -> str:
    matches = gap.get("matches", [])
    for match in matches if isinstance(matches, list) else []:
        if not isinstance(match, Mapping) or not match.get("path"):
            continue
        line = _positive_line(match.get("line"))
        for flow in flows:
            if any(
                evidence.path == match["path"]
                and evidence.line_start <= line <= (evidence.line_end or evidence.line_start)
                for step in flow.steps
                for evidence in step.evidence
            ):
                return flow.flow_key
    return flows[0].flow_key


def _branch_cases(
    flows: list[CodeFlow],
    existing_cases: list[GeneratedTestCase],
    used_keys: set[str],
) -> list[GeneratedTestCase]:
    cases = []
    for flow in flows:
        if not any(step.kind == "branch" for step in flow.steps):
            continue
        if any(
            flow.flow_key in case.covered_flow_keys and _case_covers_branch(case)
            for case in existing_cases
        ):
            continue
        key = _unique_key(f"BRANCH-{flow.flow_key}", used_keys)
        used_keys.add(key)
        cases.append(GeneratedTestCase(
            case_key=key,
            title=f"覆盖 {flow.title} 的分支两侧",
            basis=["code_flow"],
            covered_flow_keys=[flow.flow_key],
            level="graybox",
            preconditions=["已准备能够从受支持入口到达该分支的测试环境"],
            steps=[{
                "action": "分别构造使目标分支成立和不成立的输入或状态",
                "expected_result": "Coverage 记录显示分支两侧均执行，并分别记录外部结果",
            }],
            observability=["检查分支 Coverage、接口结果和运行日志"],
            cleanup=["恢复测试前输入和运行状态"],
        ))
    return cases


def _case_covers_branch(case: GeneratedTestCase) -> bool:
    text = " ".join([
        case.title,
        *[step.action for step in case.steps],
        *[step.expected_result for step in case.steps],
    ]).lower()
    return "分支" in text or "branch" in text


def _coverage_case(
    gap: Mapping[str, Any],
    flow_key: str,
    index: int,
    used_keys: set[str],
) -> GeneratedTestCase:
    key = _unique_key(f"COVERAGE-{index:03d}", used_keys)
    subject = gap.get("function") or gap.get("branch_id") or gap["coverage_id"]
    return GeneratedTestCase(
        case_key=key,
        title=f"补齐 {subject} 的 Coverage 缺口",
        basis=["code_flow", "coverage"],
        covered_flow_keys=[flow_key],
        linked_input_ids=[str(gap["coverage_id"])],
        level="graybox",
        preconditions=["已准备能够从受支持入口到达目标函数或分支的环境"],
        steps=[{
            "action": f"通过受支持入口触发 {subject}",
            "expected_result": "Coverage 记录显示目标项已执行，并记录对应业务结果",
        }],
        observability=["检查函数/分支 Coverage、接口结果和运行日志"],
        cleanup=["恢复测试前环境"],
    )


def _risk_case(
    risk: RiskFinding,
    flow_key: str,
    used_keys: set[str],
) -> GeneratedTestCase:
    key = _unique_key(f"RISK-{risk.risk_key}", used_keys)
    return GeneratedTestCase(
        case_key=key,
        title=f"验证 {risk.title}",
        basis=["code_flow", "risk"],
        covered_flow_keys=[flow_key],
        linked_risk_keys=[risk.risk_key],
        level="graybox",
        preconditions=[risk.trigger],
        steps=[{
            "action": "通过受支持入口构造风险触发条件",
            "expected_result": f"系统不得出现：{risk.system_result}",
        }],
        observability=[risk.external_observation],
        cleanup=["恢复测试前环境"],
    )


def _legacy_risk_cases(
    value: Any,
    recovered_risk_keys: set[str],
    flow_key: str,
    used_keys: set[str],
) -> list[GeneratedTestCase]:
    if not isinstance(value, list):
        return []
    cases = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            continue
        risk_key = str(item.get("risk_key") or "")
        if risk_key and risk_key in recovered_risk_keys:
            continue
        title = item.get("title") or item.get("description")
        if not title:
            continue
        trigger = item.get("trigger") or item.get("reproduction_conditions")
        if isinstance(trigger, list):
            trigger = "；".join(str(part) for part in trigger)
        trigger = str(trigger or "按 Agent 描述准备对应触发条件")
        system_result = str(item.get("system_result") or item.get("description") or title)
        observation = str(item.get("external_observation") or item.get("description") or title)
        key = _unique_key(f"DEGRADED-RISK-{index:03d}", used_keys)
        used_keys.add(key)
        cases.append(GeneratedTestCase(
            case_key=key,
            title=f"验证 {title}",
            basis=["code_flow"],
            covered_flow_keys=[flow_key],
            level="graybox",
            preconditions=[trigger],
            steps=[{
                "action": "通过受支持入口构造 Agent 已识别的风险条件",
                "expected_result": f"系统不出现该分析描述的异常结果：{system_result}",
            }],
            observability=[observation],
            cleanup=["恢复测试前环境"],
        ))
    return cases


def _evidence_list(
    task: AnalysisTask,
    value: Any,
    fallback_observation: str,
    *,
    force_task_repo: bool = True,
) -> list[SourceEvidence]:
    if not isinstance(value, list):
        return []
    recovered = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        payload = {
            "repo_id": (
                task.unit.repo_id
                if force_task_repo
                else item.get("repo_id") or task.unit.repo_id
            ),
            "path": item.get("path") or item.get("file"),
            "line_start": item.get("line_start") or item.get("line"),
            "line_end": item.get("line_end"),
            "observation": item.get("observation") or item.get("description") or fallback_observation,
        }
        try:
            recovered.append(SourceEvidence.model_validate(payload))
        except ValueError:
            continue
    return recovered


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _positive_line(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _unique_key(base: str, used_keys: set[str]) -> str:
    if base not in used_keys:
        return base
    suffix = 2
    while f"{base}-{suffix}" in used_keys:
        suffix += 1
    return f"{base}-{suffix}"
