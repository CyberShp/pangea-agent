from __future__ import annotations

import argparse
from pathlib import Path

from pangea_agent.graph.run_store import load_worker_result, load_worker_task
from pangea_agent.graph.validation import validate_worker_result, validation_message

from .init_data import init_data
from .index_repo import print_repositories
from .run_module_analysis import run_module_analysis


def main() -> None:
    parser = argparse.ArgumentParser(prog="pangea")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-data")
    sub.add_parser("list-repos")
    run = sub.add_parser("module-analysis")
    run.add_argument("--contract", required=True)
    validate = sub.add_parser("validate-worker-result")
    validate.add_argument("--task", required=True)
    args = parser.parse_args()

    if args.command == "init-data":
        init_data()
    elif args.command == "list-repos":
        print_repositories()
    elif args.command == "module-analysis":
        result = run_module_analysis(args.contract)
        if result.get("report_path"):
            print(result["report_path"])
            if result.get("html_report_path"):
                print(result["html_report_path"])
        else:
            print(f"phase={result.get('phase', 'UNKNOWN')}")
            for task_path in result.get("agent_task_paths", []):
                print(task_path)
    elif args.command == "validate-worker-result":
        try:
            task = load_worker_task(Path(args.task))
            result = load_worker_result(Path(task.result_path))
            validate_worker_result(task, result)
        except Exception as exc:
            parser.exit(1, f"FAIL {validation_message(exc)}\n")
        print("PASS")


if __name__ == "__main__":
    main()
