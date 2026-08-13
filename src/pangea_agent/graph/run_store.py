from __future__ import annotations

import hashlib
from pathlib import Path

from pangea_agent.agent_io import canonical_digest, read_json, write_json
from pangea_agent.models.run import RunProgress
from pangea_agent.models.worker import ReviewerUnavailable, ReviewResult, ReviewTask, TerminationSignal, WorkerResult, WorkerTask


def run_directory(state: dict) -> Path:
    return Path(state["data_root"]) / "runs" / state["run_id"]


def progress_path(state: dict) -> Path:
    return run_directory(state) / "progress.json"


def load_progress(state: dict) -> RunProgress | None:
    path = progress_path(state)
    if not path.exists():
        return None
    return RunProgress.model_validate(read_json(path))


def save_progress(state: dict, progress: RunProgress) -> None:
    write_json(progress_path(state), progress.model_dump(mode="json"))


def analysis_task_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-tasks" / "analysis" / f"{unit_id}.json"


def analysis_result_path(state: dict, unit_id: str, attempt: int) -> Path:
    folder = "analysis" if attempt == 0 else "rework"
    return run_directory(state) / "agent-results" / folder / f"{unit_id}.json"


def rework_task_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-tasks" / "rework" / f"{unit_id}.json"


def review_task_path(state: dict, phase: str = "initial") -> Path:
    name = "review.json" if phase == "initial" else "rework-review.json"
    return run_directory(state) / "agent-tasks" / name


def review_result_path(state: dict, phase: str = "initial") -> Path:
    name = "review.json" if phase == "initial" else "rework-review.json"
    return run_directory(state) / "agent-results" / name


def reviewer_unavailable_path(state: dict) -> Path:
    return run_directory(state) / "agent-results" / "rework-review-unavailable.json"


def termination_path(state: dict) -> Path:
    return run_directory(state) / "agent-results" / "terminate.json"


def final_state_path(state: dict) -> Path:
    return run_directory(state) / "final-state.json"


def load_worker_task(path: Path) -> WorkerTask:
    return WorkerTask.model_validate(read_json(path))


def load_worker_result(path: Path) -> WorkerResult:
    return WorkerResult.model_validate(read_json(path))


def worker_result_skeleton(task: WorkerTask) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": task.run_id,
        "unit_id": task.unit.unit_id,
        "worker_id": "",
        "attempt": task.attempt,
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
    }


def load_review_task(path: Path) -> ReviewTask:
    return ReviewTask.model_validate(read_json(path))


def load_review_result(path: Path) -> ReviewResult:
    return ReviewResult.model_validate(read_json(path))


def load_reviewer_unavailable(path: Path) -> ReviewerUnavailable:
    return ReviewerUnavailable.model_validate(read_json(path))


def load_termination(path: Path) -> TerminationSignal:
    return TerminationSignal.model_validate(read_json(path))


def load_final_state(state: dict) -> dict | None:
    path = final_state_path(state)
    return read_json(path) if path.exists() else None


def save_final_state(state: dict) -> None:
    write_json(final_state_path(state), state)


def artifact_digest(model: object) -> str:
    return canonical_digest(model.model_dump(mode="json"))


def worker_task_digest(task: WorkerTask) -> str:
    # coverage_context is analysis guidance added after the original V1 task format;
    # excluding it keeps existing Run task digests stable across this upgrade.
    payload = task.model_dump(mode="json", exclude={"input_digest", "result_path", "coverage_context"})
    payload["inventory_digest"] = _file_digest(Path(task.inventory_path))
    payload["source_manifest_digest"] = _file_digest(Path(task.source_manifest_path))
    payload["index_digest"] = _file_digest(Path(task.index_path))
    return canonical_digest(payload)


def review_task_digest(task: ReviewTask) -> str:
    payload = task.model_dump(mode="json", exclude={"task_digest", "result_path"})
    payload["inventory_digest"] = _file_digest(Path(task.inventory_path))
    payload["source_manifest_digest"] = _file_digest(Path(task.source_manifest_path))
    return canonical_digest(payload)


def _file_digest(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"任务输入文件不存在：{path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()
