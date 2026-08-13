from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pangea_agent.agent_io import canonical_digest, read_json, write_json
from pangea_agent.graph.run_store import (
    analysis_result_path,
    analysis_task_path,
    artifact_digest,
    load_final_state,
    load_progress,
    load_reviewer_unavailable,
    load_review_result,
    load_review_task,
    load_termination,
    load_worker_result,
    load_worker_task,
    normalize_review_result_path,
    normalize_worker_result_path,
    rework_task_path,
    review_result_path,
    review_task_path,
    review_task_digest,
    reviewer_unavailable_path,
    run_directory,
    save_final_state,
    save_progress,
    termination_path,
    worker_task_digest,
)
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.validation import (
    ArtifactRejected,
    normalize_unique_ids,
    validate_review_result,
    validate_worker_result,
    validation_message,
)
from pangea_agent.models.quality import QualityReport
from pangea_agent.models.run import RunProgress
from pangea_agent.models.worker import ResultRef, ReviewTask, WorkerResult, WorkerTask
from pangea_agent.documents.coverage import match_coverage_records
from pangea_agent.report import reports_are_complete


def _hydrate_run_context(state: PangeaState, progress: RunProgress) -> PangeaState:
    run_dir = run_directory(state)
    tasks = [load_worker_task(analysis_task_path(state, unit_id)) for unit_id in progress.analysis_units]
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
        {"kind": "missing_dependency", "package": package, "scope": "C/C++ structural parsing"}
        for package in inventory.get("missing_dependencies", [])
    )
    active_kinds = {
        "analysis_result_rejected",
        "analysis_results_rejected",
        "review_result_rejected",
        "rework_result_rejected",
        "rework_results_rejected",
        "rework_review_rejected",
    }
    progress.errors = [error for error in progress.errors if error.get("kind") in active_kinds]
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
        "coverage_report": match_coverage_records(source_manifest.get("coverage_records", []), inventory),
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


def _load_analysis_results(state: PangeaState, progress: RunProgress) -> list[WorkerResult] | None:
    results: list[WorkerResult] = []
    completed: list[str] = []
    for unit_id in progress.analysis_units:
        task_path = analysis_task_path(state, unit_id)
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
            continue
        completed.append(unit_id)
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


def _prepare_review(state: PangeaState, progress: RunProgress, results: list[WorkerResult]) -> None:
    refs = []
    for result in results:
        path = analysis_result_path(state, result.unit_id, 0)
        refs.append(ResultRef(unit_id=result.unit_id, result_path=str(path), result_digest=artifact_digest(result)))
    analysis_tasks = [
        load_worker_task(analysis_task_path(state, unit_id))
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
        contract_digest=progress.contract_digest,
        task_digest="0" * 64,
        result_path=str(review_result_path(state)),
        analysis_results=refs,
    )
    task.task_digest = review_task_digest(task)
    write_json(review_task_path(state), task.model_dump(mode="json"))
    progress.phase = "WAITING_REVIEW"


def _prepare_rework_review(state: PangeaState, progress: RunProgress, results: list[WorkerResult]) -> None:
    initial_task = load_review_task(review_task_path(state))
    initial_review = load_review_result(review_result_path(state))
    refs = []
    for result in results:
        attempt = 1 if rework_task_path(state, result.unit_id).exists() else 0
        path = analysis_result_path(state, result.unit_id, attempt)
        refs.append(ResultRef(unit_id=result.unit_id, result_path=str(path), result_digest=artifact_digest(result)))
    task = ReviewTask(
        run_id=state["run_id"],
        target=initial_task.target,
        repositories=initial_task.repositories,
        inventory_path=initial_task.inventory_path,
        source_manifest_path=initial_task.source_manifest_path,
        contract_digest=progress.contract_digest,
        task_digest="0" * 64,
        stage="rework_verification",
        result_path=str(review_result_path(state, "rework")),
        analysis_results=refs,
        same_reviewer_id=initial_review.reviewer_id,
        prior_issues=initial_review.issues,
    )
    task.task_digest = review_task_digest(task)
    write_json(review_task_path(state, "rework"), task.model_dump(mode="json"))
    progress.phase = "WAITING_REWORK_REVIEW"


def _prepare_rework(state: PangeaState, progress: RunProgress, review) -> None:
    issues_by_unit = defaultdict(list)
    for issue in review.issues:
        issues_by_unit[issue.unit_id].append(issue)
    for unit_id, issues in issues_by_unit.items():
        original_task = load_worker_task(analysis_task_path(state, unit_id))
        original_result = load_worker_result(Path(original_task.result_path), original_task)
        task = WorkerTask(
            task_type="rework",
            run_id=original_task.run_id,
            target=original_task.target,
            unit=original_task.unit,
            repositories=original_task.repositories,
            index_path=original_task.index_path,
            inventory_path=original_task.inventory_path,
            source_manifest_path=original_task.source_manifest_path,
            coverage_context=original_task.coverage_context,
            contract_digest=original_task.contract_digest,
            attempt=1,
            input_digest="0" * 64,
            result_path=str(analysis_result_path(state, unit_id, 1)),
            preferred_worker_id=original_result.worker_id,
            replacement_allowed=True,
            prior_result_path=str(original_task.result_path),
            prior_result_digest=artifact_digest(original_result),
            review_issues=issues,
        )
        task.input_digest = worker_task_digest(task)
        write_json(rework_task_path(state, unit_id), task.model_dump(mode="json"))
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
                original_task = load_worker_task(analysis_task_path(state, unit_id))
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
            continue
        completed_rework.append(unit_id)
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
        if git.get("version_status") != "verified":
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
    final = _state_with_results(state, results, status, unresolved)
    completeness = _completeness_issues(final)
    if completeness:
        status = "UNRESOLVED"
        unresolved = unresolved + completeness
        final = _state_with_results(state, results, status, unresolved)
    progress.quality_status = status
    progress.phase = "READY_TO_FINALIZE"
    final = final | {"phase": progress.phase}
    save_final_state(final)
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
    elif progress.phase == "WAITING_REWORK_REVIEW":
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
        load_worker_result(analysis_result_path(state, reference.unit_id, attempt))


def _rebuild_terminal_state(state: PangeaState, progress: RunProgress) -> PangeaState:
    termination = termination_path(state)
    if termination.exists():
        signal = load_termination(termination)
        if signal.run_id != state["run_id"]:
            raise ArtifactRejected("终止信号不属于当前 Run")
        if signal.phase == "WAITING_REWORK_REVIEW":
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
        validate_review_result(task, result, set(progress.analysis_units))
        if result.reviewer_id != task.same_reviewer_id or result.status == "REWORK":
            raise ArtifactRejected("返工复核结果不符合原 reviewer 和单次返工约束")
    else:
        task = load_review_task(review_task_path(state))
        _validate_review_inputs(state, task)
        result = load_review_result(review_result_path(state))
        validate_review_result(task, result, set(progress.analysis_units))
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
    if canonical_digest(state["task_contract"]) != progress.contract_digest:
        raise ArtifactRejected("请使用 resume-run --run-id <run_id> 继续这个 Run")
    state = _hydrate_run_context(state, progress)

    if progress.phase in {"WAITING_REVIEW", "WAITING_REWORK", "WAITING_REWORK_REVIEW"}:
        terminated = _terminate_if_requested(state, progress)
        if terminated is not None:
            return terminated

    if progress.phase == "WAITING_ANALYSIS":
        results = _load_analysis_results(state, progress)
        if results is not None:
            _prepare_review(state, progress, results)
        save_progress(state, progress)
        return {**state, "phase": progress.phase}

    if progress.phase == "WAITING_REVIEW":
        task_path = review_task_path(state)
        result_path = review_result_path(state)
        if not result_path.exists():
            return {**state, "phase": progress.phase}
        try:
            task = load_review_task(task_path)
            result_path = normalize_review_result_path(task_path, task)
            _validate_review_inputs(state, task)
            result = load_review_result(result_path, task)
            validate_review_result(task, result, set(progress.analysis_units))
            write_json(result_path, result.model_dump(mode="json"))
        except Exception as exc:
            _record_error(progress, "review_result_rejected", result_path, exc)
            save_progress(state, progress)
            return {**state, "phase": progress.phase}
        if result.status == "REWORK":
            _clear_error(progress, "review_result_rejected", result_path)
            _prepare_rework(state, progress, result)
            save_progress(state, progress)
            return {**state, "phase": progress.phase}
        _clear_error(progress, "review_result_rejected", result_path)
        results = _load_analysis_results(state, progress) or []
        unresolved = [issue.model_dump(mode="json") for issue in result.issues]
        return _ready_to_finalize(state, progress, results, result.status, unresolved)

    if progress.phase == "WAITING_REWORK":
        results = _load_rework_results(state, progress)
        if results is None:
            save_progress(state, progress)
            return {**state, "phase": progress.phase}
        _prepare_rework_review(state, progress, results)
        save_progress(state, progress)
        return {**state, "phase": progress.phase}

    if progress.phase == "WAITING_REWORK_REVIEW":
        task_path = review_task_path(state, "rework")
        result_path = review_result_path(state, "rework")
        unavailable_path = reviewer_unavailable_path(state)
        if unavailable_path.exists():
            unavailable = load_reviewer_unavailable(unavailable_path)
            task = load_review_task(task_path)
            if unavailable.run_id != state["run_id"] or unavailable.reviewer_id != task.same_reviewer_id:
                raise ArtifactRejected("reviewer unavailable 信号不属于原复核 Agent")
            results = _load_rework_results(state, progress) or []
            unresolved = [{"reason": unavailable.reason, "reviewer_id": unavailable.reviewer_id}]
            return _ready_to_finalize(state, progress, results, "UNRESOLVED", unresolved)
        if not result_path.exists():
            return {**state, "phase": progress.phase}
        try:
            task = load_review_task(task_path)
            result_path = normalize_review_result_path(task_path, task)
            _validate_review_inputs(state, task)
            result = load_review_result(result_path, task)
            validate_review_result(task, result, set(progress.analysis_units))
            write_json(result_path, result.model_dump(mode="json"))
            if result.reviewer_id != task.same_reviewer_id:
                raise ArtifactRejected("返工复核必须由原 review-worker 完成")
            if result.status == "REWORK":
                raise ArtifactRejected("返工复核不能再次要求返工，只能 PASS 或 UNRESOLVED")
        except Exception as exc:
            _record_error(progress, "rework_review_rejected", result_path, exc)
            save_progress(state, progress)
            return {**state, "phase": progress.phase}
        _clear_error(progress, "rework_review_rejected", result_path)
        results = _load_rework_results(state, progress) or []
        unresolved = [issue.model_dump(mode="json") for issue in result.issues]
        return _ready_to_finalize(state, progress, results, result.status, unresolved)

    if progress.phase == "READY_TO_FINALIZE":
        final = load_final_state(state)
        if final is None:
            raise ArtifactRejected("final-state.json 缺失，不能恢复报告生成")
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
        if report_path.exists():
            terminal["report_path"] = str(report_path)
            html_path = run_directory(state) / "report.html"
            if html_path.exists():
                terminal["html_report_path"] = str(html_path)
    return terminal
