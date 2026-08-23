from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.models.run import RunProgress
from pangea_agent.models.worker import (
    IndependentReviewResult,
    ReviewerUnavailable,
    ReviewResult,
    ReviewTask,
    TerminationSignal,
    WorkerResult,
    WorkerTask,
)


def run_directory(state: dict) -> Path:
    return Path(state["data_root"]) / "runs" / state["run_id"]


def progress_path(state: dict) -> Path:
    return run_directory(state) / "progress.json"


def load_progress(state: dict) -> RunProgress | None:
    path = progress_path(state)
    if not path.exists():
        return None
    payload = read_json(path)
    if payload.get("workflow_version") != 2:
        raise ValueError("当前 Run 不是 Graph V2 workflow")
    return RunProgress.model_validate(payload)


def save_progress(state: dict, progress: RunProgress) -> None:
    write_json(progress_path(state), progress.model_dump(mode="json"))


@contextmanager
def edit_progress(state: dict) -> Iterator[RunProgress]:
    """Serialize the small progress update made by parallel Agent completions."""

    lock_path = run_directory(state) / ".progress.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            progress = load_progress(state)
            if progress is None:
                raise ValueError("指定 Run 不存在")
            yield progress
            save_progress(state, progress)
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def analysis_task_path(state: dict, unit_id: str, stage: str) -> Path:
    return (
        run_directory(state)
        / "agent-tasks"
        / "analysis"
        / f"{unit_id}-{stage}.json"
    )


def analysis_result_path(state: dict, unit_id: str, attempt: int) -> Path:
    folder = "analysis" if attempt == 0 else "rework"
    return run_directory(state) / "agent-results" / folder / f"{unit_id}.json"


def rework_task_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-tasks" / "rework" / f"{unit_id}.json"


def review_task_path(state: dict, phase: str = "initial") -> Path:
    name = {
        "initial": "review.json",
        "independent": "review-independent.json",
        "rework": "rework-review.json",
    }[phase]
    return run_directory(state) / "agent-tasks" / name


def review_result_path(state: dict, phase: str = "initial") -> Path:
    name = {
        "initial": "review.json",
        "independent": "review-independent.json",
        "rework": "rework-review.json",
    }[phase]
    return run_directory(state) / "agent-results" / name


def reviewer_unavailable_path(state: dict) -> Path:
    return run_directory(state) / "agent-results" / "rework-review-unavailable.json"


def termination_path(state: dict) -> Path:
    return run_directory(state) / "agent-results" / "terminate.json"


def final_state_path(state: dict) -> Path:
    return run_directory(state) / "final-state.json"


def ready_state_path(state: dict) -> Path:
    return run_directory(state) / "ready-state.json"


def load_worker_task(path: Path) -> WorkerTask:
    payload = read_json(path)
    task = WorkerTask.model_validate(payload)
    normalized = task.model_dump(mode="json")
    if normalized != payload:
        write_json(path, normalized)
    return task


def load_worker_result(path: Path, task: WorkerTask | None = None) -> WorkerResult:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"worker 结果必须是 JSON 对象：{path}")
    payload = dict(payload)
    for field in (
        "evidence",
        "business_flows",
        "visual_findings",
        "risks",
        "test_cases",
        "addressed_review_issue_ids",
        "errors",
    ):
        payload.setdefault(field, [])
    payload.setdefault("analysis_checkpoint", worker_result_skeleton(task)["analysis_checkpoint"] if task else {})
    checkpoint = payload.get("analysis_checkpoint")
    if isinstance(checkpoint, dict):
        checkpoint.setdefault("coverage_decisions", [])
    if task is not None:
        payload.update({
            "schema_version": "1.0",
            "run_id": task.run_id,
            "unit_id": task.unit.unit_id,
            "attempt": task.attempt,
            "analyzed_scope": list(task.unit.source_scope),
            "analyzed_context_scope": list(task.unit.context_scope),
        })
    return WorkerResult.model_validate(payload)


def normalize_worker_result_path(task_path: Path, task: WorkerTask) -> Path:
    """Derive the only valid result location from the task file instead of trusting path text."""

    resolved_task = task_path.resolve()
    run_dir = next((parent for parent in resolved_task.parents if parent.name == "agent-tasks"), None)
    if run_dir is None:
        raise ValueError(f"worker task 不在当前 Run 的 agent-tasks 目录中：{task_path}")
    folder = "analysis" if task.task_type == "analysis" else "rework"
    result_path = run_dir.parent / "agent-results" / folder / f"{task.unit.unit_id}.json"
    if task.result_path != str(result_path):
        task.result_path = str(result_path)
        write_json(task_path, task.model_dump(mode="json"))
    return result_path


def worker_result_skeleton(task: WorkerTask) -> dict:
    if task.task_type == "rework":
        if not task.prior_result_path:
            raise ValueError("rework task 缺少 prior_result_path")
        prior_payload = read_json(Path(task.prior_result_path))
        prior_result = WorkerResult.model_validate(prior_payload)
        result = prior_result.model_dump(mode="json")
        result.update({
            "schema_version": "1.0",
            "run_id": task.run_id,
            "unit_id": task.unit.unit_id,
            "worker_id": "",
            "attempt": task.attempt,
            "completed_stage": task.stage,
            "finish_reason": "stop",
            "analyzed_scope": list(task.unit.source_scope),
            "analyzed_context_scope": list(task.unit.context_scope),
            "addressed_review_issue_ids": [],
            "errors": [],
        })
        return result

    return {
        "schema_version": "1.0",
        "run_id": task.run_id,
        "unit_id": task.unit.unit_id,
        "worker_id": "",
        "attempt": task.attempt,
        "completed_stage": task.stage,
        "finish_reason": "stop",
        "summary": "",
        "analyzed_scope": list(task.unit.source_scope),
        "analyzed_context_scope": list(task.unit.context_scope),
        "evidence": [],
        "business_flows": [],
        "visual_findings": [],
        "risks": [],
        "test_cases": [],
        "addressed_review_issue_ids": [],
        "errors": [],
        "analysis_checkpoint": {
            "source_paths_reviewed": list(task.unit.source_scope),
            "lifecycle_stages_checked": [],
            "failure_paths": [],
            "material_decisions": [],
            "coverage_priorities": [],
            "coverage_decisions": [],
            "risk_set_frozen": False,
            "counterexamples_checked": [],
        },
    }


def review_result_skeleton(
    task: ReviewTask,
    independent_result: IndependentReviewResult | None = None,
    prior_comparison_result: ReviewResult | None = None,
) -> dict:
    reviewer_id = ""
    independent_findings: list[dict] = []
    if task.stage in {"comparison_review", "rework_verification"} and independent_result is not None:
        reviewer_id = independent_result.reviewer_id
        independent_findings = [
            {
                **finding.model_dump(mode="json"),
                "linked_worker_risk_ids": [],
                "linked_worker_test_case_ids": [],
            }
            for finding in independent_result.findings
        ]
        if task.stage == "rework_verification" and prior_comparison_result is not None:
            previous = {
                (finding.unit_id, finding.check_id): finding
                for finding in prior_comparison_result.independent_findings
            }
            for finding in independent_findings:
                prior = previous.get((finding["unit_id"], finding["check_id"]))
                if prior is None:
                    continue
                finding.update({
                    "worker_disposition": prior.worker_disposition,
                    "linked_worker_risk_ids": list(prior.linked_worker_risk_ids),
                    "linked_worker_test_case_ids": list(prior.linked_worker_test_case_ids),
                })
    test_case_checks: list[dict] = []
    for result_ref in task.analysis_results:
        worker_result = load_worker_result(Path(result_ref.result_path))
        test_case_checks.extend(
            {
                "unit_id": result_ref.unit_id,
                "test_case_id": case.test_case_id,
                "expected_results": [
                    step.expected_result for step in case.steps
                ],
                "failure_observations": [
                    step.failure_observation for step in case.steps
                ],
                "current_behavior": "待 reviewer 按冻结源码独立填写",
                "verdict": "unresolved",
                "reason": "待 reviewer 对照正确产品通过标准与当前实现行为",
            }
            for case in worker_result.test_cases
        )
    return {
        "schema_version": "1.0",
        "run_id": task.run_id,
        "reviewer_id": reviewer_id,
        "finish_reason": "stop",
        "status": "PASS",
        "summary": "",
        "issues": [],
        "reviewed_units": [item.unit_id for item in task.analysis_results],
        "independent_findings": independent_findings,
        "test_case_checks": test_case_checks,
    }


def independent_review_result_skeleton(task: ReviewTask) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": task.run_id,
        "reviewer_id": "",
        "finish_reason": "stop",
        "summary": "",
        "reviewed_units": [item.unit_id for item in task.analysis_tasks],
        "findings": [],
    }


def load_review_task(path: Path) -> ReviewTask:
    return ReviewTask.model_validate(read_json(path))


def load_review_result(path: Path, task: ReviewTask | None = None) -> ReviewResult:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"review 结果必须是 JSON 对象：{path}")
    payload = dict(payload)
    # Compatibility for review agents that emitted detailed per-unit notes outside
    # the formal ReviewResult contract. Keep every other unknown field strict.
    payload.pop("unit_reviews", None)
    payload.setdefault("issues", [])
    if task is not None:
        payload.update({
            "schema_version": "1.0",
            "run_id": task.run_id,
        })
    return ReviewResult.model_validate(payload)


def load_independent_review_result(
    path: Path, task: ReviewTask | None = None
) -> IndependentReviewResult:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"独立复核结果必须是 JSON 对象：{path}")
    payload = dict(payload)
    payload.setdefault("findings", [])
    if task is not None:
        payload.update({
            "schema_version": "1.0",
            "run_id": task.run_id,
        })
    return IndependentReviewResult.model_validate(payload)


def normalize_review_result_path(task_path: Path, task: ReviewTask) -> Path:
    resolved_task = task_path.resolve()
    if resolved_task.parent.name != "agent-tasks":
        raise ValueError(f"review task 不在当前 Run 的 agent-tasks 目录中：{task_path}")
    result_path = resolved_task.parent.parent / "agent-results" / resolved_task.name
    if task.result_path != str(result_path):
        task.result_path = str(result_path)
        write_json(task_path, task.model_dump(mode="json"))
    return result_path


def load_reviewer_unavailable(path: Path) -> ReviewerUnavailable:
    return ReviewerUnavailable.model_validate(read_json(path))


def load_termination(path: Path) -> TerminationSignal:
    return TerminationSignal.model_validate(read_json(path))


def load_final_state(state: dict) -> dict | None:
    path = final_state_path(state)
    return read_json(path) if path.exists() else None


def save_final_state(state: dict) -> None:
    write_json(final_state_path(state), state)


def load_ready_state(state: dict) -> dict | None:
    path = ready_state_path(state)
    return read_json(path) if path.exists() else None


def save_ready_state(state: dict) -> None:
    write_json(ready_state_path(state), state)
