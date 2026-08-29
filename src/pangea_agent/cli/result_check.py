from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json


def check_result_json(task_path: str) -> dict:
    """Read the task result as JSON without changing workflow state or content."""
    task = read_json(Path(task_path))
    result_path = task.get("result_path")
    if not result_path:
        raise ValueError("task 缺少 result_path")
    read_json(Path(result_path))
    return {
        "status": "PASS",
        "check": "json_syntax_only",
        "result_path": result_path,
        "state_changed": False,
    }
