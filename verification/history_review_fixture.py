"""Explicit historical saved-task fixtures, never production review scheduling."""
from pathlib import Path
from pangea_agent.agent_io import write_json
from pangea_agent.graph.result_store import initialize_result, read_result
from pangea_agent.models.analysis import ActionState
from pangea_agent.models.source_first import SourceBinding


def saved_comparison(harness, worker="historical-reviewer"):
    """Load a pre-existing bound review turn for low-level compatibility APIs."""
    progress = harness.progress()
    action_id = f"{harness.run_id}:historical-review"
    task_path = harness.run / "agent-tasks/historical-review.json"
    result_path = harness.run / "agent-results/source-first/historical-review.json"
    version_path = harness.run / "inputs/source-first-comparison-version-set.json"
    entries = []
    for origin in harness.analysis:
        action = progress["actions"][origin]
        task = harness.task(origin)
        entries.append(dict(role="analysis", unit_id=task["unit_id"], action_id=origin,
                            task_id=action["task_id"], result_path=task["result_path"],
                            revision=read_result(task["result_path"]).revision))
    version_id = "vs-historical-fixture"
    write_json(version_path, dict(format_version="pangea-version-set-v1", version_set_id=version_id,
                                 workflow_version="source-first-v1", run_id=harness.run_id, entries=entries))
    task = {**harness.task(harness.analysis[0]), "action_id": action_id,
            "task_type": "source_first_review", "review_stage": "comparison_review",
            "result_path": str(result_path), "version_set_path": str(version_path),
            "version_set_id": version_id}
    write_json(task_path, task)
    initialize_result(result_path, SourceBinding(data_root=str(harness.root), run_id=harness.run_id,
                                                action_id=action_id, task_id=worker))
    progress["actions"][action_id] = ActionState(action_id=action_id, action="dispatch_agent", role="review",
        stage="comparison_review", status="dispatched", task_path=str(task_path), task_id=worker).model_dump(mode="json")
    progress.update(lifecycle_status="running", stage="reviewing", quality_status=None)
    write_json(harness.run / "progress.json", progress)
    return action_id, worker


def saved_closure(harness, origin):
    """Restore a historical bound closure, without invoking a dispatcher."""
    from pangea_agent.graph.result_store import copy_result_for_closure, append_records
    progress = harness.progress()
    original_task = harness.task(origin)
    original = read_result(original_task["result_path"])
    worker = progress["actions"][origin]["task_id"]
    action_id = f"{harness.run_id}:closure:{original_task['unit_id']}"
    task_path = harness.run / "agent-tasks/historical-closure.json"
    result_path = harness.run / "agent-results/source-first/historical-closure.json"
    task = {**original_task, "action_id": action_id, "task_type": "source_first_closure",
            "result_path": str(result_path), "original_result_path": original_task["result_path"],
            "base_revision": original.revision, "current_revision": original.revision}
    write_json(task_path, task)
    binding = SourceBinding(data_root=str(harness.root), run_id=harness.run_id, action_id=action_id, task_id=worker)
    write_json(result_path, copy_result_for_closure(original, binding, result_path).model_dump(mode="json"))
    append_records(result_path, binding, original.revision,
                   [{"kind": "note", "body": "Historical closure saved before suspension"}])
    progress["actions"][action_id] = ActionState(action_id=action_id, action="continue_agent", role="closure",
        stage="targeted_closure", status="paused", task_path=str(task_path), task_id=worker).model_dump(mode="json")
    progress.update(lifecycle_status="running", stage="closing", quality_status="UNRESOLVED")
    write_json(harness.run / "progress.json", progress)
    return action_id, worker
