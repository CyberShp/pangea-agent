from __future__ import annotations

import argparse
from pathlib import Path

from pangea_agent.agent_io import write_json
from pangea_agent.graph.run_store import (
    independent_review_result_skeleton,
    load_progress,
    load_review_task,
    load_worker_result,
    load_worker_task,
    normalize_review_result_path,
    normalize_worker_result_path,
    review_result_skeleton,
    save_progress,
    worker_result_skeleton,
)
from pangea_agent.graph.validation import validate_worker_result, validation_message

from .init_data import init_data
from .index_repo import print_repositories
from .run_module_analysis import resume_module_analysis, run_module_analysis


def _print_run_result(result: dict) -> None:
    if result.get("report_path"):
        print(result["report_path"])
        if result.get("html_report_path"):
            print(result["html_report_path"])
        return
    print(f"run_id={result.get('run_id', 'UNKNOWN')}")
    print(f"phase={result.get('phase', 'UNKNOWN')}")
    for task_path in result.get("agent_task_paths", []):
        print(task_path)


def _mark_session_started(task_path: Path, key: str) -> None:
    agent_tasks = next((parent for parent in task_path.resolve().parents if parent.name == "agent-tasks"), None)
    if agent_tasks is None:
        raise ValueError(f"Agent task 不在 Run 的 agent-tasks 目录中：{task_path}")
    run_dir = agent_tasks.parent
    state = {"run_id": run_dir.name, "data_root": str(run_dir.parent.parent)}
    progress = load_progress(state)
    if progress is None or key not in progress.agent_sessions:
        raise ValueError(f"progress.json 中没有待启动会话：{key}")
    progress.agent_sessions[key].status = "dispatched"
    save_progress(state, progress)


def main() -> None:
    parser = argparse.ArgumentParser(prog="pangea")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-data")
    sub.add_parser("list-repos")
    run = sub.add_parser("module-analysis")
    run.add_argument("--contract", required=True)
    resume = sub.add_parser("resume-run")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--data-root", default="pangea-data")
    prepare = sub.add_parser("prepare-worker-result")
    prepare.add_argument("--task", required=True)
    prepare_review = sub.add_parser("prepare-review-result")
    prepare_review.add_argument("--task", required=True)
    validate = sub.add_parser("validate-worker-result")
    validate.add_argument("--task", required=True)
    session = sub.add_parser("record-agent-session")
    session.add_argument("--run-id", required=True)
    session.add_argument("--data-root", default="pangea-data")
    session.add_argument("--role", choices=("analysis", "review", "rework"), required=True)
    session.add_argument("--unit-id")
    session.add_argument("--task-id")
    session.add_argument("--status", choices=("dispatched", "completed"), default="dispatched")
    args = parser.parse_args()

    if args.command == "init-data":
        init_data()
    elif args.command == "list-repos":
        print_repositories()
    elif args.command == "module-analysis":
        _print_run_result(run_module_analysis(args.contract))
    elif args.command == "resume-run":
        _print_run_result(resume_module_analysis(args.run_id, args.data_root))
    elif args.command == "prepare-worker-result":
        task_path = Path(args.task)
        task = load_worker_task(task_path)
        result_path = normalize_worker_result_path(task_path, task)
        if not result_path.exists():
            write_json(result_path, worker_result_skeleton(task))
        role = "analysis" if task.task_type == "analysis" else "rework"
        _mark_session_started(task_path, f"{role}:{task.unit.unit_id}")
        print(result_path)
    elif args.command == "prepare-review-result":
        task_path = Path(args.task)
        task = load_review_task(task_path)
        result_path = normalize_review_result_path(task_path, task)
        if not result_path.exists():
            skeleton = (
                independent_review_result_skeleton(task)
                if task.stage == "independent_review"
                else review_result_skeleton(task)
            )
            write_json(result_path, skeleton)
        _mark_session_started(task_path, "review")
        print(result_path)
    elif args.command == "validate-worker-result":
        try:
            task_path = Path(args.task)
            task = load_worker_task(task_path)
            result_path = normalize_worker_result_path(task_path, task)
            result = load_worker_result(result_path, task)
            validate_worker_result(task, result)
            write_json(result_path, result.model_dump(mode="json"))
        except Exception as exc:
            detail = validation_message(exc)
            parser.exit(
                1,
                "FAIL 当前 worker result 尚未满足提交契约。"
                "PANGEA 只会自动恢复 run_id/unit_id/attempt/analyzed_scope/analyzed_context_scope "
                "以及可确定的 evidence 位置；business_flows、visual_findings、risks、test_cases 的结构和实质内容不会自动补写。"
                f"请在当前 Worker 内一次处理下列全部错误后重新执行 validate-worker-result：{detail}\n",
            )
        print("PASS")
    elif args.command == "record-agent-session":
        state = {"run_id": args.run_id, "data_root": args.data_root}
        progress = load_progress(state)
        if progress is None:
            parser.error("指定 Run 不存在")
        key = "review" if args.role == "review" else f"{args.role}:{args.unit_id or ''}"
        record = progress.agent_sessions.get(key)
        if record is None:
            parser.error(f"当前 Run 没有待记录的 Agent 会话：{key}")
        if args.status == "dispatched" and not args.task_id:
            parser.error("记录 dispatched 状态时必须提供 --task-id")
        if record.task_id and args.task_id and record.task_id != args.task_id:
            parser.error("同一 Agent 会话不能替换 task_id")
        if args.task_id:
            record.task_id = args.task_id
        record.status = args.status
        save_progress(state, progress)
        print(f"{key}={record.status}")


if __name__ == "__main__":
    main()
