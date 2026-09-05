"""Graph path for ``source-first-v1`` Runs.

The legacy graph remains available for frozen legacy contracts.  New Runs use
this path, where planning and worker output are append-only notes and the
Graph only coordinates explicit machine handles supplied by the Agent.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.assets import analysis_asset_inputs
from pangea_agent.documents.coverage import match_coverage_records, relevant_zero_coverage
from pangea_agent.graph.result_store import initialize_result, read_result
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
)
from pangea_agent.inventory.languages import detect_analysis_language
from pangea_agent.inventory.lua_scope_expander import expand_lua_analysis_scope
from pangea_agent.inventory.lua_source_scanner import build_lua_inventory
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


def prepare_source_first_inputs(state: PangeaState) -> PangeaState:
    """Freeze source/input material and create exactly one planning action."""

    contract = state["task_contract"]
    run_dir = run_directory(state)
    freeze_enabled_methodologies(state["data_root"], run_dir, state["run_id"])
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
    task = {
        "format_version": "source-first-task-v1",
        "workflow_version": "source-first-v1",
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
        "result_format": "pangea-plan-v1",
        "result_path": str(result_path),
        "rubric_paths": [],
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

    units: list[dict] = []
    seen: set[str] = set()
    for record in result.records:
        if record.kind != "unit_plan" or not isinstance(record.body, dict):
            continue
        body = record.body
        relations = record.relates_to if isinstance(record.relates_to, list) else []
        unit_id = str(body.get("unit_id") or (relations[0] if relations else record.record_id))
        if unit_id in seen:
            continue
        owned = body.get("owned_regions", [])
        if not isinstance(owned, list) or not owned:
            # Preserve the original record.  The missing unit handle is a
            # planning incompleteness, not a semantic guess by Python.
            continue
        context = body.get("context_regions", [])
        units.append({
            "unit_id": unit_id,
            "title": str(body.get("title") or unit_id),
            "owned_regions": owned,
            "context_regions": context if isinstance(context, list) else [],
            "purpose": str(body.get("purpose") or ""),
            "plan_record_id": record.record_id,
        })
        seen.add(unit_id)
    return units


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


def _make_analysis_actions(state: PangeaState, progress: WorkflowProgress, units: list[dict], plan_task: dict) -> None:
    lookup = _region_lookup(state)
    source_manifest_path = plan_task["source_manifest_path"]
    source_index_path = plan_task["source_index_path"]
    all_paths = _all_scope_paths(read_json(Path(source_manifest_path)).get("scope_expansion", {}))
    for unit in units:
        key = _safe_key(unit["unit_id"])
        action_id = f"{state['run_id']}:analysis:{unit['unit_id']}"
        owned, context = _unit_paths(unit, lookup)
        allowed = []
        seen: set[tuple[str, str]] = set()
        for region in [*owned, *context]:
            pair = (str(region["repo_id"]), str(region["path"]))
            if pair not in seen:
                allowed.append({"repo_id": pair[0], "path": pair[1]})
                seen.add(pair)
        # The Agent decides region ownership; context is explicit and can be
        # shared.  Unknown or outside-range handles were rejected above.
        result_path = source_first_result_path(state, "analysis", key)
        task_path = source_first_task_path(state, "analysis", key)
        task = {
            "format_version": "source-first-task-v1",
            "workflow_version": "source-first-v1",
            "task_type": "source_first_analysis",
            "action_id": action_id,
            "run_id": state["run_id"],
            "target": state["task_contract"]["target"],
            "analysis_language": plan_task.get("analysis_language", "c_cpp"),
            "unit_id": unit["unit_id"],
            "title": unit["title"],
            "owned_regions": owned,
            "context_regions": context,
            "allowed_paths": allowed,
            "all_scope_paths": all_paths,
            "source_manifest_path": source_manifest_path,
            "source_index_path": source_index_path,
            "selected_inputs_path": str(run_directory(state) / "inputs" / "test-case-examples.json"),
            "result_path": str(result_path),
            "rubric_paths": [],
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
    task = {
        "format_version": "source-first-task-v1",
        "workflow_version": "source-first-v1",
        "task_type": "source_first_review",
        "review_stage": "independent_review",
        "action_id": action_id,
        "run_id": state["run_id"],
        "target": state["task_contract"]["target"],
        "analysis_language": manifest.get("analysis_language", "c_cpp"),
        "source_manifest_path": str(run_directory(state) / "inputs" / "source-manifest.json"),
        "source_index_path": str(source_first_index_path(state)),
        "allowed_paths": _all_scope_paths(manifest.get("scope_expansion", {})),
        "result_path": str(result_path),
        "rubric_paths": [],
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


def _review_disposition(result) -> tuple[str | None, list[str]]:
    """Read only the reviewer's explicit transport decision record."""

    for record in reversed(result.records):
        if record.kind != "review_decision" or not isinstance(record.body, dict):
            continue
        disposition = record.body.get("disposition")
        if disposition not in {"pass", "unresolved", "finding"}:
            return None, []
        units = record.body.get("closure_units", [])
        return str(disposition), [str(item) for item in units] if isinstance(units, list) else []
    return None, []


def _source_first_advance(state: PangeaState, progress: WorkflowProgress) -> PangeaState:
    current = [action for action in progress.actions.values() if action.status in {"pending", "dispatched", "settled"}]
    if not current or any(action.status != "settled" for action in current):
        return {**state, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": [
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
        progress.actions[action.action_id].status = "accepted"
        if not units:
            progress.quality_status = "UNRESOLVED"
            progress.needs_user = True
            progress.blocking_reason = plan_unresolved[0]
            progress.degradations.extend(plan_unresolved)
            progress.stage = "reporting"
            save_progress(state, progress)
            return {**state, "ready_to_finalize": True, "quality_status": progress.quality_status,
                    "lifecycle_status": progress.lifecycle_status, "stage": progress.stage,
                    "agent_actions": []}
        progress.analysis_units = []
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
        comparison_action.status = "accepted"
        disposition, closure_units = _review_disposition(result)
        progress.quality_status = "PASS" if disposition == "pass" else "UNRESOLVED"
        if disposition == "finding":
            progress.degradations.append({
                "kind": "source_first_review_finding",
                "record_ids": [
                    record.record_id
                    for record in result.records
                    if record.kind == "review_decision"
                ],
            })
        if disposition != "finding" or not closure_units:
            progress.stage = "reporting"
            save_progress(state, progress)
            return {**state, "ready_to_finalize": True, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": []}

        # Closure scheduling is intentionally explicit: only unit IDs named by
        # the comparison record can be continued, and unknown IDs are retained
        # as an unresolved report item rather than guessed.
        known_analysis = {
            action.action_id.rsplit(":", 1)[-1]: action
            for action in progress.actions.values()
            if action.role == "analysis"
        }
        unknown = [unit_id for unit_id in closure_units if unit_id not in known_analysis]
        if unknown:
            progress.degradations.append({
                "kind": "unknown_closure_unit",
                "message": f"Reviewer 指定了未知 closure 单元：{unknown}",
            })
        for unit_id in closure_units:
            origin = known_analysis.get(unit_id)
            if origin is None or not origin.task_id:
                continue
            original_task = read_json(Path(origin.task_path))
            original_result_path = original_task.get("result_path")
            if not isinstance(original_result_path, str):
                continue
            closure_action_id = f"{state['run_id']}:closure:{unit_id}"
            closure_task_path = source_first_task_path(state, "closure", _safe_key(unit_id))
            closure_result_path = source_first_result_path(state, "closure", _safe_key(unit_id))
            original = read_result(Path(original_result_path))
            closure_task = {
                **original_task,
                "task_type": "source_first_closure",
                "action_id": closure_action_id,
                "original_task_path": origin.task_path,
                "original_result_path": original_result_path,
                "base_revision": original.revision,
                "current_revision": original.revision,
                "result_path": str(closure_result_path),
            }
            write_json(closure_task_path, closure_task)
            write_json(closure_result_path, original.model_copy(update={
                "binding": SourceBinding(
                    data_root=str(Path(state["data_root"]).resolve()),
                    run_id=state["run_id"],
                    action_id=closure_action_id,
                    task_id="pending",
                )
            }).model_dump(mode="json"))
            add_action(progress, ActionState(
                action_id=closure_action_id,
                action="continue_agent",
                role="closure",
                stage="targeted_closure",
                task_path=str(closure_task_path),
                task_id=origin.task_id,
            ))
        if not any(action.role == "closure" and action.status == "pending" for action in progress.actions.values()):
            progress.stage = "reporting"
            progress.quality_status = "UNRESOLVED"
            save_progress(state, progress)
            return {**state, "ready_to_finalize": True, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": []}
        progress.stage = "closing"
        save_progress(state, progress)
        return {**state, "lifecycle_status": progress.lifecycle_status, "stage": progress.stage, "agent_actions": [
            action.model_dump(mode="json") for action in progress.actions.values() if action.status == "pending"
        ]}
    if progress.stage == "closing":
        for action in current:
            if action.role == "closure":
                action.status = "accepted"
                progress.completed_closure_units.append(action.action_id.rsplit(":", 1)[-1])
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
