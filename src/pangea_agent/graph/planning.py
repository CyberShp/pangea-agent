from __future__ import annotations

from pangea_agent.models.analysis import AnalysisUnit, PlanningResult, PlanningTask


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
        if unknown_assets or unknown_coverage or unknown_mechanisms:
            raise ValueError(
                "规划引用了未知输入："
                f"assets={sorted(unknown_assets)} "
                f"coverage={sorted(unknown_coverage)} "
                f"mechanisms={sorted(unknown_mechanisms)}"
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
