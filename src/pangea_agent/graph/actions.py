from __future__ import annotations

from pathlib import Path

from pangea_agent.models.run import AgentAction, RunProgress


MAX_PARALLEL_ACTIONS = 8


def agent_action(
    progress: RunProgress,
    *,
    session_key: str,
    role: str,
    stage: str,
    task_path: Path,
    unit_id: str | None = None,
    replacement_allowed: bool = False,
) -> dict:
    session = progress.agent_sessions[session_key]
    if session.status != "pending":
        raise ValueError(
            f"Agent action 只能为 pending 会话生成：{session_key}={session.status}"
        )
    action = AgentAction(
        action="continue_agent" if session.task_id else "dispatch_agent",
        role=role,
        stage=stage,
        session_key=session_key,
        unit_id=unit_id,
        task_path=str(task_path),
        task_id=session.task_id,
        replacement_allowed=replacement_allowed,
    )
    return action.model_dump(mode="json")
