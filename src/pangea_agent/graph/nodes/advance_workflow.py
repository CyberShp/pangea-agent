from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.planning import accept_planning_result, planning_result_model
from pangea_agent.graph.result_contract import (
    correction_target_identity_errors,
    risk_test_obligations,
    snapshot_correction_target,
    validate_closure_corrections,
    validate_unit_result,
)
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.workflow_store import (
    add_action,
    analysis_result_path,
    analysis_task_path,
    closure_result_path,
    closure_task_path,
    comparison_review_result_path,
    comparison_review_task_path,
    current_stage_actions,
    initialize_result,
    load_progress,
    pending_actions,
    planning_task_path,
    project_path,
    review_result_path,
    review_task_path,
    run_directory,
    save_progress,
    validated_result_path,
)
from pangea_agent.methodology import (
    SPECIALIZED_METHODOLOGIES,
    frozen_methodology_paths,
)
from pangea_agent.models.analysis import (
    ActionState,
    AnalysisTask,
    ClosureTask,
    ClosureCorrectionTarget,
    ComparisonAuditTarget,
    ComparisonReviewResult,
    ComparisonReviewTask,
    EvidenceScopeContract,
    IndependentReviewResult,
    IndependentReviewTask,
    PlanningResult,
    PlanningTask,
    RepositoryRef,
    UnitSemanticResult,
)


COMMON_RUBRIC_NAMES = (
    "dfx.md",
    "risk_reproducibility.md",
    "test_case_generation.md",
)


_UNIT_AUDIT_CHECKS = (
    "summary_consistency",
    "flow_completeness",
    "input_decision_completeness",
    "branch_completeness",
    "coverage_completeness",
    "mechanism_completeness",
    "risk_completeness",
    "scenario_completeness",
    "test_case_completeness",
)


def _analysis_audit_targets(
    unit_id: str,
    result: UnitSemanticResult,
) -> list[ComparisonAuditTarget]:
    """Enumerate review coordinates without judging their semantic verdict."""
    coordinates: list[tuple[str, str, str]] = []

    def add(object_type: str, object_key: str, *checks: str) -> None:
        coordinates.extend((object_type, object_key, check) for check in checks)

    add("unit", unit_id, *_UNIT_AUDIT_CHECKS)
    for flow in result.flows:
        add("flow", flow.flow_key, "control_flow")
        for step in flow.steps:
            add(
                "flow_step",
                f"{flow.flow_key}/{step.step_key}",
                "source_evidence",
            )
    for item in result.input_decisions:
        add("input_decision", item.item_id, "disposition_and_evidence")
    for item in result.branch_decisions:
        add(
            "branch_decision",
            item.branch_id,
            "flow_and_disposition",
            "scenario_links",
        )
    for item in result.coverage_decisions:
        add(
            "coverage_decision",
            item.coverage_id,
            "disposition_and_scenario_links",
            "direct_case_claims",
        )
    for item in result.mechanism_decisions:
        add(
            "mechanism_decision",
            item.mechanism_id,
            "causal_chain_and_disposition",
            "case_links",
            "source_evidence",
        )
    for item in result.risks:
        add(
            "risk",
            item.risk_key,
            "trigger",
            "system_result_and_observation",
            "exclusion_condition",
            "severity_and_product_impact",
            "flow_outcome_consistency",
            "test_disposition_and_links",
            "source_evidence",
        )
    for item in result.scenarios:
        checks = [
            "entry_and_readiness",
            "trigger_actions",
            "external_oracles",
            "trace_links",
            "source_evidence",
        ]
        if item.readiness == "developer_confirm":
            checks.append("developer_confirm_content")
        add("scenario", item.scenario_key, *checks)
    for item in result.test_cases:
        add(
            "test_case",
            item.case_key,
            "entry_actions_oracles",
            "coverage_claims",
            "risk_links",
        )
    for index, _ in enumerate(result.unresolved):
        add("unresolved", str(index), "scope_and_nonduplication")

    return [
        ComparisonAuditTarget(
            audit_id=f"AUD-{unit_id}-{index:04d}",
            unit_id=unit_id,
            object_type=object_type,
            object_key=object_key,
            check=check,
        )
        for index, (object_type, object_key, check) in enumerate(
            coordinates,
            start=1,
        )
    ]


def _general_rubrics(analysis_language: str) -> list[str]:
    return [
        str(project_path("src", "pangea_agent", "rubrics", "builtin", name))
        for name in (f"{analysis_language}_analysis.md", *COMMON_RUBRIC_NAMES)
    ]

SPECIALIZED_RUBRICS = {
    Path(name).stem: str(
        project_path("src", "pangea_agent", "rubrics", "builtin", name)
    )
    for name in SPECIALIZED_METHODOLOGIES
}


def _normalized_scope_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _unit_scopes(progress) -> dict[str, tuple[str, set[str]]]:
    return {
        unit.unit_id: (
            unit.repo_id,
            {
                _normalized_scope_path(path)
                for path in [*unit.source_scope, *unit.context_scope]
            },
        )
        for unit in progress.analysis_units
    }


def _evidence_scope(unit) -> EvidenceScopeContract:
    return EvidenceScopeContract(
        repo_id=unit.repo_id,
        allowed_paths=list(dict.fromkeys([
            *unit.source_scope,
            *unit.context_scope,
        ])),
    )


def _evidence_scope_by_unit(progress) -> dict[str, EvidenceScopeContract]:
    return {
        unit.unit_id: _evidence_scope(unit)
        for unit in progress.analysis_units
    }


def _canonical_evidence_path(path: str, candidates: set[str]) -> str | None:
    normalized = _normalized_scope_path(path)
    if normalized in candidates:
        return normalized

    suffix_matches = {
        candidate
        for candidate in candidates
        if candidate.endswith(f"/{normalized}") or normalized.endswith(f"/{candidate}")
    }
    if len(suffix_matches) == 1:
        return next(iter(suffix_matches))

    basename = normalized.rsplit("/", 1)[-1]
    basename_matches = {
        candidate for candidate in candidates
        if candidate.rsplit("/", 1)[-1] == basename
    }
    if len(basename_matches) == 1:
        return next(iter(basename_matches))
    return None


def _validate_evidence_for_units(
    progress,
    evidence_items,
    unit_ids: list[str],
    label: str,
) -> list[str]:
    scopes = _unit_scopes(progress)
    allowed_by_repo: dict[str, set[str]] = defaultdict(set)
    warnings: list[str] = []
    for unit_id in unit_ids:
        if unit_id not in scopes:
            warnings.append(f"{label}引用了未知单元：{unit_id}")
            continue
        repo_id, paths = scopes[unit_id]
        allowed_by_repo[repo_id].update(paths)

    for evidence in evidence_items:
        candidates = allowed_by_repo.get(evidence.repo_id, set())
        canonical = _canonical_evidence_path(evidence.path, candidates)
        if canonical is None:
            warnings.append(
                f"{label}证据待确认，不属于指定冻结单元源码："
                f"{evidence.repo_id}:{evidence.path}:{evidence.line_start}；"
                f"scope_unit_ids={sorted(unit_ids)}；"
                f"allowed_paths={sorted(candidates)}"
            )
        elif _normalized_scope_path(evidence.path) != canonical:
            warnings.append(
                f"{label}证据路径待确认，保留 Agent 原值："
                f"actual={evidence.path} possible_match={canonical}"
            )
        if evidence.line_end is not None and evidence.line_end < evidence.line_start:
            warnings.append(
                f"{label}证据行号范围无效："
                f"{evidence.repo_id}:{evidence.path}:"
                f"{evidence.line_start}-{evidence.line_end}"
            )
    return warnings


def assert_review_scope(progress, result: IndependentReviewResult) -> None:
    """Reject review evidence outside the globally frozen analysis scope."""
    errors: list[str] = []
    known_units = {unit.unit_id for unit in progress.analysis_units}
    for finding in result.findings:
        unknown = set(finding.affected_unit_ids) - known_units
        if unknown:
            errors.append(f"复核引用了未知单元：{sorted(unknown)}")
        errors.extend(_validate_evidence_for_units(
            progress,
            finding.evidence,
            sorted(known_units),
            f"复核 finding {finding.finding_key}",
        ))
    if errors:
        raise ValueError("复核证据范围不完整：" + " | ".join(errors[:24]))


def assert_comparison_review_scope(
    progress,
    independent: IndependentReviewResult,
    comparison: ComparisonReviewResult,
) -> None:
    """Keep findings and decisions inside the globally frozen source scope."""
    errors: list[str] = []
    try:
        assert_review_scope(progress, comparison)
    except ValueError as exc:
        errors.append(str(exc))
    independent_by_key = {
        finding.finding_key: finding for finding in independent.findings
    }
    for decision in comparison.independent_finding_decisions:
        finding = independent_by_key.get(decision.finding_key)
        if finding is None:
            continue
        errors.extend(_validate_evidence_for_units(
            progress,
            decision.evidence,
            sorted({unit.unit_id for unit in progress.analysis_units}),
            f"盲审裁决 {decision.finding_key}",
        ))
    if errors:
        raise ValueError("对照复核证据范围不完整：" + " | ".join(errors[:24]))


def _stage_ready(progress) -> bool:
    actions = current_stage_actions(progress)
    return bool(actions) and all(action.status == "settled" for action in actions)


def _waiting(state: PangeaState, progress) -> PangeaState:
    return {
        **state,
        "lifecycle_status": progress.lifecycle_status,
        "stage": progress.stage,
        "agent_actions": pending_actions(progress),
    }


def _fail_action(state: PangeaState, progress, action: ActionState, exc: Exception) -> None:
    action.status = "failed"
    action.error = str(exc)
    progress.lifecycle_status = "failed"
    progress.errors.append({
        "kind": "agent_result_rejected",
        "action_id": action.action_id,
        "reason": str(exc),
    })
    save_progress(state, progress)


def _read_validated_result(state, progress, action: ActionState, result_type):
    try:
        return result_type.model_validate(
            read_json(validated_result_path(state, action.action_id))
        )
    except (OSError, ValueError) as exc:
        if not action.task_id:
            raise ValueError(
                f"已结算 Action 缺少原 Agent 会话：{action.action_id}"
            ) from exc
        action.action = "continue_agent"
        action.status = "pending"
        action.error = (
            "Workflow 保存的已校验结果不可读取；请原 Agent 重新写入当前 task 的 "
            f"result_path 后再次提交：{exc}"
        )
        save_progress(state, progress)
        return None


def _prepare_analysis(state: PangeaState, progress) -> PangeaState:
    run_dir = run_directory(state)
    task = PlanningTask.model_validate(read_json(planning_task_path(state)))
    planning_action = next(
        action for action in progress.actions.values() if action.role == "planning"
    )
    result = _read_validated_result(
        state,
        progress,
        planning_action,
        planning_result_model(task),
    )
    if result is None:
        return _waiting(state, progress)
    compact = read_json(Path(task.compact_metadata_path))
    all_asset_items = read_json(run_dir / "inputs" / "asset-items.json")
    coverage_gaps = read_json(run_dir / "inputs" / "coverage-gaps.json")
    units = accept_planning_result(
        task,
        result,
        compact,
        all_asset_items,
        coverage_gaps,
    )
    unit_plan_summary = result.summary
    if len(units) != len(result.units):
        unit_plan_summary = (
            f"请求范围按直接调用链和工作量上限归并为 {len(units)} 个功能单元。"
        )
    write_json(run_dir / "inputs" / "unit-plan.json", {
        "summary": unit_plan_summary,
        "units": [unit.model_dump(mode="json") for unit in units],
        "unresolved": result.unresolved,
    })
    selected_asset_ids = {item for unit in units for item in unit.asset_item_ids}
    selected_mechanism_ids = {item for unit in units for item in unit.mechanism_ids}
    selected_coverage_ids = {item for unit in units for item in unit.coverage_ids}
    global_inputs = {
        "asset_items": {
            item_id: all_asset_items[item_id] for item_id in sorted(selected_asset_ids)
        },
        "defect_mechanisms": {
            item_id: all_asset_items[item_id] for item_id in sorted(selected_mechanism_ids)
        },
        "coverage_gaps": [
            item for item in coverage_gaps if item["coverage_id"] in selected_coverage_ids
        ],
        "test_case_examples": read_json(
            run_dir / "inputs" / "test-case-examples.json"
        ),
    }
    global_inputs_path = run_dir / "inputs" / "selected-inputs.json"
    write_json(global_inputs_path, global_inputs)
    source_manifest = read_json(run_dir / "inputs" / "source-manifest.json")
    analysis_language = source_manifest.get("analysis_language", "c_cpp")
    general_rubrics = _general_rubrics(analysis_language)
    repositories = {
        item["repo_id"]: RepositoryRef.model_validate(item)
        for item in source_manifest["repositories"]
    }
    progress.analysis_units = units
    planning_action.status = "accepted"
    progress.stage = "analyzing"
    user_rubric_paths = {
        Path(path).stem: path for path in frozen_methodology_paths(run_dir)
    }
    selectable_rubric_paths = {**SPECIALIZED_RUBRICS, **user_rubric_paths}
    for unit in units:
        action_id = f"{state['run_id']}:analysis:{unit.unit_id}"
        unit_inputs = {
            "asset_items": {
                item_id: all_asset_items[item_id] for item_id in unit.asset_item_ids
            },
            "defect_mechanisms": {
                item_id: all_asset_items[item_id] for item_id in unit.mechanism_ids
            },
            "coverage_gaps": [
                item for item in coverage_gaps if item["coverage_id"] in unit.coverage_ids
            ],
            "test_case_examples": global_inputs["test_case_examples"],
        }
        selected_path = run_dir / "inputs" / "units" / f"{unit.unit_id}.json"
        write_json(selected_path, unit_inputs)
        task_path = analysis_task_path(state, unit.unit_id)
        analysis_task = AnalysisTask(
            action_id=action_id,
            run_id=state["run_id"],
            target=state["task_contract"]["target"],
            analysis_language=analysis_language,
            unit=unit,
            evidence_scope=_evidence_scope(unit),
            repository=repositories[unit.repo_id],
            inventory_path=str(run_dir / "inputs" / "inventory.json"),
            source_manifest_path=str(run_dir / "inputs" / "source-manifest.json"),
            selected_inputs_path=str(selected_path),
            coverage_context=unit_inputs["coverage_gaps"],
            result_schema_path=str(project_path("schemas", "analysis_result.schema.json")),
            result_skeleton_path=str(
                project_path("schemas", "analysis_result.skeleton.json")
            ),
            result_example_path=str(
                project_path("schemas", "analysis_result.example.json")
            ),
            result_path=str(analysis_result_path(state, unit.unit_id)),
            rubric_paths=[
                *general_rubrics,
                *[
                    selectable_rubric_paths[methodology_id]
                    for methodology_id in unit.methodology_ids
                ],
            ],
        )
        write_json(task_path, analysis_task.model_dump(mode="json"))
        initialize_result(
            Path(analysis_task.result_path),
            read_json(Path(analysis_task.result_skeleton_path)),
        )
        add_action(progress, ActionState(
            action_id=action_id,
            action="dispatch_agent",
            role="analysis",
            stage="unit_analysis",
            task_path=str(task_path),
        ))
    save_progress(state, progress)
    return _waiting(state, progress)


def _accept_analysis(state: PangeaState, progress) -> PangeaState:
    for action in current_stage_actions(progress):
        unit_id = action.action_id.rsplit(":", 1)[-1]
        task = AnalysisTask.model_validate(read_json(analysis_task_path(state, unit_id)))
        result = _read_validated_result(
            state,
            progress,
            action,
            UnitSemanticResult,
        )
        if result is None:
            return _waiting(state, progress)
        try:
            validate_unit_result(task, result, read_json(Path(task.selected_inputs_path)))
        except Exception as exc:
            _fail_action(state, progress, action, exc)
            raise
        action.status = "accepted"
        progress.completed_analysis_units.append(unit_id)

    run_dir = run_directory(state)
    source_manifest = read_json(run_dir / "inputs" / "source-manifest.json")
    analysis_language = source_manifest.get("analysis_language", "c_cpp")
    review_rubrics = _general_rubrics(analysis_language)
    action_id = f"{state['run_id']}:review"
    user_rubric_paths = {
        Path(path).stem: path for path in frozen_methodology_paths(run_dir)
    }
    selectable_rubric_paths = {**SPECIALIZED_RUBRICS, **user_rubric_paths}
    selected_methodology_ids = {
        methodology_id
        for unit in progress.analysis_units
        for methodology_id in unit.methodology_ids
    }
    task = IndependentReviewTask(
        action_id=action_id,
        run_id=state["run_id"],
        target=state["task_contract"]["target"],
        analysis_language=analysis_language,
        repositories=[
            RepositoryRef.model_validate(item) for item in source_manifest["repositories"]
        ],
        evidence_scope_by_unit=_evidence_scope_by_unit(progress),
        unit_plan_path=str(run_dir / "inputs" / "unit-plan.json"),
        inventory_path=str(run_dir / "inputs" / "inventory.json"),
        source_manifest_path=str(run_dir / "inputs" / "source-manifest.json"),
        selected_inputs_path=str(run_dir / "inputs" / "selected-inputs.json"),
        rubric_paths=[
            *review_rubrics,
            *[
                path
                for methodology_id, path in selectable_rubric_paths.items()
                if methodology_id in selected_methodology_ids
            ],
        ],
        result_schema_path=str(project_path("schemas", "independent_review_result.schema.json")),
        result_skeleton_path=str(
            project_path("schemas", "independent_review_result.skeleton.json")
        ),
        result_path=str(review_result_path(state)),
    )
    task_path = review_task_path(state)
    write_json(task_path, task.model_dump(mode="json"))
    initialize_result(
        Path(task.result_path),
        read_json(Path(task.result_skeleton_path)),
    )
    progress.stage = "reviewing"
    add_action(progress, ActionState(
        action_id=action_id,
        action="dispatch_agent",
        role="review",
        stage="independent_review",
        task_path=str(task_path),
    ))
    save_progress(state, progress)
    return _waiting(state, progress)


def _validate_review(progress, result, selected_inputs: dict) -> list[str]:
    warnings: list[str] = []
    known_units = {unit.unit_id for unit in progress.analysis_units}
    coverage_ids = {
        item["coverage_id"] for item in selected_inputs.get("coverage_gaps", [])
    }
    known_input_ids = (
        set(selected_inputs.get("asset_items", {}))
        | set(selected_inputs.get("defect_mechanisms", {}))
        | coverage_ids
    )
    coverage_ids_by_unit = {
        unit.unit_id: set(unit.coverage_ids) for unit in progress.analysis_units
    }
    finding_keys = [finding.finding_key for finding in result.findings]
    if len(finding_keys) != len(set(finding_keys)):
        if isinstance(result, IndependentReviewResult):
            raise ValueError("Independent Review finding_key 包含重复编号")
        warnings.append("复核 finding_key 包含重复编号")
    for finding in result.findings:
        if len(finding.affected_unit_ids) != len(set(finding.affected_unit_ids)):
            raise ValueError(
                f"Review finding {finding.finding_key} 的 affected_unit_ids 包含重复单元"
            )
        if (
            isinstance(result, IndependentReviewResult)
            and finding.correction_targets
        ):
            raise ValueError(
                f"Independent finding {finding.finding_key} 看不到 Analysis，"
                "不得预填 correction_targets"
            )
        unknown = set(finding.affected_unit_ids) - known_units
        if unknown:
            warnings.append(f"复核引用了未知单元：{sorted(unknown)}")
        if finding.category == "coverage_gap":
            unknown_inputs = set(finding.linked_input_ids) - known_input_ids
            if unknown_inputs:
                raise ValueError(
                    f"Coverage finding {finding.finding_key} 引用了未知输入："
                    f"{sorted(unknown_inputs)}"
                )
            linked_coverage_ids = set(finding.linked_input_ids) & coverage_ids
            if not linked_coverage_ids:
                raise ValueError(
                    f"Coverage finding {finding.finding_key} 必须引用 "
                    "selected_inputs.coverage_gaps 中的真实 coverage_id；"
                    "coverage_diagnostics 计数不能代替 Coverage ID"
                )
            owned_coverage_ids = set().union(*(
                coverage_ids_by_unit.get(unit_id, set())
                for unit_id in finding.affected_unit_ids
            ))
            foreign_coverage_ids = linked_coverage_ids - owned_coverage_ids
            if foreign_coverage_ids:
                raise ValueError(
                    f"Coverage finding {finding.finding_key} 的 coverage_id "
                    "不属于 affected_unit_ids："
                    f"{sorted(foreign_coverage_ids)}"
                )
        warnings.extend(_validate_evidence_for_units(
            progress,
            finding.evidence,
            sorted(known_units),
            f"复核 finding {finding.finding_key}",
        ))
    return warnings


def _validate_comparison_review(
    progress,
    independent: IndependentReviewResult,
    comparison: ComparisonReviewResult,
    selected_inputs: dict,
    comparison_task: ComparisonReviewTask | None = None,
    analysis_results: dict[str, UnitSemanticResult] | None = None,
) -> list[str]:
    warnings = _validate_review(progress, comparison, selected_inputs)
    independent_keys = {finding.finding_key for finding in independent.findings}
    independent_by_key = {
        finding.finding_key: finding for finding in independent.findings
    }
    decision_keys = [
        decision.finding_key
        for decision in comparison.independent_finding_decisions
    ]
    if len(decision_keys) != len(set(decision_keys)):
        warnings.append("盲审 finding 的复核决定包含重复编号")
    missing = independent_keys - set(decision_keys)
    extra = set(decision_keys) - independent_keys
    if missing or extra:
        warnings.append(
            "对照复核没有逐条裁决盲审 finding："
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )
    for decision in comparison.independent_finding_decisions:
        finding = independent_by_key.get(decision.finding_key)
        if finding is None:
            continue
        warnings.extend(_validate_evidence_for_units(
            progress,
            decision.evidence,
            sorted({unit.unit_id for unit in progress.analysis_units}),
            f"盲审裁决 {decision.finding_key}",
        ))
    comparison_keys = {finding.finding_key for finding in comparison.findings}
    duplicates = independent_keys & comparison_keys
    if duplicates:
        warnings.append(f"对照复核 finding_key 与盲审重复：{sorted(duplicates)}")

    asset_items = selected_inputs.get("asset_items", {})
    mechanisms = selected_inputs.get("defect_mechanisms", {})
    coverage_ids = {
        item["coverage_id"] for item in selected_inputs.get("coverage_gaps", [])
    }
    known_input_ids = set(asset_items) | set(mechanisms) | coverage_ids

    def check_input_references(finding) -> None:
        unknown = set(finding.linked_input_ids) - known_input_ids
        if unknown:
            warnings.append(
                f"复核 finding {finding.finding_key} 引用了未知输入：{sorted(unknown)}"
            )

    for decision in comparison.independent_finding_decisions:
        finding = independent_by_key.get(decision.finding_key)
        if decision.disposition != "dismissed" and finding is not None:
            check_input_references(finding)
    for finding in comparison.findings:
        check_input_references(finding)
    if (
        comparison_task is not None
        and comparison_task.review_contract_version == "2.0"
    ):
        _validate_v2_comparison_contract(
            independent,
            comparison,
            comparison_task,
            analysis_results,
        )
    return warnings


def _validate_v2_comparison_contract(
    independent: IndependentReviewResult,
    comparison: ComparisonReviewResult,
    task: ComparisonReviewTask,
    analysis_results: dict[str, UnitSemanticResult] | None = None,
) -> None:
    errors: list[str] = []
    independent_keys = [finding.finding_key for finding in independent.findings]
    if len(independent_keys) != len(set(independent_keys)):
        errors.append("Independent findings 包含重复 finding_key")
    independent_by_key = {
        finding.finding_key: finding for finding in independent.findings
    }
    if comparison.unresolved:
        errors.append("Comparison v2 顶层 unresolved 必须为空；待修问题必须进入 finding")
    decision_keys = [
        decision.finding_key
        for decision in comparison.independent_finding_decisions
    ]
    if len(decision_keys) != len(set(decision_keys)):
        errors.append("Independent finding 存在重复的 Comparison decision")
    missing_decisions = set(independent_by_key) - set(decision_keys)
    extra_decisions = set(decision_keys) - set(independent_by_key)
    if missing_decisions or extra_decisions:
        errors.append(
            "Independent finding decision 集合不完整："
            f"missing={sorted(missing_decisions)} extra={sorted(extra_decisions)}"
        )
    decisions_by_key = {
        decision.finding_key: decision
        for decision in comparison.independent_finding_decisions
    }
    new_findings = {
        finding.finding_key: finding for finding in comparison.findings
    }
    if len(new_findings) != len(comparison.findings):
        errors.append("Comparison findings 包含重复 finding_key")
    retained_findings = {
        key: independent_by_key[key]
        for key, decision in decisions_by_key.items()
        if key in independent_by_key and decision.disposition != "dismissed"
    }
    duplicate_closure_keys = set(retained_findings) & set(new_findings)
    if duplicate_closure_keys:
        errors.append(
            "Comparison finding_key 与 retained Independent finding 重复："
            f"{sorted(duplicate_closure_keys)}"
        )
    closure_findings = {**retained_findings, **new_findings}

    if analysis_results is None:
        analysis_results = {
            unit_id: UnitSemanticResult.model_validate(read_json(Path(result_path)))
            for unit_id, result_path in task.analysis_result_paths.items()
        }

    for finding in independent.findings:
        if finding.correction_targets:
            errors.append(
                f"Independent finding {finding.finding_key} 不得预填 correction_targets"
            )

    def validate_targets(label: str, affected_units: list[str], targets) -> None:
        target_ids = [target.correction_id for target in targets]
        if len(target_ids) != len(set(target_ids)):
            errors.append(f"{label} correction_id 重复")
        target_refs = [
            (
                target.target.unit_id,
                target.target.collection,
                target.target.object_key,
                target.target.field_path,
            )
            for target in targets
            if not (
                target.target.collection != "result"
                and target.target.object_key is None
                and target.target.field_path is None
            )
        ]
        if len(target_refs) != len(set(target_refs)):
            errors.append(f"{label} correction target 定位重复")
        whole_objects = {
            ref[:3] for ref in target_refs if ref[2] is not None and ref[3] is None
        }
        overlapping = sorted({
            ref[:3]
            for ref in target_refs
            if ref[3] is not None and ref[:3] in whole_objects
        })
        if overlapping:
            errors.append(
                f"{label} 同时定位整个对象和其字段：{overlapping}"
            )
        for target in targets:
            if target.target.unit_id not in affected_units:
                errors.append(
                    f"{label} correction target {target.correction_id} 的 unit_id "
                    f"不属于 affected_unit_ids：{target.target.unit_id}"
                )
                continue
            analysis_result = analysis_results.get(target.target.unit_id)
            if analysis_result is None:
                errors.append(
                    f"{label} correction target {target.correction_id} "
                    "引用了 task 中不存在的 Analysis unit"
                )
                continue
            target_errors = correction_target_identity_errors(
                analysis_result,
                target.target,
            )
            errors.extend(
                f"{label} correction target {target.correction_id} 无效：{message}"
                for message in target_errors
            )
        covered_units = {target.target.unit_id for target in targets}
        missing_units = set(affected_units) - covered_units
        if missing_units:
            errors.append(
                f"{label} 缺少 affected unit 的 correction target："
                f"{sorted(missing_units)}"
            )

    for decision in comparison.independent_finding_decisions:
        finding = independent_by_key.get(decision.finding_key)
        if finding is None:
            continue
        if decision.disposition == "dismissed":
            if decision.correction_targets:
                errors.append(
                    f"dismissed Independent finding {decision.finding_key} "
                    "不得携带 correction_targets"
                )
            continue
        validate_targets(
            f"Independent decision {decision.finding_key}",
            finding.affected_unit_ids,
            decision.correction_targets,
        )
    for finding in comparison.findings:
        validate_targets(
            f"Comparison finding {finding.finding_key}",
            finding.affected_unit_ids,
            finding.correction_targets,
        )

    expected_ids = [target.audit_id for target in task.required_analysis_audits]
    actual_ids = [decision.audit_id for decision in comparison.analysis_audit_decisions]
    if len(expected_ids) != len(set(expected_ids)):
        errors.append("Comparison task 的 required_analysis_audits 包含重复 audit_id")
    if not expected_ids:
        errors.append("Comparison v2 task 缺少 required_analysis_audits")
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("Comparison result 的 analysis_audit_decisions 包含重复 audit_id")
    missing_audits = set(expected_ids) - set(actual_ids)
    extra_audits = set(actual_ids) - set(expected_ids)
    if missing_audits or extra_audits:
        errors.append(
            "Comparison audit ledger 不完整："
            f"missing={sorted(missing_audits)} extra={sorted(extra_audits)}"
        )

    audit_by_id = {
        target.audit_id: target for target in task.required_analysis_audits
    }
    referenced_units: dict[str, set[str]] = defaultdict(set)
    for decision in comparison.analysis_audit_decisions:
        target = audit_by_id.get(decision.audit_id)
        if target is None:
            continue
        if task.require_audit_conclusions and not decision.conclusion.strip():
            errors.append(
                f"audit {decision.audit_id} 必须填写逐项核对 conclusion"
            )
        if decision.disposition == "accepted":
            if decision.finding_keys:
                errors.append(
                    f"accepted audit {decision.audit_id} 不得引用 finding_keys"
                )
            continue
        if not decision.finding_keys:
            errors.append(
                f"finding audit {decision.audit_id} 必须引用至少一个 finding_key"
            )
            continue
        for finding_key in decision.finding_keys:
            finding = closure_findings.get(finding_key)
            if finding is None:
                errors.append(
                    f"audit {decision.audit_id} 引用了未进入 Closure 的 finding："
                    f"{finding_key}"
                )
                continue
            if target.unit_id not in finding.affected_unit_ids:
                errors.append(
                    f"audit {decision.audit_id} 与 finding {finding_key} 的 unit 不一致"
                )
                continue
            referenced_units[finding_key].add(target.unit_id)

    for finding_key, finding in closure_findings.items():
        missing_units = (
            set(finding.affected_unit_ids)
            - referenced_units.get(finding_key, set())
        )
        if missing_units:
            errors.append(
                f"进入 Closure 的 finding {finding_key} 未被 fail audit 覆盖："
                f"{sorted(missing_units)}"
            )

    if errors:
        raise ValueError("Comparison v2 结构合同不完整：" + " | ".join(errors[:32]))


def _accept_independent_review(state: PangeaState, progress, action) -> PangeaState:
    task = IndependentReviewTask.model_validate(read_json(review_task_path(state)))
    result = _read_validated_result(
        state,
        progress,
        action,
        IndependentReviewResult,
    )
    if result is None:
        return _waiting(state, progress)
    _validate_review(
        progress,
        result,
        read_json(Path(task.selected_inputs_path)),
    )
    if not action.task_id:
        raise ValueError(
            "Comparison Review 缺少可续接的 Independent Reviewer task_id"
        )
    action.status = "accepted"

    action_id = f"{state['run_id']}:comparison-review"
    analysis_result_paths = {
        unit.unit_id: str(validated_result_path(
            state,
            f"{state['run_id']}:analysis:{unit.unit_id}",
        ))
        for unit in progress.analysis_units
    }
    required_analysis_audits = []
    for unit in progress.analysis_units:
        analysis_result = UnitSemanticResult.model_validate(
            read_json(Path(analysis_result_paths[unit.unit_id]))
        )
        required_analysis_audits.extend(
            _analysis_audit_targets(unit.unit_id, analysis_result)
        )
    comparison_task = ComparisonReviewTask(
        action_id=action_id,
        review_contract_version="2.0",
        run_id=state["run_id"],
        target=state["task_contract"]["target"],
        analysis_language=task.analysis_language,
        evidence_scope_by_unit=_evidence_scope_by_unit(progress),
        unit_plan_path=task.unit_plan_path,
        analysis_task_paths={
            unit.unit_id: str(analysis_task_path(state, unit.unit_id))
            for unit in progress.analysis_units
        },
        analysis_result_paths=analysis_result_paths,
        required_analysis_audits=required_analysis_audits,
        require_audit_conclusions=True,
        independent_review_result_path=str(
            validated_result_path(state, action.action_id)
        ),
        selected_inputs_path=task.selected_inputs_path,
        rubric_paths=task.rubric_paths,
        result_schema_path=str(project_path("schemas", "comparison_review_result.schema.json")),
        result_skeleton_path=str(
            project_path("schemas", "comparison_review_result.skeleton.json")
        ),
        result_path=str(comparison_review_result_path(state)),
    )
    task_path = comparison_review_task_path(state)
    write_json(task_path, comparison_task.model_dump(mode="json"))
    initialize_result(
        Path(comparison_task.result_path),
        read_json(Path(comparison_task.result_skeleton_path)),
    )
    add_action(progress, ActionState(
        action_id=action_id,
        action="continue_agent",
        role="review",
        stage="comparison_review",
        task_path=str(task_path),
        task_id=action.task_id,
    ))
    save_progress(state, progress)
    return _waiting(state, progress)


def _accept_comparison_review(state: PangeaState, progress, action) -> PangeaState:
    comparison_task = ComparisonReviewTask.model_validate(
        read_json(comparison_review_task_path(state))
    )
    independent_task = IndependentReviewTask.model_validate(read_json(review_task_path(state)))
    comparison = _read_validated_result(
        state,
        progress,
        action,
        ComparisonReviewResult,
    )
    if comparison is None:
        return _waiting(state, progress)
    try:
        independent = IndependentReviewResult.model_validate(
            read_json(Path(comparison_task.independent_review_result_path))
        )
        _validate_comparison_review(
            progress,
            independent,
            comparison,
            read_json(Path(comparison_task.selected_inputs_path)),
            comparison_task,
        )
    except Exception as exc:
        _fail_action(state, progress, action, exc)
        raise
    action.status = "accepted"
    decisions = {
        decision.finding_key: decision
        for decision in comparison.independent_finding_decisions
    }
    retained_independent_findings = [
        finding.model_copy(update={
            "correction_targets": decisions[finding.finding_key].correction_targets,
        })
        for finding in independent.findings
        if (
            finding.finding_key in decisions
            and decisions[finding.finding_key].disposition != "dismissed"
        )
    ]
    all_findings = [*retained_independent_findings, *comparison.findings]
    obligations_by_unit = {}
    for unit in progress.analysis_units:
        analysis_action = progress.actions[
            f"{state['run_id']}:analysis:{unit.unit_id}"
        ]
        result = UnitSemanticResult.model_validate(
            read_json(validated_result_path(state, analysis_action.action_id))
        )
        obligations_by_unit[unit.unit_id] = risk_test_obligations(result)
    if not all_findings and not any(obligations_by_unit.values()):
        progress.stage = "reporting"
        save_progress(state, progress)
        return {**state, "ready_to_finalize": True}

    findings_by_unit = defaultdict(list)
    for finding in all_findings:
        for unit_id in finding.affected_unit_ids:
            findings_by_unit[unit_id].append(finding)
    repositories = {
        item.repo_id: item
        for item in independent_task.repositories
    }
    progress.stage = "closing"
    closure_created = False
    for unit in progress.analysis_units:
        findings = findings_by_unit.get(unit.unit_id, [])
        risk_obligations = obligations_by_unit.get(unit.unit_id, [])
        if not findings and not risk_obligations:
            continue
        action_id = f"{state['run_id']}:closure:{unit.unit_id}"
        origin_action = progress.actions[
            f"{state['run_id']}:analysis:{unit.unit_id}"
        ]
        if origin_action.status != "accepted" or not origin_action.task_id:
            raise ValueError(
                "定向补齐缺少可恢复的首轮 analysis worker："
                f"{origin_action.action_id}"
            )
        original_task_path = analysis_task_path(state, unit.unit_id)
        original_task = AnalysisTask.model_validate(read_json(original_task_path))
        original_result = UnitSemanticResult.model_validate(
            read_json(validated_result_path(state, origin_action.action_id))
        )
        correction_targets = [
            ClosureCorrectionTarget(
                finding_key=finding.finding_key,
                correction_id=target.correction_id,
                target=target.target,
                required_state=target.required_state,
                before=snapshot_correction_target(
                    original_result,
                    target.target,
                ),
            )
            for finding in findings
            for target in finding.correction_targets
            if target.target.unit_id == unit.unit_id
        ]
        task_path = closure_task_path(state, unit.unit_id)
        closure_task = ClosureTask(
            action_id=action_id,
            review_contract_version="2.0",
            run_id=state["run_id"],
            target=state["task_contract"]["target"],
            analysis_language=original_task.analysis_language,
            unit=unit,
            evidence_scope=_evidence_scope(unit),
            repository=repositories[unit.repo_id],
            original_task_path=str(original_task_path),
            original_result_path=str(validated_result_path(
                state,
                origin_action.action_id,
            )),
            review_findings=findings,
            correction_targets=correction_targets,
            risk_test_obligations=risk_obligations,
            result_schema_path=str(project_path("schemas", "analysis_result.schema.json")),
            result_example_path=str(
                project_path("schemas", "analysis_result.example.json")
            ),
            result_path=str(closure_result_path(state, unit.unit_id)),
            rubric_paths=original_task.rubric_paths,
        )
        write_json(task_path, closure_task.model_dump(mode="json"))
        initialize_result(
            Path(closure_task.result_path),
            read_json(Path(closure_task.original_result_path)),
        )
        add_action(progress, ActionState(
            action_id=action_id,
            action="continue_agent",
            role="closure",
            stage="targeted_closure",
            task_path=str(task_path),
            task_id=origin_action.task_id,
        ))
        closure_created = True
    if not closure_created:
        progress.stage = "reporting"
        save_progress(state, progress)
        return {**state, "ready_to_finalize": True}
    save_progress(state, progress)
    return _waiting(state, progress)


def _accept_review(state: PangeaState, progress) -> PangeaState:
    action = next(action for action in current_stage_actions(progress) if action.role == "review")
    if action.stage == "independent_review":
        return _accept_independent_review(state, progress, action)
    if action.stage == "comparison_review":
        return _accept_comparison_review(state, progress, action)
    raise ValueError(f"未知 Review action stage：{action.stage}")


def _accept_closure(state: PangeaState, progress) -> PangeaState:
    for action in current_stage_actions(progress):
        unit_id = action.action_id.rsplit(":", 1)[-1]
        closure_task = ClosureTask.model_validate(read_json(closure_task_path(state, unit_id)))
        original_task = AnalysisTask.model_validate(read_json(Path(closure_task.original_task_path)))
        original_result = UnitSemanticResult.model_validate(
            read_json(Path(closure_task.original_result_path))
        )
        result = _read_validated_result(
            state,
            progress,
            action,
            UnitSemanticResult,
        )
        if result is None:
            return _waiting(state, progress)
        try:
            validate_unit_result(
                original_task,
                result,
                read_json(Path(original_task.selected_inputs_path)),
                closure_task.review_findings,
            )
            correction_errors = validate_closure_corrections(
                closure_task,
                original_result,
                result,
            )
            if correction_errors:
                raise ValueError(" | ".join(correction_errors[:24]))
            # 引用不完整由 adapter 记录为降级；Graph 保留 Agent 原始结果继续汇总。
        except Exception as exc:
            _fail_action(state, progress, action, exc)
            raise
        action.status = "accepted"
        progress.completed_closure_units.append(unit_id)
    progress.stage = "reporting"
    save_progress(state, progress)
    return {**state, "ready_to_finalize": True}


def advance_workflow(state: PangeaState) -> PangeaState:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("Run progress 不存在")
    if progress.lifecycle_status != "running":
        return _waiting(state, progress)
    if not _stage_ready(progress):
        return _waiting(state, progress)
    if progress.stage == "planning":
        return _prepare_analysis(state, progress)
    if progress.stage == "analyzing":
        return _accept_analysis(state, progress)
    if progress.stage == "reviewing":
        return _accept_review(state, progress)
    if progress.stage == "closing":
        return _accept_closure(state, progress)
    if progress.stage == "reporting":
        return {**state, "ready_to_finalize": True}
    return _waiting(state, progress)
