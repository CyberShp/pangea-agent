"""Graph path for ``source-first-v1`` Runs.

The legacy graph remains available for frozen legacy contracts.  New Runs use
this path, where planning and worker output are append-only notes and the
Graph only coordinates explicit machine handles supplied by the Agent.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.assets import analysis_asset_inputs
from pangea_agent.documents.coverage import match_coverage_records, relevant_zero_coverage
from pangea_agent.graph.result_store import active_records, initialize_result, read_result
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.workflow_store import (
    add_action,
    load_progress,
    run_directory,
    save_progress,
    source_first_index_path,
    source_first_result_path,
    source_first_task_path,
    source_first_version_set_path,
    project_path,
)
from pangea_agent.inventory.languages import detect_analysis_language
from pangea_agent.inventory.lua_scope_expander import expand_lua_analysis_scope
from pangea_agent.inventory.lua_source_scanner import build_lua_inventory
from pangea_agent.inventory.source_access import resolve_binding
from pangea_agent.inventory.source_regions import build_source_index
from pangea_agent.inventory.source_scanner import build_lightweight_inventory
from pangea_agent.methodology import freeze_enabled_methodologies
from pangea_agent.models.analysis import ActionState, RepositoryRef, WorkflowProgress
from pangea_agent.models.source_first import SourceBinding
from pangea_agent.repositories.resolver import resolve_repositories_from_contract


def _safe_key(value: str, fallback: str = "unit") -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value)).strip("-_")
    return (value or fallback)[:80]


def _path(value: str | Path) -> str:
    return str(Path(value))


def _normalize_context_path(value: str) -> str:
    from pangea_agent.graph.nodes.prepare_inputs import _normalize_context_path as normalize

    return normalize(value)


def _explicit_context_files(repositories: list[dict], context_scope: list[str], expansion: dict) -> list[dict]:
    from pangea_agent.graph.nodes.prepare_inputs import _explicit_context_files as resolve_context

    return resolve_context(repositories, context_scope, expansion)


def _freeze_sources(state: PangeaState, repositories: list[dict], expansion: dict) -> list[dict]:
    from pangea_agent.graph.nodes.prepare_inputs import _freeze_sources as freeze

    return freeze(state, repositories, expansion)


def _compact_inventory(inventory: dict, expansion: dict, analysis_language: str) -> dict:
    from pangea_agent.graph.nodes.prepare_inputs import _compact_inventory as compact

    return compact(inventory, expansion, analysis_language)


def _coverage_for_owned_sources(records: list[dict], expansion: dict) -> list[dict]:
    from pangea_agent.graph.nodes.prepare_inputs import _coverage_for_owned_sources as coverage

    return coverage(records, expansion)


def _freeze_test_case_examples(state: PangeaState, examples: list[str]) -> list[str]:
    from pangea_agent.graph.nodes.prepare_inputs import _freeze_test_case_examples as freeze

    return freeze(state, examples)


def _all_scope_paths(expansion: dict) -> list[dict[str, str]]:
    paths: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in expansion.get("groups", []):
        if not isinstance(group, dict) or not group.get("repo_id"):
            continue
        repo_id = str(group["repo_id"])
        for key in ("code_paths", "context_paths"):
            values = group.get(key, [])
            for value in values if isinstance(values, list) else []:
                item = (repo_id, str(value).replace("\\", "/"))
                if item in seen:
                    continue
                seen.add(item)
                paths.append({"repo_id": item[0], "path": item[1]})
    return paths


def _scope_paths(expansion: dict, key: str) -> list[dict[str, str]]:
    paths: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in expansion.get("groups", []):
        if not isinstance(group, dict) or not group.get("repo_id"):
            continue
        for value in group.get(key, []):
            pair = (str(group["repo_id"]), str(value).replace("\\", "/"))
            if pair not in seen:
                seen.add(pair)
                paths.append({"repo_id": pair[0], "path": pair[1]})
    return paths


def _analysis_allowed_paths(
    analysis_profile: str | None,
    expansion: dict,
    selected_paths: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Return frozen files an Analysis worker may read.

    Behavior-test analysis needs the complete explicitly requested source
    contract (for example a public header beside its implementation), even
    when Planning assigns only function regions from the implementation as
    owned work. Expanded reference files remain opt-in through the unit's
    context selection so this does not expose the whole dependency closure.
    """

    candidates = list(selected_paths)
    if analysis_profile == "behavior-test-v1":
        candidates.extend(_scope_paths(expansion, "code_paths"))
    allowed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        pair = (str(item["repo_id"]), str(item["path"]).replace("\\", "/"))
        if pair in seen:
            continue
        seen.add(pair)
        allowed.append({"repo_id": pair[0], "path": pair[1]})
    return allowed


def _freeze_source_first_rubrics(run_dir: Path) -> dict[str, str]:
    source = project_path("src", "pangea_agent", "rubrics", "builtin")
    destination = run_dir / "inputs" / "methodologies" / "builtin"
    destination.mkdir(parents=True, exist_ok=True)
    frozen: dict[str, str] = {}
    for path in sorted(source.glob("*.md")):
        target = destination / path.name
        if not target.exists():
            shutil.copyfile(path, target)
        frozen[path.stem] = str(target)
    return frozen


def _input(input_id: str, path: str | Path, label: str) -> dict[str, str]:
    return {"input_id": input_id, "path": str(path), "label": label}


def _analysis_rubric_names(
    analysis_profile: str | None,
    analysis_language: str,
    methodology_ids: list[str] | None = None,
) -> list[str]:
    if analysis_profile == "behavior-test-v1":
        return ["behavior_test_generation"]
    return [
        f"{analysis_language}_analysis",
        "dfx",
        "risk_reproducibility",
        "test_case_generation",
        *(methodology_ids or []),
    ]


def prepare_source_first_inputs(state: PangeaState) -> PangeaState:
    """Freeze source/input material and create exactly one planning action."""

    contract = state["task_contract"]
    run_dir = run_directory(state)
    freeze_enabled_methodologies(state["data_root"], run_dir, state["run_id"])
    frozen_rubrics = _freeze_source_first_rubrics(run_dir)
    repositories = resolve_repositories_from_contract(contract, state["data_root"])
    requested_scope = list(contract.get("source_scope") or ["."])
    analysis_language = detect_analysis_language(repositories, requested_scope)
    if analysis_language == "lua":
        expansion = expand_lua_analysis_scope(repositories, requested_scope)
    else:
        from pangea_agent.inventory.scope_expander import expand_analysis_scope

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
        existing = {
            (item.get("repo_id"), item.get("path"))
            for item in expansion.get("context_files", [])
            if isinstance(item, dict)
        }
        new_context = [
            item for item in explicit_context
            if (item["repo_id"], item["path"]) not in existing
        ]
        expansion["context_files"] = [*expansion.get("context_files", []), *new_context]
        for group in expansion.get("groups", []):
            for item in new_context:
                if item["repo_id"] == group.get("repo_id"):
                    group.setdefault("context_paths", []).append(item["path"])
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
    inventory_scope = list(dict.fromkeys(
        path
        for group in expansion.get("groups", [])
        for path in [*group.get("code_paths", []), *group.get("context_paths", [])]
    ))
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

    compact_metadata = _compact_inventory(inventory, expansion, analysis_language)
    inputs = run_dir / "inputs"
    compact_path = inputs / "planning-metadata.json"
    write_json(compact_path, compact_metadata)
    write_json(inputs / "asset-candidates.json", assets["candidates"])
    write_json(inputs / "asset-items.json", assets["items"])
    write_json(inputs / "coverage-gaps.json", zero_coverage)
    write_json(inputs / "test-case-examples.json", frozen_examples)
    write_json(inputs / "inventory.json", inventory)
    source_index = build_source_index(inventory)
    write_json(source_first_index_path(state), source_index)
    source_manifest = {
        "workflow_version": "source-first-v1",
        "analysis_profile": contract.get("analysis_profile"),
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
        "source_index_path": str(source_first_index_path(state)),
    }
    source_manifest_path = inputs / "source-manifest.json"
    write_json(source_manifest_path, source_manifest)
    write_json(inputs / "source-first-plan.json", {
        "format_version": "pangea-plan-v1",
        "units": [],
        "unresolved": [],
    })

    action_id = f"{state['run_id']}:planning"
    task_path = source_first_task_path(state, "planning")
    result_path = source_first_result_path(state, "planning")
    allowed_paths = _all_scope_paths(expansion)
    owned_scope_paths = _scope_paths(expansion, "code_paths")
    reference_scope_paths = _scope_paths(expansion, "context_paths")
    planning_rubric = frozen_rubrics[f"{analysis_language}_unit_planning"]
    task = {
        "format_version": "source-first-task-v1",
        "workflow_version": "source-first-v1",
        "analysis_profile": contract.get("analysis_profile"),
        "task_type": "source_first_plan",
        "action_id": action_id,
        "run_id": state["run_id"],
        "target": contract["target"],
        "focus": list(contract.get("focus", [])),
        "analysis_language": analysis_language,
        "repositories": frozen_repositories,
        "source_manifest_path": str(source_manifest_path),
        "source_index_path": str(source_first_index_path(state)),
        "inventory_path": str(inputs / "inventory.json"),
        "compact_metadata_path": str(compact_path),
        "allowed_paths": allowed_paths,
        "owned_scope_paths": owned_scope_paths,
        "reference_scope_paths": reference_scope_paths,
        "requested_scope": requested_scope,
        "effective_context_budget": contract.get("effective_context_budget"),
        "result_format": "pangea-plan-v1",
        "result_path": str(result_path),
        "rubric_paths": [planning_rubric],
        "inputs": [
            _input("planning_metadata", compact_path, "源码结构摘要"),
            _input("asset_candidates", inputs / "asset-candidates.json", "候选结构化资料"),
            _input("coverage_gaps", inputs / "coverage-gaps.json", "Coverage 零覆盖提示"),
            _input("methodology_catalog", inputs / "methodologies" / "catalog.json", "方法论目录"),
            _input("unit_planning_rubric", planning_rubric, "单元规划方法"),
        ],
    }
    write_json(task_path, task)
    initialize_result(
        result_path,
        SourceBinding(
            data_root=str(Path(state["data_root"]).resolve()),
            run_id=state["run_id"],
            action_id=action_id,
            task_id="pending",
        ),
    )
    progress = WorkflowProgress(
        run_id=state["run_id"],
        workflow_version="source-first-v1",
        runtime_commit=contract.get("runtime_commit"),
        model_id=contract.get("model_id"),
        effective_context_budget=contract.get("effective_context_budget"),
        stage="planning",
    )
    add_action(progress, ActionState(
        action_id=action_id,
        action="dispatch_agent",
        role="planning",
        stage="unit_planning",
        task_path=str(task_path),
    ))
    save_progress(state, progress)
    return {
        **state,
        "workflow_version": "source-first-v1",
        "repositories": frozen_repositories,
        "module_scope": module_scope,
        "scope_expansion": expansion,
        "inventory": inventory,
        "source_manifest": source_manifest,
        "coverage_report": {"matched": zero_coverage, "ambiguous": [], "unmatched": []},
        "lifecycle_status": progress.lifecycle_status,
        "stage": progress.stage,
        "agent_actions": [progress.actions[action_id].model_dump(mode="json")],
    }


def _load_notes_action(state: PangeaState, action: ActionState):
    task = read_json(Path(action.task_path))
    result_path = task.get("result_path")
    if not isinstance(result_path, str) or not result_path:
        raise ValueError(f"source-first task 缺少 result_path：{action.action_id}")
    return task, read_result(Path(result_path))


def _planning_units(state: PangeaState, action: ActionState, task: dict, result) -> list[dict]:
    """Extract explicit unit handles; no Python inference or source splitting."""

    units_by_id: dict[str, dict] = {}
    order: list[str] = []
    for record in active_records(result):
        if record.kind != "unit_plan" or not isinstance(record.body, dict):
            continue
        body = record.body
        relations = record.relates_to if isinstance(record.relates_to, list) else []
        unit_id = str(body.get("unit_id") or (relations[0] if relations else record.record_id))
        owned = body.get("owned_regions", [])
        if not isinstance(owned, list) or not owned:
            # Preserve the original record.  The missing unit handle is a
            # planning incompleteness, not a semantic guess by Python.
            continue
        context = body.get("context_regions", [])
        if unit_id not in units_by_id:
            order.append(unit_id)
        units_by_id[unit_id] = {
            "unit_id": unit_id,
            "title": str(body.get("title") or unit_id),
            "owned_regions": owned,
            "context_regions": context if isinstance(context, list) else [],
            "purpose": str(body.get("purpose") or ""),
            "context_files": [
                str(item) for item in body.get("context_files", [])
            ] if isinstance(body.get("context_files", []), list) else [],
            "coverage_ids": [
                str(item) for item in body.get("coverage_ids", [])
            ] if isinstance(body.get("coverage_ids", []), list) else [],
            "asset_item_ids": [
                str(item) for item in body.get("asset_item_ids", [])
            ] if isinstance(body.get("asset_item_ids", []), list) else [],
            "methodology_ids": [
                str(item) for item in body.get(
                    "methodology_ids", body.get("mechanism_ids", [])
                )
            ] if isinstance(
                body.get("methodology_ids", body.get("mechanism_ids", [])), list
            ) else [],
            "plan_record_id": record.record_id,
        }
    return [units_by_id[unit_id] for unit_id in order if unit_id in units_by_id]


def _region_lookup(state: PangeaState) -> dict[str, dict]:
    index = read_json(source_first_index_path(state))
    return {
        str(region["region_id"]): region
        for file in index.get("files", [])
        if isinstance(file, dict)
        for region in file.get("regions", [])
        if isinstance(region, dict) and region.get("region_id")
    }


def _region_ref(value: Any, lookup: dict[str, dict]) -> dict:
    if isinstance(value, str):
        region_id = value
    elif isinstance(value, dict):
        region_id = value.get("region_id")
    else:
        raise ValueError("owned_regions 中的区域引用必须是 region_id 或 object")
    region = lookup.get(str(region_id))
    if region is None:
        raise ValueError(f"Planning 引用了未知 source region：{region_id}")
    return region


def _unit_paths(unit: dict, lookup: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    owned = [_region_ref(item, lookup) for item in unit["owned_regions"]]
    context = [_region_ref(item, lookup) for item in unit.get("context_regions", [])]
    return owned, context


def _unit_context_files(
    unit: dict,
    all_paths: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    """Resolve only planner-selected files already present in frozen scope."""

    available = {
        (str(item["repo_id"]), str(item["path"]).replace("\\", "/")): item
        for item in all_paths
        if isinstance(item, dict) and item.get("repo_id") and item.get("path")
    }
    by_path: dict[str, list[dict[str, str]]] = {}
    for (_repo_id, path), item in available.items():
        by_path.setdefault(path, []).append(item)

    resolved: list[dict[str, str]] = []
    unresolved: list[str] = []
    seen: set[tuple[str, str]] = set()
    for raw in unit.get("context_files", []):
        value = str(raw).strip().replace("\\", "/")
        matches: list[dict[str, str]] = []
        if ":" in value:
            repo_id, path = value.split(":", 1)
            item = available.get((repo_id, path))
            if item is not None:
                matches = [item]
        else:
            matches = by_path.get(value, [])
        if len(matches) != 1:
            unresolved.append(value)
            continue
        item = matches[0]
        pair = (item["repo_id"], item["path"])
        if pair not in seen:
            seen.add(pair)
            resolved.append(item)
    return resolved, unresolved


def _required_direct_context_files(manifest: dict) -> list[str]:
    """Return frozen direct dependencies every Analysis unit may inspect.

    Scope expansion has already established these as direct definitions or
    inline dependencies of the requested source.  Exposing them does not
    assign ownership or infer a semantic conclusion.
    """

    result: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in manifest.get("scope_expansion", {}).get("context_files", []):
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "")
        if not reason.startswith(("direct_callee_definition:", "direct_inline_dependency:")):
            continue
        repo_id = str(item.get("repo_id") or "")
        path = str(item.get("path") or "").replace("\\", "/")
        pair = (repo_id, path)
        if not repo_id or not path or pair in seen:
            continue
        seen.add(pair)
        result.append(f"{repo_id}:{path}")
    return result


def _make_analysis_actions(state: PangeaState, progress: WorkflowProgress, units: list[dict], plan_task: dict) -> None:
    lookup = _region_lookup(state)
    source_manifest_path = plan_task["source_manifest_path"]
    source_index_path = plan_task["source_index_path"]
    manifest = read_json(Path(source_manifest_path))
    all_paths = _all_scope_paths(manifest.get("scope_expansion", {}))
    required_direct_context_files = _required_direct_context_files(manifest)
    frozen_rubrics = {
        path.stem: str(path)
        for path in (run_directory(state) / "inputs" / "methodologies" / "builtin").glob("*.md")
    }
    for unit in units:
        key = _safe_key(unit["unit_id"])
        action_id = f"{state['run_id']}:analysis:{unit['unit_id']}"
        owned, context = _unit_paths(unit, lookup)
        context_file_request = {
            **unit,
            "context_files": list(dict.fromkeys([
                *unit.get("context_files", []),
                *required_direct_context_files,
            ])),
        }
        context_files, unresolved_context_files = _unit_context_files(
            context_file_request, all_paths
        )
        allowed = []
        seen: set[tuple[str, str]] = set()
        for region in [*owned, *context]:
            pair = (str(region["repo_id"]), str(region["path"]))
            if pair not in seen:
                allowed.append({"repo_id": pair[0], "path": pair[1]})
                seen.add(pair)
        for item in context_files:
            pair = (item["repo_id"], item["path"])
            if pair not in seen:
                allowed.append(item)
                seen.add(pair)
        # The Agent decides region ownership; context is explicit and can be
        # shared.  Unknown or outside-range handles were rejected above.
        result_path = source_first_result_path(state, "analysis", key)
        task_path = source_first_task_path(state, "analysis", key)
        analysis_profile = state["task_contract"].get("analysis_profile")
        allowed = _analysis_allowed_paths(
            analysis_profile,
            manifest.get("scope_expansion", {}),
            allowed,
        )
        rubric_names = _analysis_rubric_names(
            analysis_profile,
            plan_task.get("analysis_language", "c_cpp"),
            unit.get("methodology_ids", []),
        )
        task = {
            "format_version": "source-first-task-v1",
            "workflow_version": "source-first-v1",
            "analysis_profile": analysis_profile,
            "task_type": "source_first_analysis",
            "action_id": action_id,
            "run_id": state["run_id"],
            "target": state["task_contract"]["target"],
            "analysis_language": plan_task.get("analysis_language", "c_cpp"),
            "unit_id": unit["unit_id"],
            "title": unit["title"],
            "purpose": unit["purpose"],
            "owned_regions": owned,
            "context_regions": context,
            "context_files": context_files,
            "required_direct_context_files": required_direct_context_files,
            "unresolved_context_files": unresolved_context_files,
            "coverage_ids": unit.get("coverage_ids", []),
            "asset_item_ids": unit.get("asset_item_ids", []),
            "methodology_ids": unit.get("methodology_ids", []),
            "allowed_paths": allowed,
            "all_scope_paths": all_paths,
            "source_manifest_path": source_manifest_path,
            "source_index_path": source_index_path,
            "selected_inputs_path": str(run_directory(state) / "inputs" / "test-case-examples.json"),
            "effective_context_budget": state["task_contract"].get("effective_context_budget"),
            "result_path": str(result_path),
            "rubric_paths": [
                frozen_rubrics[name]
                for name in rubric_names
                if name in frozen_rubrics
            ],
        }
        task["inputs"] = [
            _input("selected_inputs", run_directory(state) / "inputs" / "test-case-examples.json", "用户用例示例"),
            _input("asset_items", run_directory(state) / "inputs" / "asset-items.json", "已选结构化资料"),
            _input("coverage_gaps", run_directory(state) / "inputs" / "coverage-gaps.json", "Coverage 零覆盖提示"),
            *[
                _input(f"rubric_{Path(path).stem}", path, f"方法论 {Path(path).stem}")
                for path in task["rubric_paths"]
            ],
        ]
        write_json(task_path, task)
        initialize_result(
            result_path,
            SourceBinding(
                data_root=str(Path(state["data_root"]).resolve()),
                run_id=state["run_id"],
                action_id=action_id,
                task_id="pending",
            ),
        )
        add_action(progress, ActionState(
            action_id=action_id,
            action="dispatch_agent",
            role="analysis",
            stage="unit_analysis",
            task_path=str(task_path),
        ))


def _prepare_review(state: PangeaState, progress: WorkflowProgress) -> None:
    # The Reviewer is a newly dispatched task; the host binds it before any
    # source/result operation.  The comparison continuation reuses this ID.
    action_id = f"{state['run_id']}:review"
    task_path = source_first_task_path(state, "review")
    result_path = source_first_result_path(state, "review")
    manifest = read_json(run_directory(state) / "inputs" / "source-manifest.json")
    frozen_rubrics = {
        path.stem: str(path)
        for path in (run_directory(state) / "inputs" / "methodologies" / "builtin").glob("*.md")
    }
    analysis_profile = state["task_contract"].get("analysis_profile")
    review_rubric_names = _analysis_rubric_names(
        analysis_profile,
        manifest.get("analysis_language", "c_cpp"),
    )
    review_rubrics = [
        frozen_rubrics[name]
        for name in review_rubric_names
        if name in frozen_rubrics
    ]
    task = {
        "format_version": "source-first-task-v1",
        "workflow_version": "source-first-v1",
        "analysis_profile": analysis_profile,
        "task_type": "source_first_review",
        "review_stage": "independent_review",
        "action_id": action_id,
        "run_id": state["run_id"],
        "target": state["task_contract"]["target"],
        "analysis_language": manifest.get("analysis_language", "c_cpp"),
        "source_manifest_path": str(run_directory(state) / "inputs" / "source-manifest.json"),
        "source_index_path": str(source_first_index_path(state)),
        "allowed_paths": _all_scope_paths(manifest.get("scope_expansion", {})),
        "owned_scope_paths": _scope_paths(manifest.get("scope_expansion", {}), "code_paths"),
        "reference_scope_paths": _scope_paths(manifest.get("scope_expansion", {}), "context_paths"),
        "effective_context_budget": state["task_contract"].get("effective_context_budget"),
        "result_path": str(result_path),
        "rubric_paths": review_rubrics,
        "inputs": [
            _input("unit_plan", run_directory(state) / "inputs" / "source-first-plan.json", "中性单元计划"),
            _input("selected_inputs", run_directory(state) / "inputs" / "test-case-examples.json", "用户用例示例"),
            _input("asset_items", run_directory(state) / "inputs" / "asset-items.json", "已选结构化资料"),
            _input("coverage_gaps", run_directory(state) / "inputs" / "coverage-gaps.json", "Coverage 零覆盖提示"),
            *[
                _input(f"rubric_{Path(path).stem}", path, f"方法论 {Path(path).stem}")
                for path in review_rubrics
            ],
        ],
    }
    write_json(task_path, task)
    initialize_result(
        result_path,
        SourceBinding(
            data_root=str(Path(state["data_root"]).resolve()),
            run_id=state["run_id"],
            action_id=action_id,
            task_id="pending",
        ),
    )
    add_action(progress, ActionState(
        action_id=action_id,
        action="dispatch_agent",
        role="review",
        stage="independent_review",
        task_path=str(task_path),
    ))


def _write_comparison_version_set(
    state: PangeaState,
    progress: WorkflowProgress,
    review_action: ActionState,
) -> tuple[Path, str]:
    """Freeze the exact accepted revisions that comparison may inspect."""

    entries: list[dict[str, Any]] = []
    for action in progress.actions.values():
        if action.role != "analysis" or action.status != "accepted":
            continue
        task = read_json(Path(action.task_path))
        result_path = task.get("result_path")
        if not isinstance(result_path, str) or not action.task_id:
            raise ValueError(f"accepted analysis action 缺少绑定结果：{action.action_id}")
        result = read_result(Path(result_path))
        entries.append({
            "role": "analysis",
            "unit_id": task.get("unit_id"),
            "action_id": action.action_id,
            "task_id": action.task_id,
            "result_path": result_path,
            "revision": result.revision,
        })
    if review_action.status != "accepted" or not review_action.task_id:
        raise ValueError("independent review 尚未绑定并接受，不能生成 comparison version set")
    review_task = read_json(Path(review_action.task_path))
    review_result_path = review_task.get("result_path")
    if not isinstance(review_result_path, str):
        raise ValueError("independent review 缺少 result_path")
    review_result = read_result(Path(review_result_path))
    entries.append({
        "role": "independent_review",
        "action_id": review_action.action_id,
        "task_id": review_action.task_id,
        "result_path": review_result_path,
        "revision": review_result.revision,
    })
    identity = {
        "run_id": state["run_id"],
        "workflow_version": "source-first-v1",
        "entries": entries,
    }
    version_set_id = "vs-" + hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    payload = {
        "format_version": "pangea-version-set-v1",
        "version_set_id": version_set_id,
        **identity,
    }
    path = source_first_version_set_path(state, "comparison")
    if path.is_file():
        existing = read_json(path)
        if existing != payload:
            raise ValueError("comparison version set 已存在且内容不一致，拒绝覆盖")
    else:
        write_json(path, payload)
    return path, version_set_id


def review_correction_routes(result, known_unit_ids: set[str]) -> tuple[str | None, dict[str, list], list[str]]:
    """Resolve the Reviewer's explicit selection using existing finding bindings."""

    records = active_records(result)
    decision = next((
        record.body for record in reversed(records)
        if record.kind == "review_decision" and isinstance(record.body, dict)
    ), {})
    disposition = decision.get("disposition")
    warnings: list[str] = []
    if not isinstance(disposition, str) or disposition not in {"pass", "unresolved", "finding"}:
        warnings.append("review_decision 尚无可读取的质量结论")
        disposition = None
    findings = {record.record_id: record for record in records if record.kind == "finding"}
    routes: dict[str, list] = {}
    if "correction_record_ids" in decision:
        selected = decision["correction_record_ids"]
        if not isinstance(selected, list):
            return disposition, routes, [*warnings, "correction_record_ids 必须是 record_id 数组；原值已保留"]
        for record_id in selected:
            record = findings.get(record_id) if isinstance(record_id, str) else None
            if record is None:
                warnings.append(f"correction_record_ids 未指向当前有效 finding：{record_id!r}")
                continue
            units = record.relates_to
            if not isinstance(units, list) or not units:
                warnings.append(f"修正记录 {record_id} 尚无有效单元绑定")
                continue
            for unit_id in units:
                if not isinstance(unit_id, str) or unit_id not in known_unit_ids:
                    warnings.append(f"修正记录 {record_id} 的单元绑定不可用：{unit_id!r}")
                    continue
                targets = routes.setdefault(unit_id, [])
                if record not in targets:
                    targets.append(record)
    else:
        # Existing saved decisions explicitly selected units before record IDs
        # became the client contract. Preserve that recorded route on resume.
        units = decision.get("closure_units", [])
        if not isinstance(units, list):
            return disposition, routes, [*warnings, "closure_units 必须是 unit_id 数组；原值已保留"]
        for unit_id in units:
            if not isinstance(unit_id, str) or unit_id not in known_unit_ids:
                warnings.append(f"closure_units 的单元绑定不可用：{unit_id!r}")
                continue
            related = [
                record for record in findings.values()
                if isinstance(record.relates_to, list) and unit_id in record.relates_to
            ]
            if related:
                routes[unit_id] = related
            else:
                warnings.append(f"closure_units 的单元 {unit_id} 尚无当前有效 finding 绑定")
        if disposition == "finding" and not units:
            warnings.append("review_decision 声明 finding，但尚未明确选择修正记录或单元")
    return disposition, routes, warnings


def _source_first_advance(state: PangeaState, progress: WorkflowProgress) -> PangeaState:
    current = [action for action in progress.actions.values() if action.status in {"pending", "dispatched", "settled"}]
    if not current or any(action.status != "settled" for action in current):
        return {**state, "ready_to_finalize": progress.stage == "reporting", "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": [
            action.model_dump(mode="json") for action in current if action.status == "pending"
        ]}
    if progress.stage == "planning":
        action = next(action for action in current if action.role == "planning")
        task, result = _load_notes_action(state, action)
        units = _planning_units(state, action, task, result)
        plan_unresolved: list[dict[str, Any]] = []
        if not units:
            # A completed notes shell with no explicit dispatchable unit is
            # transport-valid but cannot safely schedule analysis.  Preserve
            # the notes and finish as UNRESOLVED/attention instead of
            # inferring a split or discarding the Agent's body.
            plan_unresolved.append({
                "kind": "planning_unit_handles_missing",
                "message": "Planning 未提供带 owned_regions 的可调度单元；原文已保留",
                "record_ids": [record.record_id for record in result.records],
            })
        write_json(run_directory(state) / "inputs" / "source-first-plan.json", {
            "format_version": "pangea-plan-v1",
            "revision": result.revision,
            "units": units,
            "unresolved": plan_unresolved,
        })
        if not units:
            action.action = "continue_agent"
            action.status = "pending"
            action.error = plan_unresolved[0]["message"]
            save_progress(state, progress)
            return {**state, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage,
                    "agent_actions": [action.model_dump(mode="json")]}
        progress.actions[action.action_id].status = "accepted"
        progress.accepted_revisions[action.action_id] = result.revision
        _make_analysis_actions(state, progress, units, task)
        progress.stage = "analyzing"
        save_progress(state, progress)
        return {**state, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": [
            action.model_dump(mode="json") for action in progress.actions.values() if action.status == "pending"
        ]}
    if progress.stage == "analyzing":
        for action in current:
            if action.role == "analysis":
                action.status = "accepted"
                if action.action_id.rsplit(":", 1)[-1] not in progress.completed_analysis_units:
                    progress.completed_analysis_units.append(action.action_id.rsplit(":", 1)[-1])
                try:
                    result = read_result(read_json(Path(action.task_path))["result_path"])
                    progress.accepted_revisions[action.action_id] = result.revision
                except (OSError, ValueError, KeyError):
                    progress.accepted_revisions[action.action_id] = 0
        _prepare_review(state, progress)
        progress.stage = "reviewing"
        save_progress(state, progress)
        return {**state, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": [
            action.model_dump(mode="json") for action in progress.actions.values() if action.status == "pending"
        ]}
    if progress.stage == "reviewing":
        independent_actions = [
            action for action in current
            if action.role == "review" and action.stage == "independent_review"
        ]
        if independent_actions:
            review_action = independent_actions[0]
            _task, _result = _load_notes_action(state, review_action)
            review_action.status = "accepted"
            progress.accepted_revisions[review_action.action_id] = _result.revision
            version_set_path, version_set_id = _write_comparison_version_set(
                state, progress, review_action
            )
            comparison_action_id = f"{state['run_id']}:comparison-review"
            comparison_task_path = source_first_task_path(state, "comparison")
            comparison_result_path = source_first_result_path(state, "comparison")
            review_task = read_json(Path(review_action.task_path))
            comparison_task = {
                **review_task,
                "task_type": "source_first_review",
                "review_stage": "comparison_review",
                "action_id": comparison_action_id,
                "version_set_id": version_set_id,
                "version_set_path": str(version_set_path),
                "result_path": str(comparison_result_path),
            }
            write_json(comparison_task_path, comparison_task)
            initialize_result(
                comparison_result_path,
                SourceBinding(
                    data_root=str(Path(state["data_root"]).resolve()),
                    run_id=state["run_id"],
                    action_id=comparison_action_id,
                    task_id="pending",
                ),
            )
            add_action(progress, ActionState(
                action_id=comparison_action_id,
                action="continue_agent",
                role="review",
                stage="comparison_review",
                task_path=str(comparison_task_path),
                task_id=review_action.task_id,
            ))
            save_progress(state, progress)
            return {**state, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": [
                action.model_dump(mode="json") for action in progress.actions.values() if action.status == "pending"
            ]}

        comparison_actions = [
            action for action in current
            if action.role == "review" and action.stage == "comparison_review"
        ]
        if not comparison_actions:
            raise ValueError("reviewing 阶段没有可接受的 Reviewer action")
        comparison_action = comparison_actions[0]
        _task, result = _load_notes_action(state, comparison_action)
        known_analysis = {
            action.action_id.rsplit(":", 1)[-1]: action
            for action in progress.actions.values()
            if action.role == "analysis"
        }
        disposition, correction_routes, routing_warnings = review_correction_routes(
            result, set(known_analysis)
        )
        closure_origins = {}
        for unit_id in correction_routes:
            origin = known_analysis[unit_id]
            if not origin.task_id or origin.task_id == "pending":
                raise ValueError(f"closure 原 worker 缺少真实 task_id：{origin.action_id}")
            binding, run_dir, _action, original_task = resolve_binding(
                state["data_root"], state["run_id"], origin.action_id, origin.task_id
            )
            for field, expected in (
                ("run_id", state["run_id"]), ("action_id", origin.action_id), ("unit_id", unit_id)
            ):
                if original_task.get(field) != expected:
                    raise ValueError(f"closure 原 task.{field} 与绑定不一致：{origin.action_id}")
            original_result_path = original_task.get("result_path")
            if not isinstance(original_result_path, str) or not original_result_path:
                raise ValueError(f"closure 原 task 缺少 result_path：{origin.action_id}")
            result_file = Path(original_result_path).resolve()
            if not result_file.is_relative_to(run_dir):
                raise ValueError(f"closure 原 result_path 越出当前 Run 数据边界：{origin.action_id}")
            original = read_result(result_file)
            for field in ("data_root", "run_id", "action_id", "task_id"):
                actual = getattr(original.binding, field)
                expected = getattr(binding, field)
                if field == "data_root":
                    actual, expected = Path(actual).resolve(), Path(expected).resolve()
                if actual != expected:
                    raise ValueError(f"closure 原 result binding.{field} 与绑定不一致：{origin.action_id}")
            closure_origins[unit_id] = (origin, original_task, original_result_path, original)
        comparison_action.status = "accepted"
        progress.accepted_revisions[comparison_action.action_id] = result.revision
        progress.quality_status = (
            "PASS" if disposition == "pass" and not routing_warnings and not correction_routes
            else "UNRESOLVED"
        )
        progress.degradations.extend({
            "kind": "source_first_correction_routing",
            "message": message,
        } for message in routing_warnings)
        if correction_routes:
            progress.degradations.append({
                "kind": "source_first_review_finding",
                "record_ids": sorted({
                    record.record_id
                    for records in correction_routes.values()
                    for record in records
                }),
            })
        if not correction_routes:
            progress.stage = "reporting"
            save_progress(state, progress)
            return {**state, "ready_to_finalize": True, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": []}

        for unit_id, selected_findings in correction_routes.items():
            origin, original_task, original_result_path, original = closure_origins[unit_id]
            closure_action_id = f"{state['run_id']}:closure:{unit_id}"
            closure_task_path = source_first_task_path(state, "closure", _safe_key(unit_id))
            closure_result_path = source_first_result_path(state, "closure", _safe_key(unit_id))
            correction_records = [
                record.model_dump(mode="json")
                for record in selected_findings
            ]
            correction_path = (
                run_directory(state)
                / "inputs"
                / f"closure-corrections-{_safe_key(unit_id)}.json"
            )
            write_json(correction_path, {
                "format_version": "source-first-closure-corrections-v1",
                "run_id": state["run_id"],
                "unit_id": unit_id,
                "records": correction_records,
            })
            closure_task = {
                **original_task,
                "task_type": "source_first_closure",
                "action_id": closure_action_id,
                "original_task_path": origin.task_path,
                "original_result_path": original_result_path,
                "base_revision": original.revision,
                "current_revision": original.revision,
                "correction_records": correction_records,
                "correction_record_ids": [
                    record["record_id"] for record in correction_records
                ],
                "correction_input_id": "correction_records",
                "inputs": [
                    _input(
                        "correction_records",
                        correction_path,
                        "Comparison 选中的定向修正记录",
                    ),
                    *original_task.get("inputs", []),
                ],
                "result_path": str(closure_result_path),
            }
            write_json(closure_task_path, closure_task)
            write_json(closure_result_path, original.model_copy(update={
                "binding": SourceBinding(
                    data_root=str(Path(state["data_root"]).resolve()),
                    run_id=state["run_id"],
                    action_id=closure_action_id,
                    task_id="pending",
                ),
                "completion": None,
                "receipts": {},
            }).model_dump(mode="json"))
            add_action(progress, ActionState(
                action_id=closure_action_id,
                action="continue_agent",
                role="closure",
                stage="targeted_closure",
                task_path=str(closure_task_path),
                task_id=origin.task_id,
            ))
        progress.stage = "closing"
        save_progress(state, progress)
        return {**state, "ready_to_finalize": False, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": [
            action.model_dump(mode="json") for action in progress.actions.values() if action.status == "pending"
        ]}
    if progress.stage == "closing":
        for action in current:
            if action.role == "closure":
                action.status = "accepted"
                progress.completed_closure_units.append(action.action_id.rsplit(":", 1)[-1])
                _task, result = _load_notes_action(state, action)
                progress.accepted_revisions[action.action_id] = result.revision
        progress.quality_status = "UNRESOLVED"
        progress.stage = "reporting"
        save_progress(state, progress)
        return {**state, "ready_to_finalize": True, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": []}
    if progress.stage == "reporting":
        return {**state, "ready_to_finalize": True, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": []}
    return {**state, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": []}


def advance_source_first(state: PangeaState) -> PangeaState:
    progress = load_progress(state)
    if progress is None:
        raise ValueError("Run progress 不存在")
    if progress.lifecycle_status != "running":
        return {**state, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage}
    return _source_first_advance(state, progress)
