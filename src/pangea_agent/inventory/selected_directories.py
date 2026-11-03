"""Deterministic boundaries for new source and tool directory selections."""
from pathlib import Path, PurePosixPath, PureWindowsPath

from pangea_agent.repositories.resolver import resolve_repositories_from_contract


def validate_selected_directories(data_root: str, repository: str, paths: list[str]) -> list[str]:
    if (not isinstance(repository, str) or not repository.strip() or repository in {".", ".."}
            or any(char in repository for char in "/\\\0") or PureWindowsPath(repository).drive):
        raise ValueError("仓库身份无效")
    if not isinstance(paths, list) or not paths:
        raise ValueError("请选择真实存在的非根下级目录")
    root = Path(resolve_repositories_from_contract({"repository": repository}, data_root)[0]["source_root"]).resolve()
    parent = (Path(data_root) / "repositories").resolve()
    if root == parent:
        raise ValueError("仓库不能指向仓库集合根目录")
    normalized = []
    for item in paths:
        if not isinstance(item, str) or not item.strip() or "\0" in item:
            raise ValueError("目录必须是非空相对路径")
        value = item.strip().replace("\\", "/")
        relative = PurePosixPath(value)
        if relative.is_absolute() or PureWindowsPath(value).drive or ".." in relative.parts:
            raise ValueError(f"目录必须位于仓库内，不能使用绝对路径或上级路径：{item}")
        try:
            candidate = (root / value).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"目录不存在或无法读取：{item}") from exc
        if candidate == root or not candidate.is_relative_to(root) or not candidate.is_dir():
            raise ValueError(f"请选择真实存在的非根下级目录：{item}")
        normalized.append(candidate.relative_to(root).as_posix())
    return sorted(set(normalized))


def validate_new_selections(contract: dict, data_root: str) -> None:
    incremental = contract.get("incremental_request")
    if incremental and incremental.get("mode") == "supplement":
        # Only a verified parent frozen snapshot can retain historical root/file scopes.
        from pangea_agent.documents.incremental import _parent
        _, parent, progress, *_ = _parent(data_root, incremental["parent_run_id"])
        if parent.get("run_id") != incremental["parent_run_id"] or progress.get("run_id") != incremental["parent_run_id"]:
            raise ValueError("父 Run 冻结合同或进度身份不一致")
        for field in ("repository", "repositories", "source_scope", "test_tool_sources", "context_scope"):
            if contract.get(field) != parent.get(field):
                raise ValueError(f"定向补充必须沿用父 Run 冻结范围：{field}")
        return
    for repository in contract.get("repositories") or [contract.get("repository")]:
        validate_selected_directories(data_root, repository, contract.get("source_scope"))
    # Changed-files runs inherit frozen tool files, not a fresh tool selection.
    if not incremental:
        for source in contract.get("test_tool_sources") or []:
            validate_selected_directories(data_root, source["repo_id"], source["paths"])
