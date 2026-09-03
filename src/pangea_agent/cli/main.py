from __future__ import annotations

import argparse

from .init_data import init_data
from .json_api import print_error, print_success
from .public_api import (
    archive_asset,
    asset_detail,
    complete_methodology_derivation,
    import_asset,
    import_asset_revision,
    import_methodology_candidates,
    list_assets,
    list_methodology_derivations,
    list_methodologies,
    list_runs,
    prepare_asset_extraction,
    preview_asset_import,
    restore_asset,
    prepare_methodology_derivation,
    review_asset,
    run_detail,
    run_report,
    set_methodology_status,
    show_methodology_derivation,
    show_methodology,
    system_capabilities,
    stop_run,
    update_asset_metadata,
    update_asset_result,
)
from pangea_agent.skill_runs import create_skill_run


def main() -> None:
    parser = argparse.ArgumentParser(prog="pangea")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-data")
    assets = sub.add_parser("assets")
    asset_commands = assets.add_subparsers(dest="asset_command", required=True)
    asset_import = asset_commands.add_parser("import")
    asset_import.add_argument("--data-root", default="pangea-data")
    asset_import.add_argument("--path", required=True)
    asset_import.add_argument(
        "--type",
        required=True,
        choices=("requirement", "design", "historical_defect", "reference", "coverage", "test_case_example"),
    )
    asset_import.add_argument("--title")
    asset_preview = asset_commands.add_parser("preview")
    asset_preview.add_argument("--data-root", default="pangea-data")
    asset_preview.add_argument("--path", required=True)
    asset_preview.add_argument(
        "--type",
        required=True,
        choices=("requirement", "design", "historical_defect", "reference", "coverage", "test_case_example"),
    )
    asset_preview.add_argument("--title")
    asset_revision = asset_commands.add_parser("revise")
    asset_revision.add_argument("--data-root", default="pangea-data")
    asset_revision.add_argument("--asset-id", required=True)
    asset_revision.add_argument("--path", required=True)
    asset_revision.add_argument("--title")
    asset_list = asset_commands.add_parser("list")
    asset_list.add_argument("--data-root", default="pangea-data")
    asset_list.add_argument("--cursor", type=int, default=0)
    asset_list.add_argument("--limit", type=int, default=50)
    asset_list.add_argument("--type")
    asset_list.add_argument("--status")
    asset_list.add_argument("--query")
    asset_list.add_argument("--kind", choices=("semantic", "evidence"))
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
    asset_restore = asset_commands.add_parser("restore")
    asset_restore.add_argument("--data-root", default="pangea-data")
    asset_restore.add_argument("--asset-id", required=True)
    asset_metadata = asset_commands.add_parser("update-metadata")
    asset_metadata.add_argument("--data-root", default="pangea-data")
    asset_metadata.add_argument("--asset-id", required=True)
    asset_metadata.add_argument("--title", required=True)
    asset_metadata.add_argument("--repository-id", action="append")
    asset_metadata.add_argument("--module-tag", action="append")
    asset_metadata.add_argument("--language-tag", action="append")

    methodologies = sub.add_parser("methodologies")
    methodology_commands = methodologies.add_subparsers(
        dest="methodology_command",
        required=True,
    )
    methodology_import = methodology_commands.add_parser("import")
    methodology_import.add_argument("--data-root", default="pangea-data")
    methodology_import.add_argument("--input", required=True)
    methodology_derive = methodology_commands.add_parser("derive")
    methodology_derive.add_argument("--data-root", default="pangea-data")
    methodology_derive.add_argument(
        "--asset-id",
        action="append",
        required=True,
    )
    methodology_complete = methodology_commands.add_parser("complete-derivation")
    methodology_complete.add_argument("--task", required=True)
    methodology_derivations = methodology_commands.add_parser("derivations")
    derivation_commands = methodology_derivations.add_subparsers(
        dest="derivation_command",
        required=True,
    )
    derivation_list = derivation_commands.add_parser("list")
    derivation_list.add_argument("--data-root", default="pangea-data")
    derivation_list.add_argument("--cursor", type=int, default=0)
    derivation_list.add_argument("--limit", type=int, default=50)
    derivation_get = derivation_commands.add_parser("get")
    derivation_get.add_argument("--data-root", default="pangea-data")
    derivation_get.add_argument("--task-id", required=True)
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
    report.add_argument("--format", required=True, choices=("markdown",))
    run_create = run_commands.add_parser("create")
    run_create.add_argument("--request", required=True)
    run_stop = run_commands.add_parser("stop")
    run_stop.add_argument("--data-root", default="pangea-data")
    run_stop.add_argument("--run-id", required=True)

    system = sub.add_parser("system")
    system_commands = system.add_subparsers(dest="system_command", required=True)
    capabilities = system_commands.add_parser("capabilities")
    capabilities.add_argument("--data-root", default="pangea-data")

    args = parser.parse_args()

    if args.command == "init-data":
        init_data()
        print_success({"initialized": True, "data_root": "pangea-data"})
    elif args.command == "assets":
        try:
            if args.asset_command == "import":
                result = import_asset(args.data_root, args.path, args.type, args.title)
                print_success(result.model_dump(mode="json"))
            elif args.asset_command == "preview":
                print_success(preview_asset_import(
                    args.data_root, args.path, args.type, args.title,
                ))
            elif args.asset_command == "revise":
                result = import_asset_revision(args.data_root, args.asset_id, args.path, args.title)
                print_success(result.model_dump(mode="json"))
            elif args.asset_command == "list":
                print_success(list_assets(
                    args.data_root,
                    cursor=args.cursor,
                    limit=args.limit,
                    asset_type=args.type,
                    status=args.status,
                    query=args.query,
                    knowledge_kind=args.kind,
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
            elif args.asset_command == "restore":
                result = restore_asset(args.data_root, args.asset_id)
                print_success(result.model_dump(mode="json"))
            elif args.asset_command == "update-metadata":
                result = update_asset_metadata(
                    args.data_root,
                    args.asset_id,
                    title=args.title,
                    repository_ids=args.repository_id,
                    module_tags=args.module_tag,
                    language_tags=args.language_tag,
                )
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
            elif args.methodology_command == "derive":
                print_success(prepare_methodology_derivation(
                    args.data_root,
                    args.asset_id,
                ))
            elif args.methodology_command == "complete-derivation":
                print_success(complete_methodology_derivation(args.task))
            elif args.methodology_command == "derivations":
                if args.derivation_command == "list":
                    print_success(list_methodology_derivations(
                        args.data_root,
                        cursor=args.cursor,
                        limit=args.limit,
                    ))
                elif args.derivation_command == "get":
                    print_success(show_methodology_derivation(
                        args.data_root,
                        args.task_id,
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
                print_success(create_skill_run(args.request))
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


if __name__ == "__main__":
    main()
