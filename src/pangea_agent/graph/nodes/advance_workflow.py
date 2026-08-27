from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.planning import accept_plan
from pangea_agent.graph.result_contract import validate_unit_result
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
    planning_result_path,
    planning_task_path,
    project_path,
    review_result_path,
    review_task_path,
    run_directory,
    save_progress,
)
from pangea_agent.models.analysis import (
    ActionState,
    AnalysisTask,
    ClosureTask,
    ComparisonReviewResult,
    ComparisonReviewTask,
    IndependentReviewResult,
    IndependentReviewTask,
    PlanningResult,
    PlanningTask,
    RepositoryRef,
    UnitSemanticResult,
)


GENERAL_RUBRICS = [
    str(project_path("src", "pangea_agent", "rubrics", "builtin", name))
    for name in (
        "c_cpp_analysis.md",
        "dfx.md",
        "risk_reproducibility.md",
        "test_case_generation.md",
    )
]


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
) -> None:
    scopes = _unit_scopes(progress)
    allowed_by_repo: dict[str, set[str]] = defaultdict(set)
    for unit_id in unit_ids:
        repo_id, paths = scopes[unit_id]
        allowed_by_repo[repo_id].update(paths)

    for evidence in evidence_items:
        candidates = allowed_by_repo.get(evidence.repo_id, set())
        canonical = _canonical_evidence_path(evidence.path, candidates)
        if canonical is None:
            raise ValueError(
                f"{label}证据不属于 affected_unit_ids 的冻结源码："
                f"{evidence.repo_id}:{evidence.path}:{evidence.line_start}；"
                f"affected_unit_ids={sorted(unit_ids)}；"
                f"allowed_paths={sorted(candidates)}"
            )
        evidence.path = canonical


def _specialized_rubrics(unit, compact: dict) -> list[str]:
    owned_paths = set(unit.source_scope) | set(unit.context_scope)
    files = [
        item for item in compact.get("files", [])
        if item.get("repo_id") == unit.repo_id and item.get("path") in owned_paths
    ]
    text = "\n".join([
        *(path.lower() for path in owned_paths),
        *(
            str(function.get("symbol", "")).lower()
            for item in files
            for function in item.get("functions", [])
        ),
        *(
            str(signal.get("text", "")).lower()
            for item in files
            for signal in item.get("resource_signals", [])
        ),
    ])
    selected = []
    if "iscsi" in text:
        selected.append(str(project_path("src", "pangea_agent", "rubrics", "builtin", "storage_iscsi.md")))
    if any(token in text for token in ("/nvmf/", "nvme_tcp", "nvme_rdma", "nvme_fabric")):
        selected.append(str(project_path("src", "pangea_agent", "rubrics", "builtin", "storage_nvmeof.md")))
    if any(token in text for token in ("alloc", "resource", "queue", "register", "ref")):
        selected.append(str(project_path("src", "pangea_agent", "rubrics", "builtin", "storage_resource_recovery.md")))
    if "dpdk" in text:
        selected.append(str(project_path("src", "pangea_agent", "rubrics", "builtin", "vendor_dpdk.md")))
    if any(token in text for token in ("mlx4", "mlx5", "rdma")):
        selected.append(str(project_path("src", "pangea_agent", "rubrics", "builtin", "vendor_mlx_rdma.md")))
    if "doca" in text:
        selected.append(str(project_path("src", "pangea_agent", "rubrics", "builtin", "vendor_nvidia_doca.md")))
    return selected


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


def _prepare_analysis(state: PangeaState, progress) -> PangeaState:
    run_dir = run_directory(state)
    task = PlanningTask.model_validate(read_json(planning_task_path(state)))
    result = PlanningResult.model_validate(read_json(planning_result_path(state)))
    compact = read_json(Path(task.compact_metadata_path))
    all_asset_items = read_json(run_dir / "inputs" / "asset-items.json")
    coverage_gaps = read_json(run_dir / "inputs" / "coverage-gaps.json")
    units = accept_plan(task, result, compact, all_asset_items, coverage_gaps)
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
    repositories = {
        item["repo_id"]: RepositoryRef.model_validate(item)
        for item in read_json(run_dir / "inputs" / "source-manifest.json")["repositories"]
    }
    progress.analysis_units = units
    planning_action = next(
        action for action in progress.actions.values() if action.role == "planning"
    )
    planning_action.status = "accepted"
    progress.stage = "analyzing"
    for unit in units:
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
            run_id=state["run_id"],
            target=state["task_contract"]["target"],
            unit=unit,
            repository=repositories[unit.repo_id],
            inventory_path=str(run_dir / "inputs" / "inventory.json"),
            source_manifest_path=str(run_dir / "inputs" / "source-manifest.json"),
            selected_inputs_path=str(selected_path),
            coverage_context=unit_inputs["coverage_gaps"],
            result_schema_path=str(project_path("schemas", "analysis_result.schema.json")),
            result_skeleton_path=str(
                project_path("schemas", "analysis_result.skeleton.json")
            ),
            result_path=str(analysis_result_path(state, unit.unit_id)),
            rubric_paths=[*GENERAL_RUBRICS, *_specialized_rubrics(unit, compact)],
        )
        write_json(task_path, analysis_task.model_dump(mode="json"))
        initialize_result(
            Path(analysis_task.result_path),
            read_json(Path(analysis_task.result_skeleton_path)),
        )
        add_action(progress, ActionState(
            action_id=f"{state['run_id']}:analysis:{unit.unit_id}",
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
        try:
            result = UnitSemanticResult.model_validate(read_json(Path(task.result_path)))
            validate_unit_result(task, result, read_json(Path(task.selected_inputs_path)))
            write_json(Path(task.result_path), result.model_dump(mode="json"))
        except Exception as exc:
            _fail_action(state, progress, action, exc)
            raise
        action.status = "accepted"
        progress.completed_analysis_units.append(unit_id)

    run_dir = run_directory(state)
    source_manifest = read_json(run_dir / "inputs" / "source-manifest.json")
    task = IndependentReviewTask(
        run_id=state["run_id"],
        target=state["task_contract"]["target"],
        repositories=[
            RepositoryRef.model_validate(item) for item in source_manifest["repositories"]
        ],
        unit_plan_path=str(run_dir / "inputs" / "unit-plan.json"),
        inventory_path=str(run_dir / "inputs" / "inventory.json"),
        source_manifest_path=str(run_dir / "inputs" / "source-manifest.json"),
        selected_inputs_path=str(run_dir / "inputs" / "selected-inputs.json"),
        rubric_paths=GENERAL_RUBRICS,
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
        action_id=f"{state['run_id']}:review",
        action="dispatch_agent",
        role="review",
        stage="independent_review",
        task_path=str(task_path),
    ))
    save_progress(state, progress)
    return _waiting(state, progress)


def _validate_review(progress, result) -> None:
    known_units = {unit.unit_id for unit in progress.analysis_units}
    finding_keys = [finding.finding_key for finding in result.findings]
    if len(finding_keys) != len(set(finding_keys)):
        raise ValueError("复核 finding_key 不能重复")
    for finding in result.findings:
        unknown = set(finding.affected_unit_ids) - known_units
        if unknown:
            raise ValueError(f"复核引用了未知单元：{sorted(unknown)}")
        _validate_evidence_for_units(
            progress,
            finding.evidence,
            finding.affected_unit_ids,
            f"复核 finding {finding.finding_key}",
        )


def _validate_comparison_review(
    progress,
    independent: IndependentReviewResult,
    comparison: ComparisonReviewResult,
    selected_inputs: dict,
) -> None:
    _validate_review(progress, comparison)
    independent_keys = {finding.finding_key for finding in independent.findings}
    independent_by_key = {
        finding.finding_key: finding for finding in independent.findings
    }
    decision_keys = [
        decision.finding_key
        for decision in comparison.independent_finding_decisions
    ]
    if len(decision_keys) != len(set(decision_keys)):
        raise ValueError("盲审 finding 的复核决定不能重复")
    missing = independent_keys - set(decision_keys)
    extra = set(decision_keys) - independent_keys
    if missing or extra:
        raise ValueError(
            "对照复核没有逐条裁决盲审 finding："
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )
    for decision in comparison.independent_finding_decisions:
        _validate_evidence_for_units(
            progress,
            decision.evidence,
            independent_by_key[decision.finding_key].affected_unit_ids,
            f"盲审裁决 {decision.finding_key}",
        )
    comparison_keys = {finding.finding_key for finding in comparison.findings}
    duplicates = independent_keys & comparison_keys
    if duplicates:
        raise ValueError(f"对照复核 finding_key 与盲审重复：{sorted(duplicates)}")

    asset_items = selected_inputs.get("asset_items", {})
    mechanisms = selected_inputs.get("defect_mechanisms", {})
    coverage_ids = {
        item["coverage_id"] for item in selected_inputs.get("coverage_gaps", [])
    }
    known_input_ids = set(asset_items) | set(mechanisms) | coverage_ids

    def check_input_references(finding) -> None:
        unknown = set(finding.linked_input_ids) - known_input_ids
        if unknown:
            raise ValueError(
                f"复核 finding {finding.finding_key} 引用了未知输入：{sorted(unknown)}"
            )

    for decision in comparison.independent_finding_decisions:
        if decision.disposition != "dismissed":
            check_input_references(independent_by_key[decision.finding_key])
    for finding in comparison.findings:
        check_input_references(finding)


def _accept_independent_review(state: PangeaState, progress, action) -> PangeaState:
    task = IndependentReviewTask.model_validate(read_json(review_task_path(state)))
    try:
        result = IndependentReviewResult.model_validate(read_json(Path(task.result_path)))
        _validate_review(progress, result)
        write_json(Path(task.result_path), result.model_dump(mode="json"))
    except Exception as exc:
        _fail_action(state, progress, action, exc)
        raise
    action.status = "accepted"

    comparison_task = ComparisonReviewTask(
        run_id=state["run_id"],
        target=state["task_contract"]["target"],
        unit_plan_path=task.unit_plan_path,
        analysis_task_paths={
            unit.unit_id: str(analysis_task_path(state, unit.unit_id))
            for unit in progress.analysis_units
        },
        analysis_result_paths={
            unit.unit_id: str(analysis_result_path(state, unit.unit_id))
            for unit in progress.analysis_units
        },
        independent_review_result_path=task.result_path,
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
        action_id=f"{state['run_id']}:comparison-review",
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
    try:
        comparison = ComparisonReviewResult.model_validate(
            read_json(Path(comparison_task.result_path))
        )
        independent = IndependentReviewResult.model_validate(
            read_json(Path(comparison_task.independent_review_result_path))
        )
        _validate_comparison_review(
            progress,
            independent,
            comparison,
            read_json(Path(comparison_task.selected_inputs_path)),
        )
        write_json(Path(comparison_task.result_path), comparison.model_dump(mode="json"))
    except Exception as exc:
        _fail_action(state, progress, action, exc)
        raise
    action.status = "accepted"
    decisions = {
        decision.finding_key: decision.disposition
        for decision in comparison.independent_finding_decisions
    }
    retained_independent_findings = [
        finding
        for finding in independent.findings
        if decisions[finding.finding_key] != "dismissed"
    ]
    all_findings = [*retained_independent_findings, *comparison.findings]
    if not all_findings:
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
    for unit in progress.analysis_units:
        findings = findings_by_unit.get(unit.unit_id)
        if not findings:
            continue
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
        task_path = closure_task_path(state, unit.unit_id)
        closure_task = ClosureTask(
            run_id=state["run_id"],
            target=state["task_contract"]["target"],
            unit=unit,
            repository=repositories[unit.repo_id],
            original_task_path=str(original_task_path),
            original_result_path=original_task.result_path,
            review_findings=findings,
            result_schema_path=str(project_path("schemas", "analysis_result.schema.json")),
            result_path=str(closure_result_path(state, unit.unit_id)),
            rubric_paths=original_task.rubric_paths,
        )
        write_json(task_path, closure_task.model_dump(mode="json"))
        initialize_result(
            Path(closure_task.result_path),
            read_json(Path(closure_task.original_result_path)),
        )
        add_action(progress, ActionState(
            action_id=f"{state['run_id']}:closure:{unit.unit_id}",
            action="continue_agent",
            role="closure",
            stage="targeted_closure",
            task_path=str(task_path),
            task_id=origin_action.task_id,
        ))
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
        try:
            result = UnitSemanticResult.model_validate(read_json(Path(closure_task.result_path)))
            validate_unit_result(
                original_task,
                result,
                read_json(Path(original_task.selected_inputs_path)),
            )
            expected_findings = {
                finding.finding_key for finding in closure_task.review_findings
            }
            actual_findings = [
                decision.finding_key for decision in result.review_finding_decisions
            ]
            if len(actual_findings) != len(set(actual_findings)) or set(actual_findings) != expected_findings:
                raise ValueError(
                    "定向补齐没有逐项处理复核发现："
                    f"missing={sorted(expected_findings - set(actual_findings))} "
                    f"extra={sorted(set(actual_findings) - expected_findings)}"
                )
            write_json(Path(closure_task.result_path), result.model_dump(mode="json"))
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
