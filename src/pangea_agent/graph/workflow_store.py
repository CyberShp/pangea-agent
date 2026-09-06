from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Callable, Iterator

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.models.analysis import ActionState, WorkflowProgress


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)


def run_directory(state: dict) -> Path:
    return Path(state["data_root"]) / "runs" / state["run_id"]


def progress_path(state: dict) -> Path:
    return run_directory(state) / "progress.json"


@contextmanager
def run_mutation_lock(state: dict) -> Iterator[None]:
    """Serialize one Run's load-modify-save transactions across threads/processes."""

    lock_path = run_directory(state) / ".progress.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path.resolve())
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
    with process_lock:
        handle = lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def serialized_run_mutation(function: Callable) -> Callable:
    @wraps(function)
    def wrapped(data_root: str, run_id: str, *args, **kwargs):
        with run_mutation_lock({"data_root": data_root, "run_id": run_id}):
            return function(data_root, run_id, *args, **kwargs)

    return wrapped


def load_progress(state: dict) -> WorkflowProgress | None:
    path = progress_path(state)
    if not path.is_file():
        return None
    return WorkflowProgress.model_validate(read_json(path))


def save_progress(state: dict, progress: WorkflowProgress) -> None:
    write_json(progress_path(state), progress.model_dump(mode="json"))


def initialize_result(path: Path, payload: dict) -> None:
    """Create a workflow-owned result file without replacing Agent work."""
    if not path.exists():
        write_json(path, payload)


def planning_task_path(state: dict) -> Path:
    return run_directory(state) / "agent-tasks" / "planning.json"


def planning_result_path(state: dict) -> Path:
    return run_directory(state) / "agent-results" / "planning.json"


def source_first_task_path(state: dict, stage: str, key: str | None = None) -> Path:
    """Return a unique task path owned by the source-first Graph."""

    suffix = f"-{key}" if key else ""
    return run_directory(state) / "agent-tasks" / "source-first" / f"{stage}{suffix}.json"


def source_first_result_path(state: dict, stage: str, key: str | None = None) -> Path:
    """Return the single result path for one source-first action."""

    suffix = f"-{key}" if key else ""
    return run_directory(state) / "agent-results" / "source-first" / f"{stage}{suffix}.json"


def source_first_index_path(state: dict) -> Path:
    return run_directory(state) / "inputs" / "source-index.json"


def source_first_version_set_path(state: dict, stage: str = "comparison") -> Path:
    """Return the Graph-owned immutable version set for Reviewer reads."""

    return run_directory(state) / "inputs" / f"source-first-{stage}-version-set.json"


def analysis_task_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-tasks" / "analysis" / f"{unit_id}.json"


def analysis_result_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-results" / "analysis" / f"{unit_id}.json"


def review_task_path(state: dict) -> Path:
    return run_directory(state) / "agent-tasks" / "review.json"


def review_result_path(state: dict) -> Path:
    return run_directory(state) / "agent-results" / "review.json"


def comparison_review_task_path(state: dict) -> Path:
    return run_directory(state) / "agent-tasks" / "comparison-review.json"


def comparison_review_result_path(state: dict) -> Path:
    return run_directory(state) / "agent-results" / "comparison-review.json"


def comparison_review_batch_task_path(state: dict, batch_index: int) -> Path:
    return run_directory(state) / "agent-tasks" / "comparison" / f"batch-{batch_index:04d}.json"


def comparison_review_batch_result_path(state: dict, batch_index: int) -> Path:
    return run_directory(state) / "agent-results" / "comparison" / f"batch-{batch_index:04d}.json"


def comparison_review_aggregate_path(state: dict) -> Path:
    return run_directory(state) / "agent-results" / "comparison-review-aggregate.json"


def closure_task_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-tasks" / "closure" / f"{unit_id}.json"


def closure_result_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-results" / "closure" / f"{unit_id}.json"


def validated_result_path(state: dict, action_id: str) -> Path:
    """Return the workflow-owned copy of one validated Agent submission."""
    filename = action_id.replace(":", "__") + ".json"
    return run_directory(state) / "validated-results" / filename


def validation_report_path(state: dict, action_id: str, attempt: int) -> Path:
    """Return one immutable, Windows-safe Validation Report path."""
    safe_action_id = action_id.replace(":", "__")
    return (
        run_directory(state)
        / "validation"
        / safe_action_id
        / f"attempt-{attempt:04d}.json"
    )


def pending_actions(progress: WorkflowProgress, limit: int = 8) -> list[dict]:
    from pangea_agent.methodology import methodology_manifest

    active = sum(
        action.status == "dispatched" for action in progress.actions.values()
    )
    available = max(0, limit - active)
    pending = []
    for action in progress.actions.values():
        if action.status != "pending":
            continue
        payload = action.model_dump(mode="json")
        if action.role in {"analysis", "closure"}:
            payload["methodologies"] = methodology_manifest(action.task_path)
        pending.append(payload)
    return pending[:available]


def add_action(progress: WorkflowProgress, action: ActionState) -> None:
    if action.action_id in progress.actions:
        raise ValueError(f"action_id 重复：{action.action_id}")
    progress.actions[action.action_id] = action


def current_stage_actions(progress: WorkflowProgress) -> list[ActionState]:
    return [
        action
        for action in progress.actions.values()
        if action.status in {"pending", "dispatched", "settled"}
    ]
