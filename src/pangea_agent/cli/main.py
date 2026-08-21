from __future__ import annotations

import argparse
import json
from pathlib import Path

from pangea_agent.agent_io import write_json
from pangea_agent.graph.run_store import (
    independent_review_result_skeleton,
    load_independent_review_result,
    load_progress,
    load_review_result,
    load_review_task,
    load_worker_result,
    load_worker_task,
    normalize_review_result_path,
    normalize_worker_result_path,
    reviewer_unavailable_path,
    review_result_skeleton,
    save_progress,
    worker_result_skeleton,
)
from pangea_agent.models.worker import ReviewerUnavailable
from pangea_agent.graph.validation import (
    validate_independent_review_result,
    validate_review_result,
    validate_worker_result,
    validation_message,
)
from pangea_agent.index.retriever import read_material

from .init_data import init_data
from .index_repo import print_repositories
from .run_module_analysis import resume_module_analysis, start_module_analysis


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


def _expected_review_checks(task) -> set[tuple[str, str]]:
    expected: set[tuple[str, str]] = set()
    for reference in task.analysis_tasks:
        worker_task = load_worker_task(Path(reference.task_path))
        if worker_task.unit.unit_id != reference.unit_id:
            raise ValueError(f"review task 单元与 analysis task 不一致：{reference.unit_id}")
        expected.update(
            (reference.unit_id, item.check_id)
            for item in worker_task.semantic_check_items
        )
    return expected


def _check_review_artifact(task_path: Path) -> None:
    task = load_review_task(task_path)
    result_path = normalize_review_result_path(task_path, task)

    if task.stage == "independent_review":
        result = load_independent_review_result(result_path, task)
        validate_independent_review_result(task, result, _expected_review_checks(task))
        write_json(result_path, result.model_dump(mode="json"))
        return

    result = load_review_result(result_path, task)
    known_units = {item.unit_id for item in task.analysis_tasks}
    if not known_units:
        known_units = {item.unit_id for item in task.analysis_results}

    independent_result = None
    if task.stage == "comparison_review":
        if not task.independent_result_path:
            raise ValueError("对照复核缺少 independent_result_path")
        independent_task_path = task_path.parent / "review-independent.json"
        independent_task = load_review_task(independent_task_path)
        independent_result = load_independent_review_result(
            Path(task.independent_result_path),
            independent_task,
        )
        validate_independent_review_result(
            independent_task,
            independent_result,
            _expected_review_checks(independent_task),
        )

    validate_review_result(task, result, known_units, independent_result)
    if task.same_reviewer_id and result.reviewer_id != task.same_reviewer_id:
        raise ValueError("reviewer_id 与 task 绑定的原 reviewer 不一致")
    write_json(result_path, result.model_dump(mode="json"))


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
    check_review = sub.add_parser("check-review-artifact")
    check_review.add_argument("--task", required=True)
    material = sub.add_parser("read-material")
    material.add_argument("--task", required=True)
    material.add_argument("--path", required=True)
    validate = sub.add_parser("validate-worker-result")
    validate.add_argument("--task", required=True)
    session = sub.add_parser("record-agent-session")
    session.add_argument("--run-id", required=True)
    session.add_argument("--data-root", default="pangea-data")
    session.add_argument("--role", choices=("analysis", "review", "rework"), required=True)
    session.add_argument("--unit-id")
    session.add_argument("--task-id")
    session.add_argument("--status", choices=("dispatched", "completed"), default="dispatched")
    unavailable = sub.add_parser("mark-reviewer-unavailable")
    unavailable.add_argument("--run-id", required=True)
    unavailable.add_argument("--data-root", default="pangea-data")
    unavailable.add_argument("--reviewer-id", required=True)
    unavailable.add_argument("--reason", required=True)
    args = parser.parse_args()

    if args.command == "init-data":
        init_data()
    elif args.command == "list-repos":
        print_repositories()
    elif args.command == "module-analysis":
        _print_run_result(start_module_analysis(args.contract))
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
            if task.stage == "independent_review":
                skeleton = independent_review_result_skeleton(task)
            elif task.stage == "comparison_review" and task.independent_result_path:
                independent_result = load_independent_review_result(
                    Path(task.independent_result_path)
                )
                skeleton = review_result_skeleton(task, independent_result)
            else:
                skeleton = review_result_skeleton(task)
            write_json(result_path, skeleton)
        _mark_session_started(task_path, "review")
        print(result_path)
    elif args.command == "check-review-artifact":
        try:
            _check_review_artifact(Path(args.task))
        except Exception as exc:
            parser.exit(
                1,
                "FAIL 当前 review result 尚未满足提交契约。"
                f"请由当前 reviewer 修正同一结果文件后重新检查：{validation_message(exc)}\n",
            )
        print("PASS")
    elif args.command == "read-material":
        task = load_worker_task(Path(args.task))
        chunks = read_material(Path(task.index_path), args.path)
        if not chunks:
            parser.error(f"资料未在当前 Run 索引中找到：{args.path!r}")
        print(json.dumps(chunks, ensure_ascii=False, indent=2))
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
            run_dir = Path(args.data_root) / "runs" / args.run_id
            rework_task_path = run_dir / "agent-tasks" / "rework" / f"{args.unit_id}.json"
            analysis_record = progress.agent_sessions.get(f"analysis:{args.unit_id or ''}")
            replacement_allowed = (
                args.role == "rework"
                and progress.phase == "WAITING_REWORK"
                and record.status != "completed"
                and analysis_record is not None
                and record.task_id == analysis_record.task_id
                and rework_task_path.is_file()
                and load_worker_task(rework_task_path).replacement_allowed
            )
            if not replacement_allowed:
                parser.error("同一 Agent 会话不能替换 task_id")
        if args.task_id:
            record.task_id = args.task_id
        record.status = args.status
        save_progress(state, progress)
        print(f"{key}={record.status}")
    elif args.command == "mark-reviewer-unavailable":
        state = {"run_id": args.run_id, "data_root": args.data_root}
        progress = load_progress(state)
        if progress is None:
            parser.error("指定 Run 不存在")
        if progress.phase != "WAITING_REWORK_REVIEW":
            parser.error("仅返工复核阶段可以标记原 reviewer 无法恢复")
        task_path = Path(args.data_root) / "runs" / args.run_id / "agent-tasks" / "rework-review.json"
        if not task_path.is_file():
            parser.error("当前 Run 缺少返工复核 task")
        task = load_review_task(task_path)
        if task.same_reviewer_id != args.reviewer_id:
            parser.error("reviewer-id 不是当前 Run 绑定的原 reviewer")
        signal = ReviewerUnavailable(
            run_id=args.run_id,
            reviewer_id=args.reviewer_id,
            reason=args.reason,
        )
        write_json(reviewer_unavailable_path(state), signal.model_dump(mode="json"))
        print("UNRESOLVED")


if __name__ == "__main__":
    main()
