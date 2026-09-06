from __future__ import annotations

import argparse

from .adapter_api import (
    bind_action,
    bind_asset_action,
    defer_action,
    next_actions,
    retry_attention_action,
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
    complete_methodology_derivation,
    import_asset,
    import_methodology_candidates,
    list_assets,
    list_methodology_derivations,
    list_methodologies,
    list_runs,
    prepare_asset_extraction,
    prepare_methodology_derivation,
    review_asset,
    run_detail,
    run_report,
    set_methodology_status,
    show_methodology_derivation,
    show_methodology,
    system_capabilities,
    stop_run,
    update_asset_result,
)
from .result_check import check_result_json
from .run_module_analysis import resume_module_analysis, run_module_analysis
from .source_first_api import (
    comparison_finding_write,
    comparison_read,
    input_read,
    parse_json_argument,
    plan_write,
    result_read,
    result_repair,
    result_supersede,
    result_write,
    review_decide,
    source_index,
    source_read,
    source_search,
    task_open,
    work_finish,
)


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
    adapter_retry = adapter_commands.add_parser("retry")
    adapter_retry.add_argument("--data-root", default="pangea-data")
    adapter_retry.add_argument("--run-id", required=True)
    adapter_retry.add_argument("--action-id", required=True)
    adapter_defer = adapter_commands.add_parser("defer")
    adapter_defer.add_argument("--data-root", default="pangea-data")
    adapter_defer.add_argument("--run-id", required=True)
    adapter_defer.add_argument("--action-id", required=True)
    adapter_defer.add_argument("--task-id", required=True)
    adapter_defer.add_argument("--reason-code", required=True, choices=[
        "worker_error", "result_incomplete", "finalization_incomplete",
    ])
    adapter_defer.add_argument("--reason", required=True)
    adapter_defer.add_argument("--no-progress", action="store_true")
    adapter_defer.add_argument("--finalization-base-record-count", type=int)

    # Source-first worker tools.  The host must supply the exact binding that
    # Graph returned; these commands never locate another Run or task.
    task_open_cmd = sub.add_parser("task-open")
    task_open_cmd.add_argument("--data-root", default="pangea-data")
    task_open_cmd.add_argument("--run-id", required=True)
    task_open_cmd.add_argument("--action-id", required=True)
    task_open_cmd.add_argument("--task-id", required=True)

    input_read_cmd = sub.add_parser("input-read")
    input_read_cmd.add_argument("--data-root", default="pangea-data")
    input_read_cmd.add_argument("--run-id", required=True)
    input_read_cmd.add_argument("--action-id", required=True)
    input_read_cmd.add_argument("--task-id", required=True)
    input_read_cmd.add_argument("--input-id", required=True)
    input_read_cmd.add_argument("--cursor")
    input_read_cmd.add_argument("--max-chars", type=int, default=12000)

    source_index_cmd = sub.add_parser("source-index")
    source_index_cmd.add_argument("--data-root", default="pangea-data")
    source_index_cmd.add_argument("--run-id", required=True)
    source_index_cmd.add_argument("--action-id", required=True)
    source_index_cmd.add_argument("--task-id", required=True)
    source_index_cmd.add_argument("--repo-id")
    source_index_cmd.add_argument("--path")
    source_index_cmd.add_argument("--cursor")
    source_index_cmd.add_argument("--page-size", type=int, default=50)
    source_index_cmd.add_argument("--view", choices=["legacy", "compact"], default="legacy")
    source_index_cmd.add_argument("--page-token")
    source_index_cmd.add_argument("--max-chars", type=int, default=12000)

    source_read_cmd = sub.add_parser("source-read")
    source_read_cmd.add_argument("--data-root", default="pangea-data")
    source_read_cmd.add_argument("--run-id", required=True)
    source_read_cmd.add_argument("--action-id", required=True)
    source_read_cmd.add_argument("--task-id", required=True)
    source_read_cmd.add_argument("--repo-id", required=True)
    source_read_cmd.add_argument("--path")
    source_read_cmd.add_argument("--region-id")
    source_read_cmd.add_argument("--line-start", type=int)
    source_read_cmd.add_argument("--line-end", type=int)
    source_read_cmd.add_argument("--cursor")
    source_read_cmd.add_argument("--max-lines", type=int, default=400)
    source_read_cmd.add_argument("--view", choices=["legacy", "compact"], default="legacy")
    source_read_cmd.add_argument("--page-token")
    source_read_cmd.add_argument("--max-chars", type=int, default=12000)

    source_search_cmd = sub.add_parser("source-search")
    source_search_cmd.add_argument("--data-root", default="pangea-data")
    source_search_cmd.add_argument("--run-id", required=True)
    source_search_cmd.add_argument("--action-id", required=True)
    source_search_cmd.add_argument("--task-id", required=True)
    source_search_cmd.add_argument("--query", required=True)
    source_search_cmd.add_argument("--repo-id")
    source_search_cmd.add_argument("--path")
    source_search_cmd.add_argument("--cursor")
    source_search_cmd.add_argument("--page-size", type=int, default=100)
    source_search_cmd.add_argument("--view", choices=["legacy", "compact"], default="legacy")
    source_search_cmd.add_argument("--page-token")
    source_search_cmd.add_argument("--max-chars", type=int, default=12000)

    result_write_cmd = sub.add_parser("result-write")
    result_write_cmd.add_argument("--data-root", default="pangea-data")
    result_write_cmd.add_argument("--run-id", required=True)
    result_write_cmd.add_argument("--action-id", required=True)
    result_write_cmd.add_argument("--task-id", required=True)
    result_write_cmd.add_argument("--expected-revision", type=int, required=True)
    result_write_cmd.add_argument("--records", required=True, help="JSON array")
    result_write_cmd.add_argument("--request-id")

    result_supersede_cmd = sub.add_parser("result-supersede")
    result_supersede_cmd.add_argument("--data-root", default="pangea-data")
    result_supersede_cmd.add_argument("--run-id", required=True)
    result_supersede_cmd.add_argument("--action-id", required=True)
    result_supersede_cmd.add_argument("--task-id", required=True)
    result_supersede_cmd.add_argument("--expected-revision", type=int, required=True)
    result_supersede_cmd.add_argument("--target-record-ids", required=True, help="JSON array")
    result_supersede_cmd.add_argument("--replacement", required=True, help="JSON object")
    result_supersede_cmd.add_argument("--request-id")

    comparison_finding_cmd = sub.add_parser("comparison-finding-write")
    comparison_finding_cmd.add_argument("--data-root", default="pangea-data")
    comparison_finding_cmd.add_argument("--run-id", required=True)
    comparison_finding_cmd.add_argument("--action-id", required=True)
    comparison_finding_cmd.add_argument("--task-id", required=True)
    comparison_finding_cmd.add_argument("--expected-revision", type=int, required=True)
    comparison_finding_cmd.add_argument("--unit-ids", required=True, help="JSON array")
    comparison_finding_cmd.add_argument("--finding", required=True, help="JSON object")
    comparison_finding_cmd.add_argument("--replace-finding-record-ids", help="JSON array")
    comparison_finding_cmd.add_argument("--request-id")

    result_read_cmd = sub.add_parser("result-read")
    result_read_cmd.add_argument("--data-root", default="pangea-data")
    result_read_cmd.add_argument("--run-id", required=True)
    result_read_cmd.add_argument("--action-id", required=True)
    result_read_cmd.add_argument("--task-id", required=True)
    result_read_cmd.add_argument("--record-id")
    result_read_cmd.add_argument("--cursor", type=int, default=0)
    result_read_cmd.add_argument("--limit", type=int, default=100)
    result_read_cmd.add_argument("--view", choices=["legacy", "compact"], default="legacy")
    result_read_cmd.add_argument("--page-token")
    result_read_cmd.add_argument("--max-chars", type=int, default=12000)
    result_read_cmd.add_argument("--include-history", action="store_true")

    result_repair_cmd = sub.add_parser("result-repair")
    result_repair_cmd.add_argument("--data-root", default="pangea-data")
    result_repair_cmd.add_argument("--run-id", required=True)
    result_repair_cmd.add_argument("--action-id", required=True)
    result_repair_cmd.add_argument("--task-id", required=True)
    result_repair_cmd.add_argument("--expected-sha256", required=True)
    result_repair_cmd.add_argument("--records", required=True, help="JSON array resent by the same Agent")

    comparison_read_cmd = sub.add_parser("comparison-read")
    comparison_read_cmd.add_argument("--data-root", default="pangea-data")
    comparison_read_cmd.add_argument("--run-id", required=True)
    comparison_read_cmd.add_argument("--action-id", required=True)
    comparison_read_cmd.add_argument("--task-id", required=True)
    comparison_read_cmd.add_argument("--version-set-id", required=True)
    comparison_read_cmd.add_argument("--unit-id")
    comparison_read_cmd.add_argument("--cursor", type=int, default=0)
    comparison_read_cmd.add_argument("--limit", type=int, default=100)
    comparison_read_cmd.add_argument("--view", choices=["legacy", "compact"], default="legacy")
    comparison_read_cmd.add_argument("--page-token")
    comparison_read_cmd.add_argument("--max-chars", type=int, default=12000)
    comparison_read_cmd.add_argument("--include-history", action="store_true")

    finish_cmd = sub.add_parser("work-finish")
    finish_cmd.add_argument("--data-root", default="pangea-data")
    finish_cmd.add_argument("--run-id", required=True)
    finish_cmd.add_argument("--action-id", required=True)
    finish_cmd.add_argument("--task-id", required=True)
    finish_cmd.add_argument("--revision", type=int, required=True)
    finish_cmd.add_argument("--complete", action=argparse.BooleanOptionalAction, default=True)
    finish_cmd.add_argument("--note", default="")
    finish_cmd.add_argument("--request-id")

    plan_write_cmd = sub.add_parser("plan-write")
    plan_write_cmd.add_argument("--data-root", default="pangea-data")
    plan_write_cmd.add_argument("--run-id", required=True)
    plan_write_cmd.add_argument("--action-id", required=True)
    plan_write_cmd.add_argument("--task-id", required=True)
    plan_write_cmd.add_argument("--expected-revision", type=int, required=True)
    plan_write_cmd.add_argument("--unit", required=True, help="JSON object")
    plan_write_cmd.add_argument("--request-id")

    review_decide_cmd = sub.add_parser("review-decide")
    review_decide_cmd.add_argument("--data-root", default="pangea-data")
    review_decide_cmd.add_argument("--run-id", required=True)
    review_decide_cmd.add_argument("--action-id", required=True)
    review_decide_cmd.add_argument("--task-id", required=True)
    review_decide_cmd.add_argument("--expected-revision", type=int, required=True)
    review_decide_cmd.add_argument("--decision", required=True, help="JSON object")
    review_decide_cmd.add_argument("--replace-decision-record-ids", help="JSON array")
    review_decide_cmd.add_argument("--request-id")
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
            elif args.adapter_command == "retry":
                print_success(retry_attention_action(
                    args.data_root, args.run_id, args.action_id
                ))
            elif args.adapter_command == "defer":
                print_success(defer_action(
                    args.data_root, args.run_id, args.action_id, args.task_id,
                    reason_code=args.reason_code, reason=args.reason,
                    no_progress=args.no_progress,
                    finalization_base_record_count=args.finalization_base_record_count,
                ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "task-open":
        try:
            print_success(task_open(args.data_root, args.run_id, args.action_id, args.task_id))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "input-read":
        try:
            print_success(input_read(
                args.data_root, args.run_id, args.action_id, args.task_id,
                input_id=args.input_id, cursor=args.cursor, max_chars=args.max_chars,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "source-index":
        try:
            print_success(source_index(
                args.data_root, args.run_id, args.action_id, args.task_id,
                repo_id=args.repo_id, path=args.path,
                cursor=args.cursor, page_size=args.page_size,
                view=args.view, page_token=args.page_token, max_chars=args.max_chars,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "source-read":
        try:
            print_success(source_read(
                args.data_root, args.run_id, args.action_id, args.task_id,
                repo_id=args.repo_id, path=args.path, region_id=args.region_id,
                line_start=args.line_start, line_end=args.line_end,
                cursor=args.cursor, max_lines=args.max_lines,
                view=args.view, page_token=args.page_token, max_chars=args.max_chars,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "source-search":
        try:
            print_success(source_search(
                args.data_root, args.run_id, args.action_id, args.task_id,
                query=args.query, repo_id=args.repo_id, path=args.path,
                cursor=args.cursor, page_size=args.page_size,
                view=args.view, page_token=args.page_token, max_chars=args.max_chars,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "result-write":
        try:
            records = parse_json_argument(args.records)
            print_success(result_write(
                args.data_root, args.run_id, args.action_id, args.task_id,
                expected_revision=args.expected_revision, records=records,
                request_id=args.request_id,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "result-read":
        try:
            print_success(result_read(
                args.data_root, args.run_id, args.action_id, args.task_id,
                record_id=args.record_id, cursor=args.cursor, limit=args.limit,
                view=args.view, page_token=args.page_token, max_chars=args.max_chars,
                include_history=args.include_history,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "comparison-finding-write":
        try:
            unit_ids = parse_json_argument(args.unit_ids)
            finding = parse_json_argument(args.finding)
            replace_finding_record_ids = (
                parse_json_argument(args.replace_finding_record_ids)
                if args.replace_finding_record_ids
                else None
            )
            print_success(comparison_finding_write(
                args.data_root, args.run_id, args.action_id, args.task_id,
                expected_revision=args.expected_revision,
                unit_ids=unit_ids,
                finding=finding,
                replace_finding_record_ids=replace_finding_record_ids,
                request_id=args.request_id,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "result-supersede":
        try:
            target_record_ids = parse_json_argument(args.target_record_ids)
            replacement = parse_json_argument(args.replacement)
            print_success(result_supersede(
                args.data_root, args.run_id, args.action_id, args.task_id,
                expected_revision=args.expected_revision,
                target_record_ids=target_record_ids,
                replacement=replacement,
                request_id=args.request_id,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "result-repair":
        try:
            records = parse_json_argument(args.records)
            print_success(result_repair(
                args.data_root, args.run_id, args.action_id, args.task_id,
                expected_sha256=args.expected_sha256, records=records,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "comparison-read":
        try:
            print_success(comparison_read(
                args.data_root, args.run_id, args.action_id, args.task_id,
                version_set_id=args.version_set_id, unit_id=args.unit_id,
                cursor=args.cursor, limit=args.limit,
                view=args.view, page_token=args.page_token, max_chars=args.max_chars,
                include_history=args.include_history,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "work-finish":
        try:
            print_success(work_finish(
                args.data_root, args.run_id, args.action_id, args.task_id,
                revision=args.revision, complete=args.complete, note=args.note,
                request_id=args.request_id,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "plan-write":
        try:
            unit = parse_json_argument(args.unit)
            print_success(plan_write(
                args.data_root, args.run_id, args.action_id, args.task_id,
                expected_revision=args.expected_revision, unit=unit,
                request_id=args.request_id,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc
    elif args.command == "review-decide":
        try:
            decision = parse_json_argument(args.decision)
            replace_decision_record_ids = (
                parse_json_argument(args.replace_decision_record_ids)
                if args.replace_decision_record_ids
                else None
            )
            print_success(review_decide(
                args.data_root, args.run_id, args.action_id, args.task_id,
                expected_revision=args.expected_revision, decision=decision,
                replace_decision_record_ids=replace_decision_record_ids,
                request_id=args.request_id,
            ))
        except Exception as exc:
            print_error(exc)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
