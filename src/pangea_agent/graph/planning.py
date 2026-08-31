from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json
from pangea_agent.models.analysis import (
    AnalysisUnit,
    PlanningResult,
    PlanningResultV2,
    PlanningTask,
    ProposedUnit,
    ProposedUnitV2,
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
        identifiers.update(
            item.methodology_id for item in catalog.builtin_methodologies
        )
    return identifiers


def planning_result_model(task: PlanningTask):
    if task.result_contract_version == "2.0":
        return PlanningResultV2
    return PlanningResult


def normalize_planning_result(
    task: PlanningTask,
    raw_result: Any,
    warnings: list[str] | None = None,
) -> PlanningResult | PlanningResultV2:
    """Normalize non-essential Planning drift before strict model validation."""

    model = planning_result_model(task)
    if not isinstance(raw_result, Mapping):
        return model.model_validate(raw_result)

    advisory = warnings if warnings is not None else []
    allowed_result_fields = set(model.model_fields)
    ignored_result_fields = sorted(set(raw_result) - allowed_result_fields)
    if ignored_result_fields:
        advisory.append(f"Planning 忽略额外字段：{ignored_result_fields}")
    payload = {
        key: value for key, value in raw_result.items()
        if key in allowed_result_fields
    }
    expected_version = task.result_contract_version
    if payload.get("schema_version") != expected_version:
        if "schema_version" in payload:
            advisory.append(
                "Planning 按 task 纠正 schema_version："
                f"{payload['schema_version']} -> {expected_version}"
            )
        payload["schema_version"] = expected_version
    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
        payload["summary"] = "按冻结源码归属生成分析单元"
        advisory.append("Planning 补充缺失的规划摘要")
    if "unresolved" in payload:
        unresolved = payload["unresolved"]
        if not isinstance(unresolved, list):
            payload["unresolved"] = []
            advisory.append("Planning 忽略不可读取的 unresolved")
        else:
            normalized_unresolved = [item for item in unresolved if isinstance(item, str)]
            if len(normalized_unresolved) != len(unresolved):
                advisory.append("Planning 忽略 unresolved 中的非文本项")
            payload["unresolved"] = normalized_unresolved

    unit_model = ProposedUnitV2 if expected_version == "2.0" else ProposedUnit
    raw_units = payload.get("units")
    if expected_version == "2.0" and not isinstance(raw_units, list):
        raw_units = []
    if isinstance(raw_units, list):
        normalized_units = []
        seen_unit_keys: set[str] = set()
        ownership = payload.get("source_ownership")
        repositories_by_unit: dict[str, set[str]] = {}
        if expected_version == "2.0" and isinstance(ownership, Mapping):
            for ownership_key, unit_key in ownership.items():
                if not isinstance(unit_key, str) or not isinstance(ownership_key, str):
                    continue
                repo_id, separator, _ = ownership_key.partition(":")
                if separator and repo_id:
                    repositories_by_unit.setdefault(unit_key, set()).add(repo_id)
        for index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, Mapping):
                advisory.append(f"Planning 忽略不可读取的 unit[{index}]")
                continue
            allowed_unit_fields = set(unit_model.model_fields)
            ignored_unit_fields = sorted(set(raw_unit) - allowed_unit_fields)
            if ignored_unit_fields:
                advisory.append(
                    f"Planning unit[{index}] 忽略额外字段：{ignored_unit_fields}"
                )
            unit = {
                key: value for key, value in raw_unit.items()
                if key in allowed_unit_fields
            }
            for field in (
                "context_scope",
                "asset_item_ids",
                "coverage_ids",
                "mechanism_ids",
                "methodology_ids",
            ):
                if field not in unit:
                    continue
                values = unit[field]
                if not isinstance(values, list):
                    unit[field] = []
                    advisory.append(
                        f"Planning unit[{index}] 忽略不可读取的 {field}"
                    )
                    continue
                normalized_values = [
                    value for value in values if isinstance(value, str)
                ]
                if len(normalized_values) != len(values):
                    advisory.append(
                        f"Planning unit[{index}] 忽略 {field} 中的非文本项"
                    )
                unit[field] = normalized_values
            reasons = unit.get("methodology_selection_reasons")
            if reasons is not None:
                if not isinstance(reasons, Mapping):
                    unit["methodology_selection_reasons"] = {}
                    advisory.append(
                        f"Planning unit[{index}] 忽略不可读取的方法论选择依据"
                    )
                else:
                    unit["methodology_selection_reasons"] = {
                        key: value
                        for key, value in reasons.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
            if expected_version == "2.0":
                unit_key = unit.get("unit_key")
                if not isinstance(unit_key, str) or not unit_key:
                    advisory.append(f"Planning 忽略没有 unit_key 的 unit[{index}]")
                    continue
                if unit_key in seen_unit_keys:
                    advisory.append(f"Planning 忽略重复单元定义：{unit_key}")
                    continue
                seen_unit_keys.add(unit_key)
                owned_repositories = repositories_by_unit.get(unit_key, set())
                if (
                    (not isinstance(unit.get("repo_id"), str) or not unit["repo_id"].strip())
                    and len(owned_repositories) == 1
                ):
                    unit["repo_id"] = next(iter(owned_repositories))
                    advisory.append(f"Planning 为单元 {unit_key} 补充源码仓库")
                if not isinstance(unit.get("title"), str) or not unit["title"].strip():
                    unit["title"] = unit_key
                    advisory.append(f"Planning 为单元 {unit_key} 补充标题")
                if not isinstance(unit.get("rationale"), str) or not unit["rationale"].strip():
                    unit["rationale"] = "按源码唯一归属形成分析单元"
                    advisory.append(f"Planning 为单元 {unit_key} 补充划分说明")
            normalized_units.append(unit)
        if expected_version == "2.0":
            for unit_key in sorted(repositories_by_unit):
                if not unit_key or unit_key in seen_unit_keys:
                    continue
                owned_repositories = repositories_by_unit[unit_key]
                if len(owned_repositories) != 1:
                    continue
                normalized_units.append({
                    "unit_key": unit_key,
                    "repo_id": next(iter(owned_repositories)),
                    "title": unit_key,
                    "rationale": "按源码唯一归属补全分析单元定义",
                })
                advisory.append(f"Planning 按源码归属补全单元定义：{unit_key}")
        payload["units"] = normalized_units

    return model.model_validate(payload)


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
            advisory.append(f"规划忽略不可读取的上下文：{unknown_context}")
        context_scope = [
            path
            for path in proposed.context_scope
            if (proposed.repo_id, path) in files
            and (proposed.repo_id, path) not in source_keys
        ]
        removed_source_context = sorted(
            set(proposed.context_scope) - set(context_scope)
        )
        if removed_source_context and not unknown_context:
            advisory.append(
                f"规划移除与源码归属重复的上下文：{removed_source_context}"
            )
        misplaced_mechanisms = [
            item_id for item_id in proposed.asset_item_ids
            if item_id in known_mechanisms
        ]
        misplaced_assets = [
            item_id for item_id in proposed.mechanism_ids
            if item_id in known_assets
        ]
        asset_item_ids = list(dict.fromkeys([
            *(item_id for item_id in proposed.asset_item_ids if item_id in known_assets),
            *misplaced_assets,
        ]))
        mechanism_ids = list(dict.fromkeys([
            *(item_id for item_id in proposed.mechanism_ids if item_id in known_mechanisms),
            *misplaced_mechanisms,
        ]))
        coverage_ids = []
        for coverage_id in dict.fromkeys(proposed.coverage_ids):
            if coverage_id not in known_coverage:
                continue
            if coverage_id in coverage_owners:
                advisory.append(
                    f"Coverage 缺口重复分配，保留首次归属：{coverage_id} "
                    f"(U{coverage_owners[coverage_id]:02d}, U{index:02d})"
                )
                continue
            coverage_owners[coverage_id] = index
            coverage_ids.append(coverage_id)
        methodology_ids = list(dict.fromkeys(
            item_id for item_id in proposed.methodology_ids
            if item_id in known_methodologies
        ))
        ignored_inputs = {
            "assets": sorted(
                set(proposed.asset_item_ids) - known_assets - known_mechanisms
            ),
            "coverage": sorted(set(proposed.coverage_ids) - known_coverage),
            "mechanisms": sorted(
                set(proposed.mechanism_ids) - known_mechanisms - known_assets
            ),
            "methodologies": sorted(
                set(proposed.methodology_ids) - known_methodologies
            ),
        }
        if misplaced_mechanisms or misplaced_assets:
            advisory.append(
                f"单元 U{index:02d} 按冻结资产类型归位输入："
                f"mechanisms={misplaced_mechanisms} assets={misplaced_assets}"
            )
        if any(ignored_inputs.values()):
            advisory.append(
                f"单元 U{index:02d} 忽略冻结输入中不存在的编号："
                f"{ignored_inputs}"
            )
        selected_methodologies = set(methodology_ids)
        recorded_reasons = set(proposed.methodology_selection_reasons)
        missing_reasons = selected_methodologies - recorded_reasons
        extra_reasons = recorded_reasons - selected_methodologies
        if missing_reasons:
            advisory.append(
                f"单元 U{index:02d} 未记录方法论选择依据："
                f"{sorted(missing_reasons)}"
            )
        if extra_reasons:
            advisory.append(
                f"单元 U{index:02d} 记录了未选择方法论的依据："
                f"{sorted(extra_reasons)}"
            )
        line_count = sum(files[key].get("line_count", 0) for key in source_keys)
        function_count = sum(len(files[key].get("functions", [])) for key in source_keys)
        if line_count > task.max_unit_lines or function_count > task.max_unit_functions:
            advisory.append(
                f"单元 U{index:02d} 超过工作量建议上限："
                f"lines={line_count}/{task.max_unit_lines}, "
                f"functions={function_count}/{task.max_unit_functions}"
            )
        normalized = proposed.model_dump(mode="json")
        normalized.update({
            "context_scope": context_scope,
            "asset_item_ids": asset_item_ids,
            "coverage_ids": coverage_ids,
            "mechanism_ids": mechanism_ids,
            "methodology_ids": methodology_ids,
            "methodology_selection_reasons": {
                methodology_id: reason
                for methodology_id, reason
                in proposed.methodology_selection_reasons.items()
                if methodology_id in selected_methodologies
            },
        })
        units.append(AnalysisUnit(
            **normalized,
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
    gaps_by_id = {
        item["coverage_id"]: item
        for item in coverage_gaps
        if item.get("coverage_id")
    }
    for coverage_id in sorted(known_coverage - set(coverage_owners)):
        matches = gaps_by_id[coverage_id].get("matches", [])
        match = matches[0] if len(matches) == 1 else None
        source_key = (
            (match.get("repo_id"), match.get("path"))
            if isinstance(match, Mapping)
            else None
        )
        owner = owners.get(source_key) if source_key is not None else None
        if owner is None:
            advisory.append(f"Coverage 缺口无法自动归属：{coverage_id}")
            continue
        units[owner].coverage_ids.append(coverage_id)
        coverage_owners[coverage_id] = owner
        advisory.append(
            f"Coverage 缺口按唯一匹配源码自动归属："
            f"{coverage_id} -> U{owner:02d}"
        )
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
    missing_keys = expected_keys - actual_keys
    if missing_keys:
        raise ValueError(
            "源码归属清单缺少冻结源码："
            f"missing={sorted(missing_keys)}"
        )
    extra_keys = actual_keys - expected_keys
    if extra_keys and warnings is not None:
        warnings.append(f"规划忽略冻结范围外的源码归属：{sorted(extra_keys)}")
    ownership = {
        key: result.source_ownership[key]
        for key in expected_keys
    }
    unassigned_keys = sorted(
        key
        for key, unit_key in ownership.items()
        if not unit_key or unit_key == "<unit_key>"
    )
    if unassigned_keys:
        raise ValueError(
            "源码归属清单仍有未分配项："
            f"{unassigned_keys}"
        )

    definitions = {}
    for unit in result.units:
        if unit.unit_key in definitions:
            if warnings is not None:
                warnings.append(f"规划忽略重复单元定义：{unit.unit_key}")
            continue
        definitions[unit.unit_key] = unit
    unknown_units = set(ownership.values()) - set(definitions)
    for unit_key in sorted(unknown_units):
        owned_repositories = {
            requested_keys[key][0]
            for key, owner_key in ownership.items()
            if owner_key == unit_key
        }
        if len(owned_repositories) != 1:
            raise ValueError(
                f"规划单元 {unit_key} 跨越多个源码仓："
                f"{sorted(owned_repositories)}"
            )
        definitions[unit_key] = ProposedUnitV2(
            unit_key=unit_key,
            repo_id=next(iter(owned_repositories)),
            title=unit_key,
            rationale="按源码唯一归属补全分析单元定义",
        )
        if warnings is not None:
            warnings.append(f"规划按源码归属补全单元定义：{unit_key}")

    assignments: dict[str, list[tuple[str, str]]] = {
        unit_key: [] for unit_key in definitions
    }
    for ownership_key, unit_key in ownership.items():
        assignments[unit_key].append(requested_keys[ownership_key])

    proposed_units: list[ProposedUnit] = []
    for definition in definitions.values():
        owned = assignments[definition.unit_key]
        if not owned:
            if warnings is not None:
                warnings.append(
                    f"规划忽略没有源码归属的空单元：{definition.unit_key}"
                )
            continue
        owned_repositories = {repo_id for repo_id, _ in owned}
        if len(owned_repositories) != 1:
            raise ValueError(
                f"规划单元 {definition.unit_key} 跨越多个源码仓："
                f"{sorted(owned_repositories)}"
            )
        repo_id = next(iter(owned_repositories))
        if repo_id != definition.repo_id and warnings is not None:
            warnings.append(
                f"规划按源码归属纠正单元仓库："
                f"{definition.unit_key} {definition.repo_id} -> {repo_id}"
            )
        owned_paths = {
            path for owned_repo_id, path in owned if owned_repo_id == repo_id
        }
        source_scope = [
            path
            for requested_repo_id, path in requested_order
            if requested_repo_id == repo_id and path in owned_paths
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
            repo_id=repo_id,
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
