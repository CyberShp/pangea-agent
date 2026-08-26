from __future__ import annotations

import argparse
import json
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.run_store import (
    independent_review_result_skeleton,
    edit_progress,
    load_independent_review_result,
    load_progress,
    load_review_result,
    load_review_task,
    load_worker_result,
    load_worker_task,
    normalize_review_result_path,
    normalize_worker_result_path,
    review_result_skeleton,
    worker_result_skeleton,
)
from pangea_agent.graph.validation import (
    validate_independent_review_result,
    validate_review_result,
    validate_worker_stage_result,
    validation_message,
)
from pangea_agent.index.retriever import read_material

from .init_data import init_data
from .index_repo import print_repositories
from .run_module_analysis import apply_run_event, resume_module_analysis, start_module_analysis


def _print_run_result(result: dict) -> None:
    print(f"run_id={result.get('run_id', 'UNKNOWN')}")
    print(f"data_root={result.get('data_root', 'pangea-data')}")
    print(f"phase={result.get('phase', 'UNKNOWN')}")
    if result.get("report_path"):
        print(result["report_path"])
        if result.get("html_report_path"):
            print(result["html_report_path"])
        return
    for action in result.get("agent_actions", []):
        print(f"action={json.dumps(action, ensure_ascii=False)}")
    for task_path in result.get("agent_task_paths", []):
        print(task_path)


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


def _require_current_task(task_path: Path, task):
    resolved = task_path.resolve()
    agent_tasks = next((parent for parent in resolved.parents if parent.name == "agent-tasks"), None)
    if agent_tasks is None:
        raise ValueError(f"Agent task 不在 Run 的 agent-tasks 目录中：{task_path}")
    run_dir = agent_tasks.parent
    state = {"run_id": run_dir.name, "data_root": str(run_dir.parent.parent)}
    progress = load_progress(state)
    if progress is None or task.run_id != run_dir.name:
        raise ValueError("Agent task 不属于当前 Run")
    if hasattr(task, "task_type"):
        phase = {
            "source_checkpoint": "WAITING_SOURCE_CHECKPOINT",
            "risk_analysis": "WAITING_RISK_ANALYSIS",
            "test_generation": "WAITING_TEST_GENERATION",
            "rework": "WAITING_REWORK",
        }[task.stage]
        folder = "analysis" if task.task_type == "analysis" else "rework"
        name = (
            f"{task.unit.unit_id}-{task.stage}.json"
            if task.task_type == "analysis"
            else f"{task.unit.unit_id}.json"
        )
        expected = agent_tasks / folder / name
    else:
        phase = {
            "independent_review": "WAITING_INDEPENDENT_REVIEW",
            "comparison_review": "WAITING_COMPARISON_REVIEW",
            "rework_verification": "WAITING_REWORK_VERIFICATION",
        }.get(task.stage)
        if phase is None:
            raise ValueError(f"Graph V2 不接受 review stage：{task.stage}")
        name = {
            "independent_review": "review-independent.json",
            "comparison_review": "review.json",
            "rework_verification": "rework-review.json",
        }[task.stage]
        expected = agent_tasks / name
    if resolved != expected.resolve() or progress.phase != phase:
        raise ValueError(
            f"Agent task 不是当前 Graph 阶段任务：task={getattr(task, 'stage', 'unknown')} "
            f"progress={progress.phase}"
        )
    return progress


def _bound_agent_id(progress, task) -> str | None:
    if hasattr(task, "task_type"):
        role = "analysis" if task.task_type == "analysis" else "rework"
        key = f"{role}:{task.unit.unit_id}"
    else:
        key = "review"
    session = progress.agent_sessions.get(key)
    return session.task_id if session is not None else None


def _complete_current_task_session(task_path: Path, task) -> None:
    resolved = task_path.resolve()
    agent_tasks = next(parent for parent in resolved.parents if parent.name == "agent-tasks")
    run_dir = agent_tasks.parent
    state = {"run_id": run_dir.name, "data_root": str(run_dir.parent.parent)}
    if hasattr(task, "task_type"):
        role = "analysis" if task.task_type == "analysis" else "rework"
        key = f"{role}:{task.unit.unit_id}"
    else:
        key = "review"
    with edit_progress(state) as progress:
        session = progress.agent_sessions.get(key)
        if session is None:
            return
        if session.stage != task.stage:
            raise ValueError(
                f"Agent 会话不是当前 task 阶段：session={session.stage} task={task.stage}"
            )
        session.status = "completed"


def _check_review_artifact(task_path: Path) -> None:
    task = load_review_task(task_path)
    progress = _require_current_task(task_path, task)
    result_path = normalize_review_result_path(task_path, task)
    reviewer_id = _bound_agent_id(progress, task)
    if reviewer_id and result_path.exists():
        payload = read_json(result_path)
        if isinstance(payload, dict) and payload.get("reviewer_id") != reviewer_id:
            payload["reviewer_id"] = reviewer_id
            write_json(result_path, payload)

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
    if task.stage in {"comparison_review", "rework_verification"}:
        if not task.independent_result_path:
            raise ValueError("复核缺少 independent_result_path")
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
    resume.add_argument("--settled-task-id")
    prepare = sub.add_parser("prepare-worker-result")
    prepare.add_argument("--task", required=True)
    prepare_review = sub.add_parser("prepare-review-result")
    prepare_review.add_argument("--task", required=True)
    prepare_review.add_argument("--fresh", action="store_true")
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
        _print_run_result(resume_module_analysis(
            args.run_id,
            args.data_root,
            settled_task_id=args.settled_task_id,
        ))
    elif args.command == "prepare-worker-result":
        task_path = Path(args.task)
        task = load_worker_task(task_path)
        progress = _require_current_task(task_path, task)
        result_path = normalize_worker_result_path(task_path, task)
        if not result_path.exists():
            skeleton = worker_result_skeleton(task)
            worker_id = _bound_agent_id(progress, task)
            if worker_id:
                skeleton["worker_id"] = worker_id
            write_json(result_path, skeleton)
        print(result_path)
    elif args.command == "prepare-review-result":
        task_path = Path(args.task)
        task = load_review_task(task_path)
        progress = _require_current_task(task_path, task)
        result_path = normalize_review_result_path(task_path, task)
        if args.fresh or not result_path.exists():
            if task.stage == "independent_review":
                skeleton = independent_review_result_skeleton(task)
            elif task.stage in {"comparison_review", "rework_verification"} and task.independent_result_path:
                independent_result = load_independent_review_result(
                    Path(task.independent_result_path)
                )
                skeleton = review_result_skeleton(task, independent_result)
            else:
                skeleton = review_result_skeleton(task)
            reviewer_id = _bound_agent_id(progress, task)
            if reviewer_id:
                skeleton["reviewer_id"] = reviewer_id
            write_json(result_path, skeleton)
        print(result_path)
    elif args.command == "check-review-artifact":
        try:
            task_path = Path(args.task)
            task = load_review_task(task_path)
            _check_review_artifact(task_path)
            _complete_current_task_session(task_path, task)
        except Exception as exc:
            parser.exit(
                1,
                "FAIL 当前 review result 尚未满足提交契约。"
                f"请由当前 reviewer 修正同一结果文件后重新检查：{validation_message(exc)}\n",
            )
        print("PASS")
    elif args.command == "read-material":
        task_path = Path(args.task)
        payload = read_json(task_path)
        if "task_type" in payload:
            task = load_worker_task(task_path)
            index_path = Path(task.index_path)
        else:
            task = load_review_task(task_path)
            manifest = read_json(Path(task.source_manifest_path))
            index_path = Path(manifest["index_path"])
        _require_current_task(task_path, task)
        if getattr(task, "stage", None) == "source_checkpoint":
            parser.error("source_checkpoint 阶段不能读取资料正文")
        chunks = read_material(index_path, args.path)
        if not chunks:
            parser.error(f"资料未在当前 Run 索引中找到：{args.path!r}")
        print(json.dumps(chunks, ensure_ascii=False, indent=2))
    elif args.command == "validate-worker-result":
        try:
            task_path = Path(args.task)
            task = load_worker_task(task_path)
            progress = _require_current_task(task_path, task)
            result_path = normalize_worker_result_path(task_path, task)
            worker_id = _bound_agent_id(progress, task)
            if worker_id and result_path.exists():
                payload = read_json(result_path)
                if isinstance(payload, dict) and payload.get("worker_id") != worker_id:
                    payload["worker_id"] = worker_id
                    write_json(result_path, payload)
            result = load_worker_result(result_path, task)
            validate_worker_stage_result(task, result, task.stage)
            write_json(result_path, result.model_dump(mode="json"))
            _complete_current_task_session(task_path, task)
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
        try:
            result = apply_run_event(args.run_id, args.data_root, {
                "type": "record_agent_session",
                "role": args.role,
                "unit_id": args.unit_id,
                "task_id": args.task_id,
                "status": "dispatched",
            })
        except ValueError as exc:
            parser.error(str(exc))
        print(result["event_result"])
    elif args.command == "mark-reviewer-unavailable":
        try:
            result = apply_run_event(args.run_id, args.data_root, {
                "type": "reviewer_unavailable",
                "reviewer_id": args.reviewer_id,
                "reason": args.reason,
            })
        except ValueError as exc:
            parser.error(str(exc))
        print(result["event_result"])


if __name__ == "__main__":
    main()
