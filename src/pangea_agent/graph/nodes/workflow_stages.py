from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.actions import MAX_PARALLEL_ACTIONS, agent_action
from pangea_agent.graph.run_store import (
    analysis_result_path,
    analysis_task_path,
    load_independent_review_result,
    load_progress,
    load_review_result,
    load_review_task,
    load_reviewer_unavailable,
    load_worker_result,
    load_worker_task,
    normalize_review_result_path,
    normalize_worker_result_path,
    rework_task_path,
    review_result_path,
    review_task_path,
    reviewer_unavailable_path,
    run_directory,
    save_progress,
)
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.validation import (
    ArtifactRejected,
    validate_independent_review_result,
    validate_review_result,
    validate_worker_stage_result,
)
from pangea_agent.models.run import RunProgress
from pangea_agent.models.worker import WorkerTask

from .advance_run import (
    _clear_error,
    _complete_session,
    _expected_independent_checks,
    _hydrate_run_context,
    _load_analysis_results,
    _load_bound_independent_review,
    _load_rework_results,
    _mark_session_pending,
    _prepare_rework,
    _prepare_rework_review,
    _prepare_review,
    _prepare_review_comparison,
    _ready_to_finalize,
    _record_error,
    _terminate_if_requested,
    _validate_review_inputs,
)
from .prepare_worker_tasks import _allowed_material_paths, _coverage_context


_ANALYSIS_PHASES = {
    "source_checkpoint": "WAITING_SOURCE_CHECKPOINT",
    "risk_analysis": "WAITING_RISK_ANALYSIS",
    "test_generation": "WAITING_TEST_GENERATION",
}


def _resume_context(state: PangeaState) -> tuple[PangeaState, RunProgress]:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("progress.json 不存在，不能恢复 Run")
    frozen_contract_path = run_directory(state) / "inputs" / "task-contract.json"
    if not frozen_contract_path.is_file() or read_json(frozen_contract_path) != state["task_contract"]:
        raise ArtifactRejected("请使用 resume-run --run-id <run_id> 继续这个 Run")
    return _hydrate_run_context(state, progress), progress


def _analysis_actions(
    state: PangeaState,
    progress: RunProgress,
    stage: str,
    unit_ids: list[str],
) -> list[dict]:
    active = sum(
        session.stage == stage and session.status == "dispatched"
        for key, session in progress.agent_sessions.items()
        if key.startswith("analysis:")
    )
    available = max(0, MAX_PARALLEL_ACTIONS - active)
    eligible = [
        unit_id for unit_id in unit_ids
        if progress.agent_sessions[f"analysis:{unit_id}"].status == "pending"
    ][:available]
    return [
        agent_action(
            progress,
            session_key=f"analysis:{unit_id}",
            role="analysis",
            stage=stage,
            unit_id=unit_id,
            task_path=analysis_task_path(state, unit_id, stage),
        )
        for unit_id in eligible
    ]


def _review_actions(
    state: PangeaState,
    progress: RunProgress,
    stage: str,
    task_path: Path,
) -> list[dict]:
    if progress.agent_sessions["review"].status != "pending":
        return []
    return [agent_action(
        progress,
        session_key="review",
        role="review",
        stage=stage,
        task_path=task_path,
    )]


def _write_analysis_validation_feedback(
    state: PangeaState,
    progress: RunProgress,
    unit_ids: list[str],
) -> None:
    for unit_id in unit_ids:
        task_path = analysis_task_path(state, unit_id, "test_generation")
        task = load_worker_task(task_path)
        result_path = str(normalize_worker_result_path(task_path, task))
        feedback = [
            str(error.get("reason", ""))
            for error in progress.errors
            if error.get("kind") == "analysis_result_rejected"
            and error.get("artifact") == result_path
            and str(error.get("reason", "")).strip()
        ]
        payload = task.model_dump(mode="json")
        payload["validation_feedback"] = feedback
        write_json(task_path, WorkerTask.model_validate(payload).model_dump(mode="json"))
        if feedback:
            progress.agent_sessions[f"analysis:{unit_id}"].task_id = None


def _rework_actions(
    state: PangeaState,
    progress: RunProgress,
    unit_ids: list[str],
) -> list[dict]:
    actions: list[dict] = []
    active = sum(
        session.stage == "rework" and session.status == "dispatched"
        for key, session in progress.agent_sessions.items()
        if key.startswith("rework:")
    )
    available = max(0, MAX_PARALLEL_ACTIONS - active)
    eligible = [
        unit_id for unit_id in unit_ids
        if progress.agent_sessions[f"rework:{unit_id}"].status == "pending"
    ][:available]
    for unit_id in eligible:
        task_path = rework_task_path(state, unit_id)
        task = load_worker_task(task_path)
        actions.append(agent_action(
            progress,
            session_key=f"rework:{unit_id}",
            role="rework",
            stage="rework",
            unit_id=unit_id,
            task_path=task_path,
            replacement_allowed=task.replacement_allowed,
        ))
    return actions


def _waiting(
    state: PangeaState,
    progress: RunProgress,
    actions: list[dict],
) -> PangeaState:
    return {
        **state,
        "phase": progress.phase,
        "agent_actions": actions,
        "agent_task_paths": [action["task_path"] for action in actions],
        "next_node": "end",
    }


def _session_completed(progress: RunProgress, session_key: str) -> bool:
    session = progress.agent_sessions.get(session_key)
    return (
        session is not None
        and session.task_id is not None
        and session.status == "completed"
    )


def _accept_analysis_stage(
    state: PangeaState,
    expected_stage: str,
    next_node: str,
) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != _ANALYSIS_PHASES[expected_stage]:
        raise ArtifactRejected(f"当前 Graph 不在 {expected_stage} 阶段")
    completed: list[str] = []
    for unit_id in progress.analysis_units:
        task_path = analysis_task_path(state, unit_id, expected_stage)
        task = load_worker_task(task_path)
        result_path = normalize_worker_result_path(task_path, task)
        if not result_path.exists():
            continue
        if not _session_completed(progress, f"analysis:{unit_id}"):
            continue
        try:
            result = load_worker_result(result_path, task)
            validate_worker_stage_result(task, result, expected_stage)
            write_json(result_path, result.model_dump(mode="json"))
        except Exception as exc:
            _record_error(progress, "analysis_result_rejected", result_path, exc)
            _mark_session_pending(progress, f"analysis:{unit_id}")
            continue
        _clear_error(progress, "analysis_result_rejected", result_path)
        completed.append(unit_id)
    save_progress(state, progress)
    pending = [unit_id for unit_id in progress.analysis_units if unit_id not in completed]
    if pending:
        return _waiting(state, progress, _analysis_actions(state, progress, expected_stage, pending))
    return {**state, "phase": progress.phase, "next_node": next_node}


def _prepare_analysis_stage(state: PangeaState, stage: str) -> PangeaState:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("progress.json 不存在")
    previous_phase = {
        "risk_analysis": "WAITING_SOURCE_CHECKPOINT",
        "test_generation": "WAITING_RISK_ANALYSIS",
    }[stage]
    if progress.phase != previous_phase:
        raise ArtifactRejected(f"不能从 {progress.phase} 进入 {stage}")
    run_dir = run_directory(state)
    previous_stage = {
        "risk_analysis": "source_checkpoint",
        "test_generation": "risk_analysis",
    }[stage]
    for unit_id in progress.analysis_units:
        previous_path = analysis_task_path(state, unit_id, previous_stage)
        path = analysis_task_path(state, unit_id, stage)
        task = load_worker_task(previous_path)
        payload = task.model_dump(mode="json")
        payload.update({
            "stage": stage,
            "inventory_path": str(run_dir / "inputs" / "inventory.json"),
            "source_manifest_path": str(run_dir / "inputs" / "source-manifest.json"),
            "allowed_material_paths": _allowed_material_paths(state.get("source_manifest", {})),
            "coverage_context": _coverage_context(task.unit, state.get("coverage_report", {})),
        })
        task = WorkerTask.model_validate(payload)
        write_json(path, task.model_dump(mode="json"))
        session = progress.agent_sessions[f"analysis:{unit_id}"]
        session.stage = stage
        session.status = "pending"
    progress.phase = _ANALYSIS_PHASES[stage]
    save_progress(state, progress)
    return _waiting(
        state,
        progress,
        _analysis_actions(state, progress, stage, list(progress.analysis_units)),
    )


def accept_source_checkpoint(state: PangeaState) -> PangeaState:
    return _accept_analysis_stage(state, "source_checkpoint", "prepare_risk_analysis")


def prepare_risk_analysis(state: PangeaState) -> PangeaState:
    return _prepare_analysis_stage(state, "risk_analysis")


def accept_risk_analysis(state: PangeaState) -> PangeaState:
    return _accept_analysis_stage(state, "risk_analysis", "prepare_test_generation")


def prepare_test_generation(state: PangeaState) -> PangeaState:
    return _prepare_analysis_stage(state, "test_generation")


def accept_test_generation(state: PangeaState) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != "WAITING_TEST_GENERATION":
        raise ArtifactRejected("当前 Graph 不在 test_generation 阶段")
    unsubmitted = [
        unit_id for unit_id in progress.analysis_units
        if not _session_completed(progress, f"analysis:{unit_id}")
    ]
    if unsubmitted:
        _write_analysis_validation_feedback(state, progress, unsubmitted)
        save_progress(state, progress)
        return _waiting(
            state,
            progress,
            _analysis_actions(state, progress, "test_generation", unsubmitted),
        )
    results = _load_analysis_results(state, progress)
    save_progress(state, progress)
    if results is None:
        pending = [
            unit_id for unit_id in progress.analysis_units
            if unit_id not in progress.completed_analysis_units
        ]
        return _waiting(
            state,
            progress,
            _analysis_actions(state, progress, "test_generation", pending),
        )
    independent_task_path = review_task_path(state, "independent")
    independent_result_path = review_result_path(state, "independent")
    if (
        independent_task_path.is_file()
        and independent_result_path.is_file()
        and _session_completed(progress, "review")
    ):
        try:
            independent_task = load_review_task(independent_task_path)
            independent_result = load_independent_review_result(
                independent_result_path,
                independent_task,
            )
            validate_independent_review_result(
                independent_task,
                independent_result,
                _expected_independent_checks(state, independent_task),
            )
        except Exception:
            pass
        else:
            progress.phase = "WAITING_INDEPENDENT_REVIEW"
            save_progress(state, progress)
            return {
                **state,
                "phase": progress.phase,
                "reviewer_id": independent_result.reviewer_id,
                "next_node": "prepare_comparison_review",
            }
    return {**state, "phase": progress.phase, "next_node": "prepare_independent_review"}


def prepare_independent_review(state: PangeaState) -> PangeaState:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("progress.json 不存在")
    if progress.phase != "WAITING_TEST_GENERATION":
        raise ArtifactRejected(f"不能从 {progress.phase} 进入 independent_review")
    _prepare_review(state, progress)
    save_progress(state, progress)
    actions = _review_actions(
        state,
        progress,
        "independent_review",
        review_task_path(state, "independent"),
    )
    return _waiting(state, progress, actions)


def accept_independent_review(state: PangeaState) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != "WAITING_INDEPENDENT_REVIEW":
        raise ArtifactRejected("当前 Graph 不在 independent_review 阶段")
    terminated = _terminate_if_requested(state, progress)
    if terminated is not None:
        return {**terminated, "next_node": "finalize_report"}
    task_path = review_task_path(state, "independent")
    result_path = review_result_path(state, "independent")
    if not result_path.exists():
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "independent_review", task_path),
        )
    if not _session_completed(progress, "review"):
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "independent_review", task_path),
        )
    try:
        task = load_review_task(task_path)
        result_path = normalize_review_result_path(task_path, task)
        result = load_independent_review_result(result_path, task)
        validate_independent_review_result(
            task,
            result,
            _expected_independent_checks(state, task),
        )
        write_json(result_path, result.model_dump(mode="json"))
    except Exception as exc:
        _record_error(progress, "independent_review_rejected", result_path, exc)
        _mark_session_pending(progress, "review")
        save_progress(state, progress)
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "independent_review", task_path),
        )
    _clear_error(progress, "independent_review_rejected", result_path)
    save_progress(state, progress)
    return {
        **state,
        "phase": progress.phase,
        "reviewer_id": result.reviewer_id,
        "next_node": "prepare_comparison_review",
    }


def prepare_comparison_review(state: PangeaState) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != "WAITING_INDEPENDENT_REVIEW":
        raise ArtifactRejected(f"不能从 {progress.phase} 进入 comparison_review")
    independent_task = load_review_task(review_task_path(state, "independent"))
    independent_result = load_independent_review_result(
        review_result_path(state, "independent"),
        independent_task,
    )
    results = _load_analysis_results(state, progress)
    if results is None:
        progress.phase = "WAITING_TEST_GENERATION"
        pending = [
            unit_id for unit_id in progress.analysis_units
            if unit_id not in progress.completed_analysis_units
        ]
        _write_analysis_validation_feedback(state, progress, pending)
        save_progress(state, progress)
        return _waiting(
            state,
            progress,
            _analysis_actions(state, progress, "test_generation", pending),
        )
    _prepare_review_comparison(
        state,
        progress,
        independent_task,
        independent_result.reviewer_id,
        results,
    )
    save_progress(state, progress)
    actions = _review_actions(
        state,
        progress,
        "comparison_review",
        review_task_path(state),
    )
    return _waiting(state, progress, actions)


def accept_comparison_review(state: PangeaState) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != "WAITING_COMPARISON_REVIEW":
        raise ArtifactRejected("当前 Graph 不在 comparison_review 阶段")
    terminated = _terminate_if_requested(state, progress)
    if terminated is not None:
        return {**terminated, "next_node": "finalize_report"}
    task_path = review_task_path(state)
    result_path = review_result_path(state)
    if not result_path.exists():
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "comparison_review", task_path),
        )
    if not _session_completed(progress, "review"):
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "comparison_review", task_path),
        )
    try:
        task = load_review_task(task_path)
        result_path = normalize_review_result_path(task_path, task)
        _validate_review_inputs(state, task)
        independent_result = _load_bound_independent_review(state, task)
        result = load_review_result(result_path, task)
        validate_review_result(task, result, set(progress.analysis_units), independent_result)
        write_json(result_path, result.model_dump(mode="json"))
    except Exception as exc:
        _record_error(progress, "review_result_rejected", result_path, exc)
        _mark_session_pending(progress, "review")
        save_progress(state, progress)
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "comparison_review", task_path),
        )
    _clear_error(progress, "review_result_rejected", result_path)
    save_progress(state, progress)
    if result.status == "REWORK":
        return {**state, "phase": progress.phase, "next_node": "prepare_rework"}
    _complete_session(progress, "review")
    results = _load_analysis_results(state, progress) or []
    unresolved = [issue.model_dump(mode="json") for issue in result.issues]
    ready = _ready_to_finalize(state, progress, results, result.status, unresolved)
    return {**ready, "next_node": "finalize_report"}


def prepare_rework(state: PangeaState) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != "WAITING_COMPARISON_REVIEW":
        raise ArtifactRejected(f"不能从 {progress.phase} 进入 rework")
    review = load_review_result(review_result_path(state), load_review_task(review_task_path(state)))
    _prepare_rework(state, progress, review)
    save_progress(state, progress)
    unit_ids = [issue.unit_id for issue in review.issues]
    unit_ids = list(dict.fromkeys(unit_ids))
    return _waiting(state, progress, _rework_actions(state, progress, unit_ids))


def accept_rework(state: PangeaState) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != "WAITING_REWORK":
        raise ArtifactRejected("当前 Graph 不在 rework 阶段")
    terminated = _terminate_if_requested(state, progress)
    if terminated is not None:
        return {**terminated, "next_node": "finalize_report"}
    unsubmitted: list[str] = []
    for unit_id in progress.analysis_units:
        task_path = rework_task_path(state, unit_id)
        if not task_path.exists():
            continue
        task = load_worker_task(task_path)
        if (
            normalize_worker_result_path(task_path, task).exists()
            and not _session_completed(progress, f"rework:{unit_id}")
        ):
            unsubmitted.append(unit_id)
    if unsubmitted:
        return _waiting(state, progress, _rework_actions(state, progress, unsubmitted))
    results = _load_rework_results(state, progress)
    save_progress(state, progress)
    if results is None:
        rework_units = [
            path.stem
            for path in (run_directory(state) / "agent-tasks" / "rework").glob("*.json")
        ]
        pending = [
            unit_id for unit_id in rework_units
            if unit_id not in progress.completed_rework_units
        ]
        return _waiting(state, progress, _rework_actions(state, progress, pending))
    return {**state, "phase": progress.phase, "next_node": "prepare_rework_verification"}


def prepare_rework_verification(state: PangeaState) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != "WAITING_REWORK":
        raise ArtifactRejected(f"不能从 {progress.phase} 进入 rework_verification")
    results = _load_rework_results(state, progress)
    if results is None:
        raise ArtifactRejected("生成 rework_verification task 前返工结果不完整")
    _prepare_rework_review(state, progress, results)
    save_progress(state, progress)
    actions = _review_actions(
        state,
        progress,
        "rework_verification",
        review_task_path(state, "rework"),
    )
    return _waiting(state, progress, actions)


def accept_rework_verification(state: PangeaState) -> PangeaState:
    state, progress = _resume_context(state)
    if progress.phase != "WAITING_REWORK_VERIFICATION":
        raise ArtifactRejected("当前 Graph 不在 rework_verification 阶段")
    terminated = _terminate_if_requested(state, progress)
    if terminated is not None:
        return {**terminated, "next_node": "finalize_report"}
    task_path = review_task_path(state, "rework")
    result_path = review_result_path(state, "rework")
    unavailable_path = reviewer_unavailable_path(state)
    if unavailable_path.exists():
        unavailable = load_reviewer_unavailable(unavailable_path)
        task = load_review_task(task_path)
        if unavailable.run_id != state["run_id"] or unavailable.reviewer_id != task.same_reviewer_id:
            raise ArtifactRejected("reviewer unavailable 信号不属于原复核 Agent")
        results = _load_rework_results(state, progress) or []
        ready = _ready_to_finalize(
            state,
            progress,
            results,
            "UNRESOLVED",
            [{"reason": unavailable.reason, "reviewer_id": unavailable.reviewer_id}],
        )
        return {**ready, "next_node": "finalize_report"}
    if not result_path.exists():
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "rework_verification", task_path),
        )
    if not _session_completed(progress, "review"):
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "rework_verification", task_path),
        )
    task = load_review_task(task_path)
    try:
        _validate_review_inputs(state, task)
    except Exception as exc:
        artifact = (
            Path(task.analysis_results[0].result_path)
            if task.analysis_results
            else result_path
        )
        _record_error(progress, "rework_result_rejected", artifact, exc)
        original_results = _load_analysis_results(state, progress) or []
        ready = _ready_to_finalize(
            state,
            progress,
            original_results,
            "UNRESOLVED",
            [{"reason": str(exc), "artifact": str(artifact)}],
        )
        return {**ready, "next_node": "finalize_report"}
    try:
        result_path = normalize_review_result_path(task_path, task)
        result = load_review_result(result_path, task)
        independent_result = _load_bound_independent_review(state, task)
        validate_review_result(
            task,
            result,
            set(progress.analysis_units),
            independent_result,
        )
        if result.reviewer_id != task.same_reviewer_id:
            raise ArtifactRejected("返工复核必须由原 review-worker 完成")
        if result.status == "REWORK":
            raise ArtifactRejected("返工复核不能再次要求返工")
        write_json(result_path, result.model_dump(mode="json"))
    except Exception as exc:
        _record_error(progress, "rework_review_rejected", result_path, exc)
        _mark_session_pending(progress, "review")
        save_progress(state, progress)
        return _waiting(
            state,
            progress,
            _review_actions(state, progress, "rework_verification", task_path),
        )
    _clear_error(progress, "rework_review_rejected", result_path)
    _complete_session(progress, "review")
    results = _load_rework_results(state, progress) or []
    unresolved = [issue.model_dump(mode="json") for issue in result.issues]
    ready = _ready_to_finalize(state, progress, results, result.status, unresolved)
    return {**ready, "next_node": "finalize_report"}
