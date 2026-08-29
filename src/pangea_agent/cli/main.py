from __future__ import annotations

import argparse

from .adapter_api import (
    bind_action,
    bind_asset_action,
    next_actions,
    settle_action,
    settle_asset_action,
    validate_action,
    validate_asset_action,
)
from .init_data import init_data
from .json_api import print_error, print_success
from .public_api import (
    archive_asset,
    asset_detail,
    import_asset,
    import_methodology_candidates,
    list_assets,
    list_methodologies,
    list_runs,
    prepare_asset_extraction,
    review_asset,
    run_detail,
    run_report,
    set_methodology_status,
    show_methodology,
    system_capabilities,
    stop_run,
    update_asset_result,
)
from .result_check import check_result_json
from .run_module_analysis import resume_module_analysis, run_module_analysis


def main() -> None:
    parser = argparse.ArgumentParser(prog="pangea")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-data")
    result_check = sub.add_parser("check-result-json")
    result_check.add_argument("--task", required=True)
    run = sub.add_parser("module-analysis")
    run.add_argument("--contract", required=True)
    resume = sub.add_parser("resume-run")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--data-root", default="pangea-data")
    assets = sub.add_parser("assets")
    asset_commands = assets.add_subparsers(dest="asset_command", required=True)
    asset_import = asset_commands.add_parser("import")
    asset_import.add_argument("--data-root", default="pangea-data")
    asset_import.add_argument("--path", required=True)
    asset_import.add_argument(
        "--type",
        required=True,
        choices=("requirement", "design", "historical_defect", "reference", "coverage"),
    )
    asset_import.add_argument("--title")
    asset_list = asset_commands.add_parser("list")
    asset_list.add_argument("--data-root", default="pangea-data")
    asset_list.add_argument("--cursor", type=int, default=0)
    asset_list.add_argument("--limit", type=int, default=50)
    asset_list.add_argument("--type")
    asset_list.add_argument("--status")
    asset_list.add_argument("--query")
    asset_get = asset_commands.add_parser("get")
    asset_get.add_argument("--data-root", default="pangea-data")
    asset_get.add_argument("--asset-id", required=True)
    asset_extract = asset_commands.add_parser("extract")
    asset_extract.add_argument("--data-root", default="pangea-data")
    asset_extract.add_argument("--asset-id", required=True)
    asset_review = asset_commands.add_parser("review")
    asset_review.add_argument("--data-root", default="pangea-data")
    asset_review.add_argument("--asset-id", required=True)
    asset_review.add_argument("--decision", required=True, choices=("approve", "reject"))
    asset_update = asset_commands.add_parser("update-result")
    asset_update.add_argument("--data-root", default="pangea-data")
    asset_update.add_argument("--asset-id", required=True)
    asset_update.add_argument("--result", required=True)
    asset_archive = asset_commands.add_parser("archive")
    asset_archive.add_argument("--data-root", default="pangea-data")
    asset_archive.add_argument("--asset-id", required=True)

    methodologies = sub.add_parser("methodologies")
    methodology_commands = methodologies.add_subparsers(
        dest="methodology_command",
        required=True,
    )
    methodology_import = methodology_commands.add_parser("import")
    methodology_import.add_argument("--data-root", default="pangea-data")
    methodology_import.add_argument("--input", required=True)
    methodology_list = methodology_commands.add_parser("list")
    methodology_list.add_argument("--data-root", default="pangea-data")
    methodology_list.add_argument("--cursor", type=int, default=0)
    methodology_list.add_argument("--limit", type=int, default=50)
    methodology_list.add_argument(
        "--status",
        choices=("candidate", "enabled", "disabled"),
    )
    methodology_list.add_argument("--query")
    methodology_get = methodology_commands.add_parser("get")
    methodology_get.add_argument("--data-root", default="pangea-data")
    methodology_get.add_argument("--id", required=True)
    methodology_enable = methodology_commands.add_parser("enable")
    methodology_enable.add_argument("--data-root", default="pangea-data")
    methodology_enable.add_argument("--id", required=True)
    methodology_disable = methodology_commands.add_parser("disable")
    methodology_disable.add_argument("--data-root", default="pangea-data")
    methodology_disable.add_argument("--id", required=True)

    runs = sub.add_parser("runs")
    run_commands = runs.add_subparsers(dest="run_command", required=True)
    run_list = run_commands.add_parser("list")
    run_list.add_argument("--data-root", default="pangea-data")
    run_list.add_argument("--cursor", type=int, default=0)
    run_list.add_argument("--limit", type=int, default=50)
    run_get = run_commands.add_parser("get")
    run_get.add_argument("--data-root", default="pangea-data")
    run_get.add_argument("--run-id", required=True)
    report = run_commands.add_parser("report")
    report.add_argument("--data-root", default="pangea-data")
    report.add_argument("--run-id", required=True)
    report.add_argument("--format", required=True, choices=("html", "markdown"))
    run_create = run_commands.add_parser("create")
    run_create.add_argument("--contract", required=True)
    run_stop = run_commands.add_parser("stop")
    run_stop.add_argument("--data-root", default="pangea-data")
    run_stop.add_argument("--run-id", required=True)

    system = sub.add_parser("system")
    system_commands = system.add_subparsers(dest="system_command", required=True)
    capabilities = system_commands.add_parser("capabilities")
    capabilities.add_argument("--data-root", default="pangea-data")

    adapter = sub.add_parser("adapter")
    adapter_commands = adapter.add_subparsers(dest="adapter_command", required=True)
    adapter_next = adapter_commands.add_parser("next")
    adapter_next.add_argument("--data-root", default="pangea-data")
    adapter_next.add_argument("--run-id", required=True)
    adapter_next.add_argument("--limit", type=int, default=8)
    adapter_bind = adapter_commands.add_parser("bind")
    adapter_bind.add_argument("--data-root", default="pangea-data")
    adapter_bind_target = adapter_bind.add_mutually_exclusive_group(required=True)
    adapter_bind_target.add_argument("--run-id")
    adapter_bind_target.add_argument("--asset-id")
    adapter_bind.add_argument("--action-id", required=True)
    adapter_bind.add_argument("--task-id", required=True)
    adapter_validate = adapter_commands.add_parser("validate")
    adapter_validate.add_argument("--data-root", default="pangea-data")
    adapter_validate_target = adapter_validate.add_mutually_exclusive_group(required=True)
    adapter_validate_target.add_argument("--run-id")
    adapter_validate_target.add_argument("--asset-id")
    adapter_validate.add_argument("--action-id", required=True)
    adapter_settle = adapter_commands.add_parser("settle")
    adapter_settle.add_argument("--data-root", default="pangea-data")
    adapter_settle_target = adapter_settle.add_mutually_exclusive_group(required=True)
    adapter_settle_target.add_argument("--run-id")
    adapter_settle_target.add_argument("--asset-id")
    adapter_settle.add_argument("--action-id", required=True)
    args = parser.parse_args()

    if args.command == "init-data":
        init_data()
        print_success({"initialized": True, "data_root": "pangea-data"})
    elif args.command == "check-result-json":
        try:
            print_success(check_result_json(args.task))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "module-analysis":
        try:
            print_success(run_module_analysis(args.contract))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "resume-run":
        try:
            print_success(resume_module_analysis(args.run_id, args.data_root))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "assets":
        try:
            if args.asset_command == "import":
                result = import_asset(args.data_root, args.path, args.type, args.title)
                print_success(result.model_dump(mode="json"))
            elif args.asset_command == "list":
                print_success(list_assets(
                    args.data_root,
                    cursor=args.cursor,
                    limit=args.limit,
                    asset_type=args.type,
                    status=args.status,
                    query=args.query,
                ))
            elif args.asset_command == "get":
                print_success(asset_detail(args.data_root, args.asset_id))
            elif args.asset_command == "extract":
                print_success(prepare_asset_extraction(args.data_root, args.asset_id))
            elif args.asset_command == "review":
                result = review_asset(args.data_root, args.asset_id, args.decision)
                print_success(result.model_dump(mode="json"))
            elif args.asset_command == "update-result":
                result = update_asset_result(args.data_root, args.asset_id, args.result)
                print_success(result.model_dump(mode="json"))
            elif args.asset_command == "archive":
                result = archive_asset(args.data_root, args.asset_id)
                print_success(result.model_dump(mode="json"))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "methodologies":
        try:
            if args.methodology_command == "import":
                print_success(import_methodology_candidates(
                    args.data_root,
                    args.input,
                ))
            elif args.methodology_command == "list":
                print_success(list_methodologies(
                    args.data_root,
                    cursor=args.cursor,
                    limit=args.limit,
                    status=args.status,
                    query=args.query,
                ))
            elif args.methodology_command == "get":
                print_success(show_methodology(args.data_root, args.id))
            elif args.methodology_command == "enable":
                print_success(set_methodology_status(
                    args.data_root,
                    args.id,
                    "enabled",
                ))
            elif args.methodology_command == "disable":
                print_success(set_methodology_status(
                    args.data_root,
                    args.id,
                    "disabled",
                ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "runs":
        try:
            if args.run_command == "list":
                print_success(list_runs(args.data_root, cursor=args.cursor, limit=args.limit))
            elif args.run_command == "get":
                print_success(run_detail(args.data_root, args.run_id))
            elif args.run_command == "report":
                print_success(run_report(args.data_root, args.run_id, args.format))
            elif args.run_command == "create":
                print_success(run_module_analysis(args.contract))
            elif args.run_command == "stop":
                print_success(stop_run(args.data_root, args.run_id))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "system":
        try:
            print_success(system_capabilities(args.data_root))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "adapter":
        try:
            if args.adapter_command == "next":
                print_success(next_actions(args.data_root, args.run_id, args.limit))
            elif args.adapter_command == "bind":
                if args.run_id:
                    result = bind_action(
                        args.data_root, args.run_id, args.action_id, args.task_id
                    )
                else:
                    result = bind_asset_action(
                        args.data_root, args.asset_id, args.action_id, args.task_id
                    )
                print_success(result)
            elif args.adapter_command == "validate":
                if args.run_id:
                    result = validate_action(args.data_root, args.run_id, args.action_id)
                else:
                    result = validate_asset_action(
                        args.data_root, args.asset_id, args.action_id
                    )
                print_success(result)
            elif args.adapter_command == "settle":
                if args.run_id:
                    result = settle_action(args.data_root, args.run_id, args.action_id)
                else:
                    result = settle_asset_action(
                        args.data_root, args.asset_id, args.action_id
                    )
                print_success(result)
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
