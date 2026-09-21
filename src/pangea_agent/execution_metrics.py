"""Read-only summaries of execution facts already recorded by the host."""

from __future__ import annotations


def summarize_execution(progress: dict) -> dict:
    """Sum worker time, not Run wall time or model inference time.

    The current unfinished turn is excluded: execution_elapsed_ms is updated
    only when a turn ends. Older actions without timing remain unmeasured.
    Counters report recorded events, with per-counter action coverage. Missing
    counts remain None; partial coverage yields only the recorded subtotal.
    These facts do not judge analysis quality.
    """
    counters = (
        "worker_turns", "auto_continuations", "repair_dispatches",
        "validation_failures", "incomplete_attempts",
    )

    def aggregate(actions: list[dict]) -> dict:
        recorded_counts = {
            key: [action[key] for action in actions if action.get(key) is not None]
            for key in counters
        }
        timed = [
            action for action in actions
            if action.get("execution_started_at_ms") is not None
            or action.get("execution_finished_at_ms") is not None
            or action.get("execution_elapsed_ms", 0) > 0
        ]
        return {
            "action_count": len(actions),
            "timed_action_count": len(timed),
            "unfinished_timed_action_count": sum(
                action.get("execution_started_at_ms") is not None
                and action.get("execution_finished_at_ms") is None
                for action in timed
            ),
            "worker_elapsed_ms": sum(
                action.get("execution_elapsed_ms", 0) for action in timed
            ) if timed else None,
            **{
                key: sum(values) if values else None
                for key, values in recorded_counts.items()
            },
            "counter_action_counts": {
                key: len(values) for key, values in recorded_counts.items()
            },
        }

    actions = list(progress.get("actions", {}).values())
    stages: dict[str, list[dict]] = {}
    for action in actions:
        stages.setdefault(action.get("stage") or "unknown", []).append(action)
    return {
        "timing_basis": "recorded_worker_execution",
        **aggregate(actions),
        "stages": [
            {"stage": stage, **aggregate(items)}
            for stage, items in stages.items()
        ],
    }
