from __future__ import annotations

from pathlib import Path

from pangea_agent.agent_io import read_json
from pangea_agent.models.analysis import (
    AnalysisUnit,
    PlanningResult,
    PlanningResultV2,
    PlanningTask,
    ProposedUnit,
)
from pangea_agent.models.methodology import FrozenMethodologyCatalog


def _known_methodology_ids(task: PlanningTask) -> set[str]:
    identifiers = {Path(item).stem for item in task.methodology_paths}
    if task.methodology_catalog_path:
        catalog = FrozenMethodologyCatalog.model_validate(
            read_json(Path(task.methodology_catalog_path))
        )
        identifiers.update(
            item.methodology_id for item in catalog.enabled_user_methodologies
        )
    return identifiers


def planning_result_model(task: PlanningTask):
    if task.result_contract_version == "2.0":
        return PlanningResultV2
    return PlanningResult


def accept_plan(
    task: PlanningTask,
    result: PlanningResult,
    compact_metadata: dict,
    asset_inputs: dict,
    coverage_gaps: list[dict],
    warnings: list[str] | None = None,
) -> list[AnalysisUnit]:
    advisory = warnings if warnings is not None else []
    files = {
        (item["repo_id"], item["path"]): item
        for item in compact_metadata.get("files", [])
    }
    requested = {
        (item["repo_id"], item["path"])
        for item in compact_metadata.get("owned_source_paths", [])
    }
    owners: dict[tuple[str, str], int] = {}
    known_assets = {
        item_id
        for item_id, item in asset_inputs.items()
        if item.get("item_type") != "historical_defect"
    }
    known_coverage = {item["coverage_id"] for item in coverage_gaps}
    known_mechanisms = {
        item_id
        for item_id, item in asset_inputs.items()
        if item.get("item_type") == "historical_defect"
    }
    known_methodologies = _known_methodology_ids(task)
    units: list[AnalysisUnit] = []
    coverage_owners: dict[str, int] = {}
    for index, proposed in enumerate(result.units):
        source_keys = [(proposed.repo_id, path) for path in proposed.source_scope]
        unknown_sources = [key for key in source_keys if key not in files]
        if unknown_sources:
            raise ValueError(f"规划引用了未知源码：{unknown_sources}")
        out_of_scope_sources = [key for key in source_keys if key not in requested]
        if out_of_scope_sources:
            raise ValueError(
                "规划把请求范围外文件提升为源码所有权："
                + ", ".join(
                    f"{repo_id}:{path}"
                    for repo_id, path in sorted(out_of_scope_sources)
                )
            )
        for key in source_keys:
            if key in owners:
                raise ValueError(
                    f"源码被多个单元拥有：{key[0]}:{key[1]} "
                    f"(U{owners[key]:02d}, U{index:02d})"
                )
            owners[key] = index
        context_keys = [(proposed.repo_id, path) for path in proposed.context_scope]
        unknown_context = [key for key in context_keys if key not in files]
        if unknown_context:
            raise ValueError(f"规划引用了未知上下文：{unknown_context}")
        if set(source_keys) & set(context_keys):
            raise ValueError("同一文件不能同时属于 source_scope 和 context_scope")
        unknown_assets = set(proposed.asset_item_ids) - known_assets
        unknown_coverage = set(proposed.coverage_ids) - known_coverage
        unknown_mechanisms = set(proposed.mechanism_ids) - known_mechanisms
        unknown_methodologies = (
            set(proposed.methodology_ids) - known_methodologies
        )
        if (
            unknown_assets
            or unknown_coverage
            or unknown_mechanisms
            or unknown_methodologies
        ):
            raise ValueError(
                "规划引用了未知输入："
                f"assets={sorted(unknown_assets)} "
                f"coverage={sorted(unknown_coverage)} "
                f"mechanisms={sorted(unknown_mechanisms)} "
                f"methodologies={sorted(unknown_methodologies)}"
            )
        for coverage_id in proposed.coverage_ids:
            if coverage_id in coverage_owners:
                advisory.append(
                    f"Coverage 缺口被多个单元处理：{coverage_id} "
                    f"(U{coverage_owners[coverage_id]:02d}, U{index:02d})"
                )
            else:
                coverage_owners[coverage_id] = index
        line_count = sum(files[key].get("line_count", 0) for key in source_keys)
        function_count = sum(len(files[key].get("functions", [])) for key in source_keys)
        if line_count > task.max_unit_lines or function_count > task.max_unit_functions:
            advisory.append(
                f"单元 U{index:02d} 超过工作量建议上限："
                f"lines={line_count}/{task.max_unit_lines}, "
                f"functions={function_count}/{task.max_unit_functions}"
            )
        units.append(AnalysisUnit(
            **proposed.model_dump(mode="json"),
            unit_id=f"U{index:02d}",
            line_count=line_count,
            function_count=function_count,
        ))
    missing = requested - set(owners)
    if missing:
        raise ValueError(
            "规划没有覆盖全部源码："
            + ", ".join(f"{repo_id}:{path}" for repo_id, path in sorted(missing))
        )
    missing_coverage = known_coverage - set(coverage_owners)
    if missing_coverage:
        advisory.append(f"规划没有分配 Coverage 缺口：{sorted(missing_coverage)}")
    return units


def accept_plan_v2(
    task: PlanningTask,
    result: PlanningResultV2,
    compact_metadata: dict,
    asset_inputs: dict,
    coverage_gaps: list[dict],
    warnings: list[str] | None = None,
) -> list[AnalysisUnit]:
    requested_order = [
        (item["repo_id"], item["path"])
        for item in compact_metadata.get("owned_source_paths", [])
    ]
    requested_keys = {
        f"{repo_id}:{path}": (repo_id, path)
        for repo_id, path in requested_order
    }
    actual_keys = set(result.source_ownership)
    expected_keys = set(requested_keys)
    if actual_keys != expected_keys:
        raise ValueError(
            "源码归属清单与请求范围不一致："
            f"missing={sorted(expected_keys - actual_keys)} "
            f"extra={sorted(actual_keys - expected_keys)}"
        )

    definitions = {unit.unit_key: unit for unit in result.units}
    if len(definitions) != len(result.units):
        raise ValueError("规划 units[].unit_key 包含重复值")
    unknown_units = set(result.source_ownership.values()) - set(definitions)
    if unknown_units:
        raise ValueError(f"源码归属引用了未知 unit_key：{sorted(unknown_units)}")

    assignments: dict[str, list[tuple[str, str]]] = {
        unit_key: [] for unit_key in definitions
    }
    for ownership_key, unit_key in result.source_ownership.items():
        assignments[unit_key].append(requested_keys[ownership_key])

    proposed_units: list[ProposedUnit] = []
    for definition in result.units:
        owned = assignments[definition.unit_key]
        if not owned:
            if warnings is not None:
                warnings.append(
                    f"规划忽略没有源码归属的空单元：{definition.unit_key}"
                )
            continue
        mismatched_repositories = {
            repo_id for repo_id, _ in owned if repo_id != definition.repo_id
        }
        if mismatched_repositories:
            raise ValueError(
                f"规划单元 {definition.unit_key} 的 repo_id 与源码归属不一致："
                f"unit_repo={definition.repo_id} source_repos={sorted(mismatched_repositories)}"
            )
        owned_paths = {
            path for repo_id, path in owned if repo_id == definition.repo_id
        }
        source_scope = [
            path
            for repo_id, path in requested_order
            if repo_id == definition.repo_id and path in owned_paths
        ]
        context_scope = [
            path for path in definition.context_scope if path not in owned_paths
        ]
        removed_context = sorted(set(definition.context_scope) - set(context_scope))
        if removed_context and warnings is not None:
            warnings.append(
                f"规划移除与源码归属重复的上下文："
                f"{definition.unit_key}={removed_context}"
            )
        proposed_units.append(ProposedUnit(
            repo_id=definition.repo_id,
            title=definition.title,
            source_scope=source_scope,
            context_scope=context_scope,
            rationale=definition.rationale,
            asset_item_ids=definition.asset_item_ids,
            coverage_ids=definition.coverage_ids,
            mechanism_ids=definition.mechanism_ids,
            methodology_ids=definition.methodology_ids,
            methodology_selection_reasons=(
                definition.methodology_selection_reasons
            ),
        ))

    legacy_shape = PlanningResult(
        summary=result.summary,
        units=proposed_units,
        unresolved=result.unresolved,
    )
    return accept_plan(
        task,
        legacy_shape,
        compact_metadata,
        asset_inputs,
        coverage_gaps,
        warnings,
    )


def accept_planning_result(
    task: PlanningTask,
    result: PlanningResult | PlanningResultV2,
    compact_metadata: dict,
    asset_inputs: dict,
    coverage_gaps: list[dict],
    warnings: list[str] | None = None,
) -> list[AnalysisUnit]:
    if isinstance(result, PlanningResultV2):
        return accept_plan_v2(
            task,
            result,
            compact_metadata,
            asset_inputs,
            coverage_gaps,
            warnings,
        )
    return accept_plan(
        task,
        result,
        compact_metadata,
        asset_inputs,
        coverage_gaps,
        warnings,
    )
