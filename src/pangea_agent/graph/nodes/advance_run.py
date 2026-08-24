from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pangea_agent.agent_io import agent_path, read_json, write_json
from pangea_agent.documents.coverage import relevant_zero_coverage
from pangea_agent.graph.actions import agent_action
from pangea_agent.graph.run_store import (
    analysis_result_path,
    analysis_task_path,
    load_final_state,
    load_independent_review_result,
    load_progress,
    load_ready_state,
    load_reviewer_unavailable,
    load_review_result,
    load_review_task,
    load_termination,
    load_worker_result,
    load_worker_task,
    normalize_worker_result_path,
    rework_task_path,
    review_result_path,
    review_task_path,
    reviewer_unavailable_path,
    run_directory,
    save_final_state,
    save_ready_state,
    save_progress,
    termination_path,
    review_result_skeleton,
    worker_result_skeleton,
)
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.validation import (
    ArtifactRejected,
    normalize_unique_ids,
    validate_independent_review_result,
    validate_review_result,
    validate_worker_stage_result,
    validate_worker_result,
    validation_message,
)
from pangea_agent.models.quality import QualityReport
from pangea_agent.models.run import AgentSession, RunProgress
from pangea_agent.models.worker import (
    IndependentReviewResult,
    ResultRef,
    ReviewTask,
    TaskRef,
    WorkerResult,
    WorkerTask,
)
from pangea_agent.documents.coverage import filter_inventory_to_sources, match_coverage_records
from pangea_agent.report import reports_are_complete


ACTIVE_RESULT_ERROR_KINDS = {
    "analysis_result_rejected",
    "analysis_results_rejected",
    "independent_review_rejected",
    "review_result_rejected",
    "rework_result_rejected",
    "rework_results_rejected",
    "rework_review_rejected",
}


def _hydrate_run_context(state: PangeaState, progress: RunProgress) -> PangeaState:
    run_dir = run_directory(state)
    analysis_stage = {
        "WAITING_SOURCE_CHECKPOINT": "source_checkpoint",
        "WAITING_RISK_ANALYSIS": "risk_analysis",
    }.get(progress.phase, "test_generation")
    tasks = [
        load_worker_task(analysis_task_path(state, unit_id, analysis_stage))
        for unit_id in progress.analysis_units
    ]
    first_task = tasks[0]
    manifest_path = run_dir / "inputs" / "source-manifest.json"
    inventory_path = run_dir / "inputs" / "inventory.json"
    source_manifest = read_json(manifest_path) if manifest_path.exists() else {}
    inventory = read_json(inventory_path) if inventory_path.exists() else {}
    environment_errors = [
        {"kind": "document_parse_warning", **warning}
        for warning in source_manifest.get("warnings", [])
    ]
    environment_errors.extend(
        {"kind": "missing_dependency", **dependency}
        for dependency in source_manifest.get("missing_dependencies", [])
    )
    environment_errors.extend(
        {"kind": "missing_dependency", "package": package, "scope": "源码结构化解析"}
        for package in inventory.get("missing_dependencies", [])
    )
    progress.errors = [error for error in progress.errors if error.get("kind") in ACTIVE_RESULT_ERROR_KINDS]
    for error in environment_errors:
        if error not in progress.error_history:
            progress.error_history.append(error)
    errors = progress.errors + environment_errors
    return {
        **state,
        "repositories": list({
            repo.repo_id: repo.model_dump(mode="json")
            for task in tasks
            for repo in task.repositories
        }.values()),
        "module_scope": list(state["task_contract"].get("source_scope", [])),
        "scope_expansion": source_manifest.get("scope_expansion", {}),
        "source_manifest": source_manifest,
        "index_path": first_task.index_path,
        "inventory": inventory,
        "coverage_report": relevant_zero_coverage(
            match_coverage_records(
                source_manifest.get("coverage_records", []),
                filter_inventory_to_sources(
                    inventory,
                    {
                        (task.unit.repo_id, path)
                        for task in tasks
                        for path in task.unit.source_scope
                    },
                ),
                path_inventory=filter_inventory_to_sources(
                    inventory,
                    {
                        (task.unit.repo_id, path)
                        for task in tasks
                        for path in [*task.unit.source_scope, *task.unit.context_scope]
                    },
                ),
            )
        ),
        "analysis_units": [
            task.unit.model_dump(mode="json") for task in tasks
        ],
        "parse_failures": inventory.get("parse_failures", []),
        "unread_images": source_manifest.get("attachments", []),
        "errors": errors,
    }


def _record_error(progress: RunProgress, kind: str, artifact: Path, exc: Exception) -> None:
    error = {"kind": kind, "artifact": str(artifact), "reason": validation_message(exc)}
    if error not in progress.errors:
        progress.errors.append(error)
    if error not in progress.error_history:
        progress.error_history.append(error)


def _clear_error(progress: RunProgress, kind: str, artifact: Path) -> None:
    progress.errors = [
        error for error in progress.errors
        if error.get("kind") != kind or error.get("artifact") != str(artifact)
    ]


def _complete_session(progress: RunProgress, key: str) -> None:
    session = progress.agent_sessions.get(key)
    if session is not None:
        session.status = "completed"


def _mark_session_pending(progress: RunProgress, key: str) -> None:
    session = progress.agent_sessions.get(key)
    if session is not None:
        session.status = "pending"


def _sync_state_errors(state: PangeaState, progress: RunProgress) -> PangeaState:
    environment_errors = [
        error for error in state.get("errors", [])
        if error.get("kind") not in ACTIVE_RESULT_ERROR_KINDS
    ]
    return {**state, "errors": list(progress.errors) + environment_errors}


def _load_analysis_results(state: PangeaState, progress: RunProgress) -> list[WorkerResult] | None:
    results: list[WorkerResult] = []
    completed: list[str] = []
    for unit_id in progress.analysis_units:
        task_path = analysis_task_path(state, unit_id, "test_generation")
        task = load_worker_task(task_path)
        path = normalize_worker_result_path(task_path, task)
        if not path.exists():
            continue
        try:
            result = load_worker_result(path, task)
            validate_worker_result(task, result)
            write_json(path, result.model_dump(mode="json"))
        except Exception as exc:
            _record_error(progress, "analysis_result_rejected", path, exc)
            _mark_session_pending(progress, f"analysis:{unit_id}")
            continue
        completed.append(unit_id)
        _complete_session(progress, f"analysis:{unit_id}")
        _clear_error(progress, "analysis_result_rejected", path)
        results.append(result)
    progress.completed_analysis_units = completed
    if len(results) != len(progress.analysis_units):
        return None
    normalize_unique_ids(results)
    for result in results:
        write_json(analysis_result_path(state, result.unit_id, 0), result.model_dump(mode="json"))
    _clear_error(progress, "analysis_results_rejected", run_directory(state) / "agent-results" / "analysis")
    return results


def _prepare_review(state: PangeaState, progress: RunProgress) -> None:
    analysis_tasks = [
        load_worker_task(analysis_task_path(state, unit_id, "test_generation"))
        for unit_id in progress.analysis_units
    ]
    first_task = analysis_tasks[0]
    repositories = list({
        repository.repo_id: repository
        for analysis_task in analysis_tasks
        for repository in analysis_task.repositories
    }.values())
    task = ReviewTask(
        run_id=state["run_id"],
        target=first_task.target,
        repositories=repositories,
        inventory_path=first_task.inventory_path,
        source_manifest_path=first_task.source_manifest_path,
        stage="independent_review",
        result_path=str(review_result_path(state, "independent")),
        analysis_tasks=[
            TaskRef(
                unit_id=unit_id,
                task_path=str(analysis_task_path(state, unit_id, "test_generation")),
            )
            for unit_id in progress.analysis_units
        ],
    )
    write_json(review_task_path(state, "independent"), task.model_dump(mode="json"))
    progress.agent_sessions["review"] = AgentSession(role="review", stage="independent_review")
    progress.phase = "WAITING_INDEPENDENT_REVIEW"


def _expected_independent_checks(state: PangeaState, task: ReviewTask) -> set[tuple[str, str]]:
    expected: set[tuple[str, str]] = set()
    referenced_units = {reference.unit_id for reference in task.analysis_tasks}
    known_units = {unit["unit_id"] for unit in state["analysis_units"]}
    if referenced_units != known_units:
        raise ArtifactRejected("独立复核 task 未绑定全部分析单元")
    for reference in task.analysis_tasks:
        expected_path = analysis_task_path(state, reference.unit_id, "test_generation")
        if reference.task_path != agent_path(expected_path):
            raise ArtifactRejected(f"独立复核 task 路径与当前 Run 不一致：{reference.unit_id}")
        worker_task = load_worker_task(expected_path)
        expected.update(
            (reference.unit_id, item.check_id)
            for item in worker_task.semantic_check_items
        )
    return expected


def _prepare_review_comparison(
    state: PangeaState,
    progress: RunProgress,
    independent_task: ReviewTask,
    reviewer_id: str,
    results: list[WorkerResult],
) -> None:
    refs = [
        ResultRef(
            unit_id=result.unit_id,
            result_path=str(analysis_result_path(state, result.unit_id, 0)),
        )
        for result in results
    ]
    task = ReviewTask(
        run_id=state["run_id"],
        target=independent_task.target,
        repositories=independent_task.repositories,
        inventory_path=independent_task.inventory_path,
        source_manifest_path=independent_task.source_manifest_path,
        stage="comparison_review",
        result_path=str(review_result_path(state)),
        analysis_tasks=independent_task.analysis_tasks,
        analysis_results=refs,
        independent_result_path=str(review_result_path(state, "independent")),
        same_reviewer_id=reviewer_id,
    )
    write_json(review_task_path(state), task.model_dump(mode="json"))
    review_session = progress.agent_sessions["review"]
    review_session.stage = "comparison_review"
    review_session.status = "pending"
    progress.phase = "WAITING_COMPARISON_REVIEW"


def _prepare_rework_review(state: PangeaState, progress: RunProgress, results: list[WorkerResult]) -> None:
    initial_task = load_review_task(review_task_path(state))
    comparison_review = load_review_result(review_result_path(state))
    refs = []
    for result in results:
        attempt = 1 if rework_task_path(state, result.unit_id).exists() else 0
        path = analysis_result_path(state, result.unit_id, attempt)
        refs.append(ResultRef(unit_id=result.unit_id, result_path=str(path)))
    task = ReviewTask(
        run_id=state["run_id"],
        target=initial_task.target,
        repositories=initial_task.repositories,
        inventory_path=initial_task.inventory_path,
        source_manifest_path=initial_task.source_manifest_path,
        stage="rework_verification",
        result_path=str(review_result_path(state, "rework")),
        analysis_tasks=initial_task.analysis_tasks,
        analysis_results=refs,
        independent_result_path=initial_task.independent_result_path,
        same_reviewer_id=comparison_review.reviewer_id,
        prior_issues=comparison_review.issues,
    )
    write_json(review_task_path(state, "rework"), task.model_dump(mode="json"))
    independent_result = load_independent_review_result(
        Path(task.independent_result_path)
    )
    skeleton = review_result_skeleton(
        task,
        independent_result,
        prior_comparison_result=comparison_review,
    )
    skeleton["reviewer_id"] = comparison_review.reviewer_id
    write_json(Path(task.result_path), skeleton)
    review_session = progress.agent_sessions.get("review")
    if review_session is None:
        review_session = AgentSession(role="review", stage="rework_verification")
        progress.agent_sessions["review"] = review_session
    review_session.stage = "rework_verification"
    review_session.status = "pending"
    progress.phase = "WAITING_REWORK_VERIFICATION"


def _prepare_rework(state: PangeaState, progress: RunProgress, review) -> None:
    issues_by_unit = defaultdict(list)
    for issue in review.issues:
        issues_by_unit[issue.unit_id].append(issue)
    for unit_id, issues in issues_by_unit.items():
        original_task = load_worker_task(
            analysis_task_path(state, unit_id, "test_generation")
        )
        original_result = load_worker_result(Path(original_task.result_path), original_task)
        task = WorkerTask(
            task_type="rework",
            stage="rework",
            run_id=original_task.run_id,
            target=original_task.target,
            unit=original_task.unit,
            repositories=original_task.repositories,
            index_path=original_task.index_path,
            inventory_path=original_task.inventory_path,
            source_manifest_path=original_task.source_manifest_path,
            allowed_material_paths=original_task.allowed_material_paths,
            checkpoint_rubric_paths=original_task.checkpoint_rubric_paths,
            coverage_context=original_task.coverage_context,
            failure_signal_context=original_task.failure_signal_context,
            semantic_check_items=original_task.semantic_check_items,
            attempt=1,
            result_path=str(analysis_result_path(state, unit_id, 1)),
            preferred_worker_id=original_result.worker_id,
            replacement_allowed=True,
            prior_result_path=str(original_task.result_path),
            review_issues=issues,
        )
        write_json(rework_task_path(state, unit_id), task.model_dump(mode="json"))
        # Graph prepares the only writable rework artifact before dispatch. The
        # worker never needs to edit or copy the read-only prior result.
        write_json(Path(task.result_path), worker_result_skeleton(task))
        analysis_session = progress.agent_sessions.get(f"analysis:{unit_id}")
        progress.agent_sessions[f"rework:{unit_id}"] = AgentSession(
            role="rework",
            unit_id=unit_id,
            stage="rework",
            task_id=analysis_session.task_id if analysis_session else None,
        )
    _complete_session(progress, "review")
    progress.phase = "WAITING_REWORK"
    progress.quality_status = "REWORK"


def _load_rework_results(state: PangeaState, progress: RunProgress) -> list[WorkerResult] | None:
    final_results: list[WorkerResult] = []
    completed_rework: list[str] = []
    for unit_id in progress.analysis_units:
        rework_path = rework_task_path(state, unit_id)
        if not rework_path.exists():
            original_path = analysis_result_path(state, unit_id, 0)
            try:
                original_task = load_worker_task(
                    analysis_task_path(state, unit_id, "test_generation")
                )
                original_result = load_worker_result(original_path, original_task)
                validate_worker_result(original_task, original_result)
            except Exception as exc:
                _record_error(progress, "rework_result_rejected", original_path, exc)
                continue
            _clear_error(progress, "rework_result_rejected", original_path)
            final_results.append(original_result)
            continue
        task = load_worker_task(rework_path)
        result_path = normalize_worker_result_path(rework_path, task)
        if not result_path.exists():
            continue
        try:
            result = load_worker_result(result_path, task)
            validate_worker_result(task, result)
            write_json(result_path, result.model_dump(mode="json"))
        except Exception as exc:
            _record_error(progress, "rework_result_rejected", result_path, exc)
            _mark_session_pending(progress, f"rework:{unit_id}")
            continue
        completed_rework.append(unit_id)
        _complete_session(progress, f"rework:{unit_id}")
        _clear_error(progress, "rework_result_rejected", result_path)
        final_results.append(result)
    progress.completed_rework_units = completed_rework
    expected_rework = len(list((run_directory(state) / "agent-tasks" / "rework").glob("*.json")))
    if len(completed_rework) != expected_rework or len(final_results) != len(progress.analysis_units):
        return None
    normalize_unique_ids(final_results)
    for result in final_results:
        attempt = 1 if rework_task_path(state, result.unit_id).exists() else 0
        write_json(analysis_result_path(state, result.unit_id, attempt), result.model_dump(mode="json"))
    _clear_error(progress, "rework_results_rejected", run_directory(state) / "agent-results" / "rework")
    return final_results


def _analysis_summary(state: PangeaState, result: WorkerResult) -> dict:
    unit = next(
        item for item in state.get("analysis_units", [])
        if item.get("unit_id") == result.unit_id
    )
    owned = {(unit["repo_id"], path) for path in unit.get("source_scope", [])}
    function_count = sum(
        len(item.get("functions", []))
        for item in state.get("inventory", {}).get("files", [])
        if (item.get("repo_id"), item.get("path")) in owned
    )
    direct_callee_paths = {
        item.get("path")
        for item in state.get("scope_expansion", {}).get("context_files", [])
        if item.get("repo_id") == unit["repo_id"]
        and str(item.get("reason", "")).startswith("direct_callee:")
        and item.get("path") in unit.get("context_scope", [])
    }
    return {
        "unit_id": result.unit_id,
        "worker_id": result.worker_id,
        "summary": result.summary,
        "assigned_source_files": len(unit.get("source_scope", [])),
        "reviewed_source_files": len(set(result.analysis_checkpoint.source_paths_reviewed)),
        "function_count": function_count,
        "failure_path_count": len(result.analysis_checkpoint.failure_paths),
        "risk_count": len(result.risks),
        "test_case_count": len(result.test_cases),
        "direct_callee_context_count": len(direct_callee_paths),
    }


def _state_with_results(state: PangeaState, results: list[WorkerResult], status: str, unresolved: list[dict]) -> PangeaState:
    risks = [risk.model_dump(mode="json") for result in results for risk in result.risks]
    cases = [case.model_dump(mode="json") for result in results for case in result.test_cases]
    flows = [flow.model_dump(mode="json") for result in results for flow in result.business_flows]
    pending_evidence = [
        evidence.model_dump(mode="json")
        for result in results
        for evidence in (
            list(result.evidence)
            + [item for flow in result.business_flows for item in flow.evidence]
            + [item for risk in result.risks for item in risk.evidence]
        )
        if evidence.status == "pending_confirmation"
    ]
    analyzed_attachments = {
        finding.attachment_path
        for result in results
        for finding in result.visual_findings
    }
    unread_images = [
        attachment
        for attachment in state.get("unread_images", [])
        if attachment.get("attachment_path") not in analyzed_attachments
    ]
    quality = QualityReport(
        status=status,
        checks=[
            "所有 worker 均正常完成",
            "证据已自动关联；无法确定的条目保留为证据待确认",
            "跨单元风险和测试用例编号已自动消除冲突",
            "独立 review-worker 已完成语义复核",
        ],
        unresolved=unresolved,
    )
    return {
        **state,
        "analysis_summaries": [_analysis_summary(state, result) for result in results],
        "material_decisions": [
            {"unit_id": result.unit_id, **decision.model_dump(mode="json")}
            for result in results
            for decision in result.analysis_checkpoint.material_decisions
        ],
        "material_evidence": [
            {"unit_id": result.unit_id, **evidence.model_dump(mode="json")}
            for result in results
            for evidence in result.evidence
            if evidence.chunk_id.startswith("material:")
        ],
        "business_flows": flows,
        "visual_findings": [
            finding.model_dump(mode="json")
            for result in results
            for finding in result.visual_findings
        ],
        "risks": risks,
        "test_cases": cases,
        "pending_evidence": pending_evidence,
        "unread_images": unread_images,
        "quality_report": quality.model_dump(mode="json"),
    }


def _completeness_issues(state: PangeaState) -> list[dict]:
    issues: list[dict] = []
    issues.extend({"kind": "parse_failure", **item} for item in state.get("parse_failures", []))
    issues.extend({"kind": "unread_image", **item} for item in state.get("unread_images", []))
    issues.extend(state.get("errors", []))
    for item in state.get("source_manifest", {}).get("repository_versions", []):
        git = item.get("git", {})
        if git.get("is_git") and git.get("version_status") != "verified":
            issues.append({
                "kind": "source_version_unverifiable",
                "repo_id": item.get("repo_id"),
                "reason": git.get("reason", f"source version status is {git.get('version_status', 'unknown')}"),
            })
    return issues


def _ready_to_finalize(
    state: PangeaState,
    progress: RunProgress,
    results: list[WorkerResult],
    status: str,
    unresolved: list[dict],
) -> PangeaState:
    state = _sync_state_errors(state, progress)
    final = _state_with_results(state, results, status, unresolved)
    completeness = _completeness_issues(final)
    if completeness:
        status = "UNRESOLVED"
        unresolved = unresolved + completeness
        final = _state_with_results(state, results, status, unresolved)
    progress.quality_status = status
    progress.phase = "READY_TO_FINALIZE"
    final = final | {"phase": progress.phase}
    save_ready_state(final)
    save_progress(state, progress)
    return final


def _terminate_if_requested(state: PangeaState, progress: RunProgress) -> PangeaState | None:
    path = termination_path(state)
    if not path.exists():
        return None
    signal = load_termination(path)
    if signal.run_id != state["run_id"] or signal.phase != progress.phase:
        raise ArtifactRejected("终止信号不属于当前 Run 阶段")
    if progress.phase == "WAITING_REWORK":
        results = _load_analysis_results(state, progress) or []
    elif progress.phase == "WAITING_REWORK_VERIFICATION":
        results = _load_rework_results(state, progress) or []
    else:
        results = _load_analysis_results(state, progress) or []
    return _ready_to_finalize(
        state,
        progress,
        results,
        "UNRESOLVED",
        [{"reason": signal.reason, "phase": signal.phase}],
    )


def _validate_review_inputs(state: PangeaState, task: ReviewTask) -> None:
    for reference in task.analysis_results:
        attempt = 1 if task.stage == "rework_verification" and rework_task_path(state, reference.unit_id).exists() else 0
        task_path = (
            rework_task_path(state, reference.unit_id)
            if attempt == 1
            else analysis_task_path(state, reference.unit_id, "test_generation")
        )
        worker_task = load_worker_task(task_path)
        result_path = analysis_result_path(state, reference.unit_id, attempt)
        if reference.result_path != agent_path(result_path):
            raise ArtifactRejected(f"review 输入路径与当前 Run 不一致：{reference.unit_id}")
        result = load_worker_result(result_path, worker_task)
        validate_worker_result(worker_task, result)
        write_json(result_path, result.model_dump(mode="json"))


def _load_bound_independent_review(
    state: PangeaState,
    task: ReviewTask,
) -> IndependentReviewResult:
    expected_path = review_result_path(state, "independent")
    if task.independent_result_path != agent_path(expected_path):
        raise ArtifactRejected("对照复核绑定的独立复核结果路径与当前 Run 不一致")
    independent_task = load_review_task(review_task_path(state, "independent"))
    independent_result = load_independent_review_result(expected_path, independent_task)
    validate_independent_review_result(
        independent_task,
        independent_result,
        _expected_independent_checks(state, independent_task),
    )
    if independent_result.reviewer_id != task.same_reviewer_id:
        raise ArtifactRejected("对照复核 task 未绑定原独立 reviewer")
    return independent_result


def _rebuild_terminal_state(state: PangeaState, progress: RunProgress) -> PangeaState:
    termination = termination_path(state)
    if termination.exists():
        signal = load_termination(termination)
        if signal.run_id != state["run_id"]:
            raise ArtifactRejected("终止信号不属于当前 Run")
        if signal.phase == "WAITING_REWORK_VERIFICATION":
            results = _load_rework_results(state, progress)
        else:
            results = _load_analysis_results(state, progress)
        if results is None:
            raise ArtifactRejected("终态快照丢失，且终止时的绑定结果无法重建")
        return _ready_to_finalize(
            state,
            progress,
            results,
            "UNRESOLVED",
            [{"reason": signal.reason, "phase": signal.phase}],
        )

    rework_review_task_path = review_task_path(state, "rework")
    if rework_review_task_path.exists():
        task = load_review_task(rework_review_task_path)
        _validate_review_inputs(state, task)
        results = _load_rework_results(state, progress)
        if results is None:
            raise ArtifactRejected("终态快照丢失，且返工结果无法重建")
        unavailable_path = reviewer_unavailable_path(state)
        if unavailable_path.exists():
            unavailable = load_reviewer_unavailable(unavailable_path)
            if unavailable.run_id != state["run_id"] or unavailable.reviewer_id != task.same_reviewer_id:
                raise ArtifactRejected("reviewer unavailable 信号不属于原复核 Agent")
            return _ready_to_finalize(
                state,
                progress,
                results,
                "UNRESOLVED",
                [{"reason": unavailable.reason, "reviewer_id": unavailable.reviewer_id}],
            )
        result = load_review_result(review_result_path(state, "rework"))
        independent_result = _load_bound_independent_review(state, task)
        validate_review_result(
            task,
            result,
            set(progress.analysis_units),
            independent_result,
        )
        if result.reviewer_id != task.same_reviewer_id or result.status == "REWORK":
            raise ArtifactRejected("返工复核结果不符合原 reviewer 和单次返工约束")
    else:
        task = load_review_task(review_task_path(state))
        _validate_review_inputs(state, task)
        result = load_review_result(review_result_path(state))
        independent_result = (
            _load_bound_independent_review(state, task)
            if task.stage == "comparison_review"
            else None
        )
        validate_review_result(
            task,
            result,
            set(progress.analysis_units),
            independent_result,
        )
        if result.status == "REWORK":
            raise ArtifactRejected("终态快照丢失，初审仍要求返工，不能宣称完成")
        results = _load_analysis_results(state, progress)
        if results is None:
            raise ArtifactRejected("终态快照丢失，且分析结果无法重建")
    unresolved = [issue.model_dump(mode="json") for issue in result.issues]
    return _ready_to_finalize(state, progress, results, result.status, unresolved)


def advance_run(state: PangeaState) -> PangeaState:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("progress.json 不存在，不能恢复 Run")
    frozen_contract_path = run_directory(state) / "inputs" / "task-contract.json"
    if not frozen_contract_path.is_file() or read_json(frozen_contract_path) != state["task_contract"]:
        raise ArtifactRejected("请使用 resume-run --run-id <run_id> 继续这个 Run")
    state = _hydrate_run_context(state, progress)

    if progress.phase == "READY_TO_FINALIZE":
        final = load_ready_state(state)
        if final is None:
            raise ArtifactRejected("ready-state.json 缺失，不能恢复报告生成")
        return final

    report_path = run_directory(state) / "report.md"
    terminal = {**state, "phase": progress.phase}
    if progress.phase in {"COMPLETE", "INCOMPLETE"}:
        final = load_final_state(state)
        if final is None:
            return _rebuild_terminal_state(state, progress)
        if final is not None and not reports_are_complete(run_directory(state)):
            final["phase"] = "READY_TO_FINALIZE"
            return final
        if final.get("phase") != progress.phase:
            final["phase"] = progress.phase
            final["run_status"] = progress.phase
            save_final_state(final)
        if report_path.exists():
            terminal["report_path"] = str(report_path)
            html_path = run_directory(state) / "report.html"
            if html_path.exists():
                terminal["html_report_path"] = str(html_path)
        return terminal
    raise ArtifactRejected(f"活动阶段必须由对应 Graph 节点恢复：{progress.phase}")
