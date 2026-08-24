from __future__ import annotations

from pathlib import Path

from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.source_languages import analysis_language_for_path


TARGET_UNIT_LINES = 3000
MAX_UNIT_LINES = 5000
MAX_UNIT_FUNCTIONS = 140
_STRONG_CONTEXT_REASONS = (
    "companion_source",
    "declared_definition:",
    "direct_callee:",
    "direct_inline_dependency:",
    "function_pointer_implementation:",
)


def _unit_languages(paths: list[str]) -> list[str]:
    languages = {
        language
        for path in paths
        if (language := analysis_language_for_path(Path(path))) is not None
    }
    return sorted(languages)


def _unit_frameworks(repo_id: str, paths: list[str], inventory: dict) -> list[str]:
    owned = set(paths)
    return sorted({
        framework
        for item in inventory.get("files", [])
        if item.get("repo_id") == repo_id and item.get("path") in owned
        for framework in item.get("frameworks", [])
    })


def _workload(repo_id: str, paths: list[str], inventory: dict) -> tuple[int, int]:
    owned = set(paths)
    lines = 0
    functions = 0
    for item in inventory.get("files", []):
        if item.get("repo_id") != repo_id or item.get("path") not in owned:
            continue
        lines += int(item.get("line_count") or 0)
        functions += len(item.get("functions", []))
    return lines, functions


def _semantic_family(paths: list[str]) -> str | None:
    families = set()
    for value in paths:
        stem = Path(value).stem.lower()
        parts = [part for part in stem.split("_") if part and part != "nvme"]
        if parts:
            families.add(parts[0])
    return next(iter(families)) if len(families) == 1 else None


def _cluster_groups(groups: list[dict], inventory: dict, context_files: list[dict]) -> list[dict]:
    """Merge strongly related, bounded file families into deterministic units."""

    clustered: list[dict] = []
    reasons = {
        (item.get("repo_id"), item.get("path")): str(item.get("reason", ""))
        for item in context_files
        if isinstance(item, dict)
    }
    for repo_id in sorted({group["repo_id"] for group in groups}):
        repo_groups = [group for group in groups if group["repo_id"] == repo_id]
        owners = {
            path: index
            for index, group in enumerate(repo_groups)
            for path in group["code_paths"]
        }
        parent = list(range(len(repo_groups)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def members(root: int) -> list[int]:
            return [index for index in range(len(repo_groups)) if find(index) == root]

        edges: dict[tuple[int, int], int] = {}
        neighbors: dict[int, set[int]] = {}
        for left, group in enumerate(repo_groups):
            for path in group["context_paths"]:
                right = owners.get(path)
                if right is not None and left != right:
                    neighbors.setdefault(left, set()).add(right)
                    neighbors.setdefault(right, set()).add(left)
        for left, group in enumerate(repo_groups):
            for path in group["context_paths"]:
                right = owners.get(path)
                reason = reasons.get((repo_id, path), "")
                if right is None or left == right or not reason.startswith(_STRONG_CONTEXT_REASONS):
                    continue
                if len(neighbors.get(left, set())) > 4 or len(neighbors.get(right, set())) > 4:
                    continue
                edge = tuple(sorted((left, right)))
                weight = 3 if reason.startswith(("declared_definition:", "direct_callee:")) else 2
                edges[edge] = max(edges.get(edge, 0), weight)
        for left in range(len(repo_groups)):
            left_family = _semantic_family(repo_groups[left]["code_paths"])
            if left_family is None:
                continue
            for right in range(left + 1, len(repo_groups)):
                if _semantic_family(repo_groups[right]["code_paths"]) == left_family:
                    edges[(left, right)] = max(edges.get((left, right), 0), 1)

        for (left, right), weight in sorted(
            edges.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                continue
            candidate_indexes = members(left_root) + members(right_root)
            candidate_paths = sorted({
                path
                for index in candidate_indexes
                for path in repo_groups[index]["code_paths"]
            })
            left_paths = sorted({
                path for index in members(left_root) for path in repo_groups[index]["code_paths"]
            })
            right_paths = sorted({
                path for index in members(right_root) for path in repo_groups[index]["code_paths"]
            })
            left_lines, _ = _workload(repo_id, left_paths, inventory)
            right_lines, _ = _workload(repo_id, right_paths, inventory)
            line_count, function_count = _workload(repo_id, candidate_paths, inventory)
            if (
                (left_lines >= TARGET_UNIT_LINES and right_lines >= TARGET_UNIT_LINES)
                or (
                    line_count > TARGET_UNIT_LINES
                    and _semantic_family(candidate_paths) is None
                )
                or line_count > MAX_UNIT_LINES
                or function_count > MAX_UNIT_FUNCTIONS
            ):
                continue
            parent[right_root] = left_root

        roots = []
        for index in range(len(repo_groups)):
            root = find(index)
            if root not in roots:
                roots.append(root)
        for root in roots:
            indexes = members(root)
            code_paths = sorted({
                path for index in indexes for path in repo_groups[index]["code_paths"]
            })
            context_paths = sorted({
                path for index in indexes for path in repo_groups[index]["context_paths"]
            } - set(code_paths))
            clustered.append({
                "repo_id": repo_id,
                "requested_scope": sorted({
                    scope for index in indexes for scope in repo_groups[index]["requested_scope"]
                }),
                "code_paths": code_paths,
                "context_paths": context_paths,
            })
    return clustered


def make_analysis_units(state: PangeaState) -> PangeaState:
    """Create coarse semantic units from source scope.

    This is intentionally deterministic. LLM-based planning can be added later,
    but schema remains the source of truth.
    """

    units = []
    expansion = state.get("scope_expansion", {})
    expansion_groups = _cluster_groups(
        expansion.get("groups", []),
        state.get("inventory", {}),
        expansion.get("context_files", []),
    )
    inventory = state.get("inventory", {})
    requested_focus = state.get("task_contract", {}).get("focus", [])
    for group in expansion_groups:
        if group["code_paths"]:
            requested = ", ".join(group["requested_scope"])
            languages = _unit_languages(group["code_paths"])
            units.append({
                "unit_id": f"U{len(units):02d}",
                "repo_id": group["repo_id"],
                "title": f"{group['repo_id']} 源码范围 {requested}",
                "source_scope": group["code_paths"],
                "context_scope": group["context_paths"],
                "focus": list(dict.fromkeys([
                    *requested_focus,
                    "code_map", "flows", "branches", "risks", "test_cases",
                ])),
                "languages": languages or ["c_cpp"],
                "frameworks": _unit_frameworks(
                    group["repo_id"],
                    [*group["code_paths"], *group["context_paths"]],
                    inventory,
                ),
                "dfx": [
                    "功能与状态",
                    "资源与规格",
                    "性能与压力",
                    "并发与异常",
                    "升级与兼容",
                    "可靠性与一致性",
                ],
            })
    return {**state, "analysis_units": units}
