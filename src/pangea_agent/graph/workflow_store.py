from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.models.analysis import ActionState, WorkflowProgress


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)


def run_directory(state: dict) -> Path:
    return Path(state["data_root"]) / "runs" / state["run_id"]


def progress_path(state: dict) -> Path:
    return run_directory(state) / "progress.json"


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


def closure_task_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-tasks" / "closure" / f"{unit_id}.json"


def closure_result_path(state: dict, unit_id: str) -> Path:
    return run_directory(state) / "agent-results" / "closure" / f"{unit_id}.json"


def validated_result_path(state: dict, action_id: str) -> Path:
    """Return the workflow-owned copy of one validated Agent submission."""
    filename = action_id.replace(":", "__") + ".json"
    return run_directory(state) / "validated-results" / filename


def pending_actions(progress: WorkflowProgress, limit: int = 8) -> list[dict]:
    active = sum(
        action.status == "dispatched" for action in progress.actions.values()
    )
    available = max(0, limit - active)
    return [
        action.model_dump(mode="json")
        for action in progress.actions.values()
        if action.status == "pending"
    ][:available]


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
