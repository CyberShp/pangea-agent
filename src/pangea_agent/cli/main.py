from __future__ import annotations

import argparse

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
    args = parser.parse_args()

    if args.command == "init-data":
        init_data()
    elif args.command == "list-repos":
        print_repositories()
    elif args.command == "module-analysis":
        result = run_module_analysis(args.contract)
        print(result.get("report_path", result))


if __name__ == "__main__":
    main()
