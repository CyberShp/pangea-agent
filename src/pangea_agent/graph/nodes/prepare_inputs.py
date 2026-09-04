from __future__ import annotations

import shutil
from pathlib import Path
from pathlib import PurePosixPath

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.assets import analysis_asset_inputs
from pangea_agent.documents.coverage import match_coverage_records, relevant_zero_coverage
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.workflow_store import (
    add_action,
    initialize_result,
    planning_result_path,
    planning_task_path,
    project_path,
    run_directory,
    save_progress,
)
from pangea_agent.graph.schema_contract import freeze_run_contracts
from pangea_agent.inventory.scope_expander import expand_analysis_scope
from pangea_agent.inventory.languages import detect_analysis_language
from pangea_agent.inventory.lua_scope_expander import expand_lua_analysis_scope
from pangea_agent.inventory.lua_source_scanner import build_lua_inventory
from pangea_agent.inventory.source_scanner import build_lightweight_inventory
from pangea_agent.methodology import freeze_enabled_methodologies
from pangea_agent.models.analysis import (
    ActionState,
    PlanningTask,
    RepositoryRef,
    WorkflowProgress,
)
from pangea_agent.repositories.resolver import resolve_repositories_from_contract


def _normalize_context_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("context_scope 只能包含非空相对文件路径")
    normalized = value.strip().replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"context_scope 必须是相对路径：{value}")
    parts = PurePosixPath(normalized).parts
    if ".." in parts:
        raise ValueError(f"context_scope 越过仓库边界：{value}")
    return str(PurePosixPath(normalized))


def _explicit_context_files(
    repositories: list[dict], context_scope: list[str], expansion: dict,
) -> list[dict]:
    """Resolve user-frozen context without changing source ownership."""

    if not context_scope:
        return []
    repo_ids = {item["repo_id"] for item in repositories}
    owned = {
        (group["repo_id"], _normalize_context_path(path))
        for group in expansion.get("groups", [])
        for path in group.get("code_paths", [])
    }
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in context_scope:
        repo_hint = None
        candidate = raw
        if isinstance(raw, str) and ":" in raw:
            prefix, suffix = raw.split(":", 1)
            if prefix in repo_ids and suffix:
                repo_hint, candidate = prefix, suffix
        relative = _normalize_context_path(candidate)
        matches = []
        for repository in repositories:
            if repo_hint and repository["repo_id"] != repo_hint:
                continue
            root = Path(repository["source_root"]).resolve()
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"context_scope 越过仓库边界：{repository['repo_id']}:{relative}"
                ) from exc
            if path.is_file():
                matches.append((repository["repo_id"], relative))
        if len(matches) != 1:
            if not matches:
                raise ValueError(f"context_scope 文件不存在：{raw}")
            raise ValueError(
                f"多仓库 context_scope 路径不唯一，请使用 repo_id:path：{raw}"
            )
        key = matches[0]
        if key in owned:
            raise ValueError(
                f"context_scope 与 source_scope 重叠：{key[0]}:{key[1]}"
            )
        if key in seen:
            continue
        seen.add(key)
        records.append({"repo_id": key[0], "path": key[1], "reason": "explicit_context"})
    return records


def _freeze_sources(state: PangeaState, repositories: list[dict], expansion: dict) -> list[dict]:
    paths_by_repo: dict[str, set[str]] = {}
    for group in expansion.get("groups", []):
        paths = paths_by_repo.setdefault(group["repo_id"], set())
        paths.update(group.get("code_paths", []))
        paths.update(group.get("context_paths", []))

    run_dir = run_directory(state)
    staging_root = run_dir / "inputs" / ".source-staging"
    frozen_root = run_dir / "inputs" / "source"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)
    frozen_repositories = []
    for repository in repositories:
        repo_id = repository["repo_id"]
        source_root = Path(repository["source_root"]).resolve()
        destination_root = staging_root / repo_id
        for relative in sorted(paths_by_repo.get(repo_id, set())):
            source = (source_root / relative).resolve()
            try:
                source.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(f"源码范围越过仓库边界：{repo_id}:{relative}") from exc
            if not source.is_file():
                raise ValueError(f"源码文件不存在：{repo_id}:{relative}")
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        frozen_repositories.append({
            "repo_id": repo_id,
            "source_root": str(frozen_root / repo_id),
            "git": repository.get("git", {}),
        })
    if frozen_root.exists():
        shutil.rmtree(frozen_root)
    staging_root.replace(frozen_root)
    return frozen_repositories


def _compact_inventory(
    inventory: dict, expansion: dict, analysis_language: str
) -> dict:
    files = []
    for item in inventory.get("files", []):
        record = {
            "repo_id": item["repo_id"],
            "path": item["path"],
            "line_count": item.get("line_count", 0),
            "parse_complete": item.get("parse_complete", False),
            "functions": [
                {"symbol": function["symbol"], "line": function["line"]}
                for function in item.get("functions", [])
            ],
            "branch_count": len(item.get("branches", [])),
            "calls": item.get("calls", []),
            "resource_signals": item.get("resource_signals", []),
        }
        if analysis_language == "lua":
            record.update({
                "requires": item.get("requires", []),
                "module_exports": item.get("module_exports", []),
                "state_writes": item.get("state_writes", []),
                "protected_calls": item.get("protected_calls", []),
                "coroutine_calls": item.get("coroutine_calls", []),
            })
        files.append(record)

    compact = {
        "analysis_language": analysis_language,
        "files": files,
        "owned_source_paths": [
            {"repo_id": group["repo_id"], "path": path}
            for group in expansion.get("groups", [])
            for path in group.get("code_paths", [])
        ],
        "scope_groups": expansion.get("groups", []),
        "context_files": expansion.get("context_files", []),
        "parse_failures": inventory.get("parse_failures", []),
    }
    owned = {
        (group["repo_id"], path)
        for group in expansion.get("groups", [])
        for path in group.get("code_paths", [])
    }
    parse_failures = inventory.get("parse_failures", [])
    compact["parse_failures_by_role"] = {
        "source": [
            item for item in parse_failures
            if isinstance(item, dict)
            and (item.get("repo_id"), item.get("path")) in owned
        ],
        "context": [
            item for item in parse_failures
            if isinstance(item, dict)
            and (item.get("repo_id"), item.get("path")) not in owned
        ],
    }
    if analysis_language == "lua":
        compact["require_dependencies"] = expansion.get(
            "require_dependencies", []
        )
    return compact


def _coverage_for_owned_sources(records: list[dict], expansion: dict) -> list[dict]:
    owned = {
        (group["repo_id"], path)
        for group in expansion.get("groups", [])
        for path in group.get("code_paths", [])
    }
    return [
        record
        for record in records
        if len(record.get("matches", [])) == 1
        and (
            record["matches"][0].get("repo_id"),
            record["matches"][0].get("path"),
        ) in owned
    ]


def _freeze_test_case_examples(state: PangeaState, examples: list[str]) -> list[str]:
    if not examples:
        return []
    destination_root = run_directory(state) / "inputs" / "test-case-examples"
    frozen: list[str] = []
    for number, raw_path in enumerate(examples, 1):
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"用例示例不是文件：{raw_path}")
        destination = destination_root / f"{number:03d}-{source.name}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        frozen.append(str(destination))
    return frozen


def prepare_inputs(state: PangeaState) -> PangeaState:
    contract = state["task_contract"]
    run_dir = run_directory(state)
    freeze_enabled_methodologies(
        state["data_root"],
        run_dir,
        state["run_id"],
    )
    repositories = resolve_repositories_from_contract(contract, state["data_root"])
    requested_scope = list(contract.get("source_scope") or ["."])
    analysis_language = detect_analysis_language(repositories, requested_scope)
    if analysis_language == "lua":
        expansion = expand_lua_analysis_scope(repositories, requested_scope)
    else:
        expansion = expand_analysis_scope(
            repositories,
            requested_scope,
            target=str(contract.get("target", "")),
            focus=list(contract.get("focus", [])),
        )
    explicit_context = _explicit_context_files(
        repositories,
        list(contract.get("context_scope") or []),
        expansion,
    )
    if explicit_context:
        existing_context = {
            (item.get("repo_id"), item.get("path"))
            for item in expansion.get("context_files", [])
            if isinstance(item, dict)
        }
        new_context = [
            item for item in explicit_context
            if (item["repo_id"], item["path"]) not in existing_context
        ]
        expansion["context_files"] = [
            *expansion.get("context_files", []),
            *new_context,
        ]
        for group in expansion.get("groups", []):
            for record in new_context:
                if record["repo_id"] == group["repo_id"]:
                    group.setdefault("context_paths", []).append(record["path"])
    if not any(group.get("code_paths") for group in expansion.get("groups", [])):
        raise ValueError(
            f"用户指定范围没有可分析的 {'Lua' if analysis_language == 'lua' else 'C/C++'} 源码"
        )
    frozen_repositories = _freeze_sources(state, repositories, expansion)
    module_scope = list(dict.fromkeys(
        path
        for group in expansion.get("groups", [])
        for path in group.get("code_paths", [])
    ))
    inventory_scope = list(dict.fromkeys([
        path
        for group in expansion.get("groups", [])
        for path in [*group.get("code_paths", []), *group.get("context_paths", [])]
    ]))
    if analysis_language == "lua":
        inventory = build_lua_inventory(frozen_repositories, inventory_scope)
    else:
        inventory = build_lightweight_inventory(frozen_repositories, inventory_scope)
    assets = analysis_asset_inputs(state["data_root"], contract.get("asset_ids"))
    coverage_match = match_coverage_records(assets["coverage_records"], inventory)
    zero_coverage = _coverage_for_owned_sources(
        relevant_zero_coverage(coverage_match),
        expansion,
    )
    frozen_examples = _freeze_test_case_examples(
        state,
        list(contract.get("test_case_examples", [])),
    )

    compact_path = run_dir / "inputs" / "planning-metadata.json"
    candidates_path = run_dir / "inputs" / "asset-candidates.json"
    asset_items_path = run_dir / "inputs" / "asset-items.json"
    coverage_path = run_dir / "inputs" / "coverage-gaps.json"
    inventory_path = run_dir / "inputs" / "inventory.json"
    source_manifest_path = run_dir / "inputs" / "source-manifest.json"
    compact_metadata = _compact_inventory(
        inventory, expansion, analysis_language
    )
    write_json(compact_path, compact_metadata)
    write_json(candidates_path, assets["candidates"])
    write_json(asset_items_path, assets["items"])
    write_json(coverage_path, zero_coverage)
    write_json(run_dir / "inputs" / "test-case-examples.json", frozen_examples)
    write_json(inventory_path, inventory)
    write_json(source_manifest_path, {
        "analysis_language": analysis_language,
        "repositories": frozen_repositories,
        "requested_scope": requested_scope,
        "source_scope": module_scope,
        "scope_expansion": expansion,
        "coverage_records": zero_coverage,
        "coverage_diagnostics": {
            "ambiguous": len(coverage_match["ambiguous"]),
            "unmatched": len(coverage_match["unmatched"]),
        },
        "parse_failures_by_role": compact_metadata.get("parse_failures_by_role", {}),
    })

    planning_skeleton_path = run_dir / "inputs" / "planning-result.skeleton.json"
    write_json(planning_skeleton_path, {
        "schema_version": "2.0",
        "summary": "<非空规划摘要>",
        "units": [],
        "source_ownership": {
            f"{item['repo_id']}:{item['path']}": "<unit_key>"
            for item in compact_metadata.get("owned_source_paths", [])
        },
        "unresolved": [],
    })
    try:
        frozen_contracts = freeze_run_contracts(
            run_dir,
            state["run_id"],
            planning_skeleton_path=planning_skeleton_path,
        )
    except ValueError as exc:
        # Contract parity is a Workflow-owned input gate. Persist the exact
        # failure before dispatch so callers can inspect why the Run stopped.
        progress = WorkflowProgress(
            run_id=state["run_id"],
            lifecycle_status="failed",
            stage="preparing",
            errors=[{
                "kind": "workflow_input_invalid",
                "reason": str(exc),
            }],
        )
        save_progress(state, progress)
        raise
    planning_contract = frozen_contracts["planning-result-v1"]

    action_id = f"{state['run_id']}:planning"
    task = PlanningTask(
        action_id=action_id,
        run_id=state["run_id"],
        target=contract["target"],
        focus=list(contract.get("focus", [])),
        analysis_language=analysis_language,
        repositories=[RepositoryRef.model_validate(item) for item in frozen_repositories],
        requested_scope=requested_scope,
        requested_context_scope=[
            f"{item['repo_id']}:{item['path']}" for item in explicit_context
        ],
        compact_metadata_path=str(compact_path),
        asset_candidates_path=str(candidates_path),
        methodology_catalog_path=str(
            run_dir / "inputs" / "methodologies" / "catalog.json"
        ),
        result_schema_path=str(planning_contract["result_schema_path"]),
        result_skeleton_path=str(planning_contract["result_skeleton_path"]),
        result_example_path=str(planning_contract["result_example_path"]),
        result_contract_path=str(planning_contract["result_contract_path"]),
        result_contract_manifest_path=str(
            planning_contract["result_contract_manifest_path"]
        ),
        result_path=str(planning_result_path(state)),
        rubric_paths=[str(project_path(
            "src",
            "pangea_agent",
            "rubrics",
            "builtin",
            f"{analysis_language}_unit_planning.md",
        ))],
    )
    task_path = planning_task_path(state)
    write_json(task_path, task.model_dump(mode="json"))
    initialize_result(
        Path(task.result_path),
        read_json(Path(task.result_skeleton_path)),
    )
    action = ActionState(
        action_id=action_id,
        action="dispatch_agent",
        role="planning",
        stage="unit_planning",
        task_path=str(task_path),
    )
    progress = WorkflowProgress(run_id=state["run_id"], stage="planning")
    add_action(progress, action)
    save_progress(state, progress)
    return {
        **state,
        "repositories": frozen_repositories,
        "module_scope": module_scope,
        "scope_expansion": expansion,
        "inventory": inventory,
        "coverage_report": {"matched": zero_coverage, "ambiguous": [], "unmatched": []},
        "lifecycle_status": progress.lifecycle_status,
        "stage": progress.stage,
        "agent_actions": [action.model_dump(mode="json")],
    }
