"""Retire model-review scheduling without rewriting historical semantic output."""
from pathlib import Path

from pangea_agent.agent_io import read_json

RETIRED_STAGES = frozenset({"independent_review", "comparison_review", "targeted_closure"})


def is_model_review(action) -> bool:
    return action.role in {"review", "closure"} or action.stage in RETIRED_STAGES


def retire_model_reviews(state, progress) -> None:
    """Upgrade active workflows only; completed historical reports stay untouched.

    Paused is an existing transport state. review_skipped distinguishes retired
    work from interrupted analysis; no review result is manufactured or accepted.
    Existing closure writes remain deliverable with their original binding.
    """
    from pangea_agent.graph.workflow_store import save_progress, run_directory
    from pangea_agent.graph.result_store import read_result

    if progress.lifecycle_status != "running":
        return
    changed = progress.review_policy != "disabled"
    progress.review_policy = "disabled"
    for action in progress.actions.values():
        if not is_model_review(action) or action.status == "accepted" or action.review_skipped:
            continue
        if action.role == "closure" and progress.workflow_version == "source-first-v1":
            run_dir = run_directory(state).resolve()
            task_path = Path(action.task_path).resolve()
            task_path.relative_to(run_dir)
            if task_path.is_file():
                task = read_json(task_path)
                result_path = Path(task["result_path"]).resolve()
                result_path.relative_to(run_dir)
                if result_path.is_file():
                    result = read_result(result_path)
                    if (Path(result.binding.data_root).resolve() != Path(state["data_root"]).resolve()
                            or result.binding.run_id != state["run_id"]
                            or result.binding.action_id != action.action_id
                            or result.binding.task_id not in {action.task_id, "pending"}):
                        raise ValueError("历史 closure 结果绑定不一致，保留结果等待修复")
                    if result.binding.task_id == action.task_id and result.revision > task.get("base_revision", result.revision):
                        action.delivery_revision = result.revision
        action.review_skipped = True
        action.status = "paused"
        action.attention_required = False
        action.error = "模型审核暂不执行；保留历史产物，不再派发或续接"
        action.pending_repair = None
        action.repair_status = "none"
        changed = True
    if progress.stage in {"reviewing", "closing"}:
        # Do not fabricate completion if an inconsistent old state still has
        # unfinished analysis. Continue those exact actions, never re-plan.
        unfinished = [a for a in progress.actions.values()
                      if a.role == "analysis" and a.status != "accepted"]
        progress.stage = "analyzing" if unfinished else "reporting"
        progress.quality_status = "UNRESOLVED"
        progress.needs_user = False
        progress.blocking_reason = None
        changed = True
    if changed:
        save_progress(state, progress)
