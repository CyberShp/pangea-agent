from __future__ import annotations

from pangea_agent.models.analysis import AnalysisUnit, PlanningResult, PlanningTask


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _merge_direct_call_units(
    task: PlanningTask,
    units: list[AnalysisUnit],
    files: dict[tuple[str, str], dict],
    owners: dict[tuple[str, str], int],
    requested: set[tuple[str, str]],
) -> list[AnalysisUnit]:
    if len(units) < 2:
        return units

    definitions: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key in requested:
        for function in files[key].get("functions", []):
            definitions.setdefault((key[0], function["symbol"]), []).append(key)

    edges: set[tuple[int, int]] = set()
    for caller in requested:
        for call in files[caller].get("calls", []):
            targets = definitions.get((caller[0], call["symbol"]), [])
            if len(targets) != 1 or targets[0] == caller:
                continue
            left, right = owners[caller], owners[targets[0]]
            if left != right:
                edges.add(tuple(sorted((left, right))))

    parents = list(range(len(units)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def members(parent: int) -> list[int]:
        return [index for index in range(len(units)) if root(index) == parent]

    for left, right in sorted(edges):
        left_root, right_root = root(left), root(right)
        if left_root == right_root:
            continue
        combined = members(left_root) + members(right_root)
        if sum(units[index].line_count for index in combined) > (
            task.merge_direct_call_chain_max_lines
        ):
            continue
        if sum(units[index].function_count for index in combined) > (
            task.merge_direct_call_chain_max_functions
        ):
            continue
        parents[right_root] = left_root

    groups: dict[int, list[AnalysisUnit]] = {}
    for index, unit in enumerate(units):
        groups.setdefault(root(index), []).append(unit)
    if len(groups) == len(units):
        return units

    merged: list[AnalysisUnit] = []
    for group in groups.values():
        source_scope = _unique([
            path for unit in group for path in unit.source_scope
        ])
        source_set = set(source_scope)
        context_scope = [
            path
            for path in _unique([
                path for unit in group for path in unit.context_scope
            ])
            if path not in source_set
        ]
        merged.append(AnalysisUnit(
            repo_id=group[0].repo_id,
            title="、".join(unit.title for unit in group),
            source_scope=source_scope,
            context_scope=context_scope,
            rationale="；".join(unit.rationale for unit in group),
            asset_item_ids=_unique([
                item for unit in group for item in unit.asset_item_ids
            ]),
            coverage_ids=_unique([
                item for unit in group for item in unit.coverage_ids
            ]),
            mechanism_ids=_unique([
                item for unit in group for item in unit.mechanism_ids
            ]),
            unit_id=f"U{len(merged):02d}",
            line_count=sum(unit.line_count for unit in group),
            function_count=sum(unit.function_count for unit in group),
        ))
    return merged


def accept_plan(
    task: PlanningTask,
    result: PlanningResult,
    compact_metadata: dict,
    asset_inputs: dict,
    coverage_gaps: list[dict],
) -> list[AnalysisUnit]:
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
    units: list[AnalysisUnit] = []
    coverage_owners: dict[str, int] = {}
    for index, proposed in enumerate(result.units):
        source_keys = [(proposed.repo_id, path) for path in proposed.source_scope]
        unknown_sources = [key for key in source_keys if key not in files]
        if unknown_sources:
            raise ValueError(f"规划引用了未知源码：{unknown_sources}")
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
        if unknown_assets or unknown_coverage or unknown_mechanisms:
            raise ValueError(
                "规划引用了未知输入："
                f"assets={sorted(unknown_assets)} "
                f"coverage={sorted(unknown_coverage)} "
                f"mechanisms={sorted(unknown_mechanisms)}"
            )
        for coverage_id in proposed.coverage_ids:
            if coverage_id in coverage_owners:
                raise ValueError(
                    f"Coverage 缺口被多个单元处理：{coverage_id} "
                    f"(U{coverage_owners[coverage_id]:02d}, U{index:02d})"
                )
            coverage_owners[coverage_id] = index
        line_count = sum(files[key].get("line_count", 0) for key in source_keys)
        function_count = sum(len(files[key].get("functions", [])) for key in source_keys)
        if line_count > task.max_unit_lines or function_count > task.max_unit_functions:
            raise ValueError(
                f"单元 U{index:02d} 超过工作量上限："
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
        raise ValueError(f"规划没有分配 Coverage 缺口：{sorted(missing_coverage)}")
    return _merge_direct_call_units(task, units, files, owners, requested)
