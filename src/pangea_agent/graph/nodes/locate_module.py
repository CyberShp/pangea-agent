from __future__ import annotations

import re
import shutil
from pathlib import Path
from time import perf_counter

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.run_store import load_progress, save_progress
from pangea_agent.graph.state import PangeaState
from pangea_agent.inventory.scope_expander import expand_analysis_scope

DOCUMENT_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".xlsx"}


def _list_text(values) -> str:
    if not isinstance(values, list) or not values:
        return "- 未记录"
    return "\n".join(f"- {value}" for value in values)


def _historical_issue_markdown(issue_id: str, review: dict) -> str:
    issue = review["issue"]
    evidence = issue.get("evidence", [])
    evidence_text = "\n".join(
        f"- 位置：{item.get('location', '未记录')}\n  摘录：{item.get('excerpt', '未记录')}"
        for item in evidence
        if isinstance(item, dict)
    ) or "- 未记录"
    return "\n".join([
        "# 已确认历史问题",
        "",
        f"- 历史问题 ID：{issue_id}",
        f"- 来源资产 ID：{review.get('asset_id', '未记录')}",
        f"- 人工确认时间：{review.get('reviewed_at', '未记录')}",
        "- 资料角色：历史问题参考，不直接决定当前实现是否仍存在风险，也不直接决定测试通过标准。",
        "",
        f"## {issue.get('title', issue_id)}",
        "",
        "### 现象",
        str(issue.get("symptom", "未记录")),
        "",
        "### 触发条件",
        _list_text(issue.get("trigger_conditions")),
        "",
        "### 影响",
        _list_text(issue.get("impact")),
        "",
        "### 历史根因",
        _list_text(issue.get("root_causes")),
        "",
        "### 历史解决方式",
        _list_text(issue.get("resolutions")),
        "",
        "### 历史验证方式",
        _list_text(issue.get("verification")),
        "",
        "### 限制与缺失信息",
        _list_text([*issue.get("limitations", []), *issue.get("missing_fields", [])]),
        "",
        "### 原始资料证据",
        evidence_text,
        "",
    ])


def _freeze_confirmed_historical_issues(data_root: Path, staging_root: Path) -> None:
    reviews_path = data_root / "asset-catalog" / "historical-issue-reviews.json"
    if not reviews_path.is_file():
        return
    payload = read_json(reviews_path)
    reviews = payload.get("reviews") if isinstance(payload, dict) else None
    if not isinstance(reviews, dict):
        raise ValueError(f"历史问题复核文件缺少 reviews 对象：{reviews_path}")
    destination_root = staging_root / "materials" / "historical-issues"
    for issue_id, review in sorted(reviews.items()):
        if not isinstance(review, dict) or review.get("decision") != "confirmed":
            continue
        if not isinstance(review.get("issue"), dict):
            raise ValueError(f"已确认历史问题缺少 issue 内容：{issue_id}")
        if re.fullmatch(r"[A-Za-z0-9._-]+", issue_id) is None:
            raise ValueError(f"历史问题 ID 不能作为冻结路径：{issue_id}")
        destination = destination_root / f"{issue_id}.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_historical_issue_markdown(issue_id, review), encoding="utf-8")


def _freeze_inputs(state: PangeaState, expansion: dict, run_dir: Path) -> list[dict]:
    paths_by_repo: dict[str, set[str]] = {}
    for group in expansion.get("groups", []):
        paths_by_repo.setdefault(group["repo_id"], set()).update(group.get("code_paths", []))
        paths_by_repo[group["repo_id"]].update(group.get("context_paths", []))

    staging_root = run_dir / "inputs" / ".frozen-staging"
    frozen_root = run_dir / "inputs" / "frozen"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)
    frozen: list[dict] = []
    for repository in state.get("repositories", []):
        repo_id = repository["repo_id"]
        source_root = Path(repository["source_root"]).resolve()
        snapshot_root = staging_root / "source" / repo_id
        for relative in sorted(paths_by_repo.get(repo_id, set())):
            source = (source_root / relative).resolve()
            try:
                source.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(f"源码范围越过仓库边界：{repo_id}:{relative}") from exc
            if not source.is_file():
                raise ValueError(f"冻结源码时文件不存在：{repo_id}:{relative}")
            destination = snapshot_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        frozen.append({
            "repo_id": repo_id,
            "source_root": str(frozen_root / "source" / repo_id),
            "git": repository.get("git", {}),
        })
    for folder in ("inbox", "coverage"):
        source_root = Path(state["data_root"]) / folder
        if not source_root.exists():
            continue
        for source in source_root.rglob("*"):
            if not source.is_file() or source.suffix.lower() not in DOCUMENT_SUFFIXES:
                continue
            destination = staging_root / "materials" / folder / source.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    _freeze_confirmed_historical_issues(Path(state["data_root"]), staging_root)
    if frozen_root.exists():
        shutil.rmtree(frozen_root)
    staging_root.replace(frozen_root)
    return frozen


def locate_module(state: PangeaState) -> PangeaState:
    """Resolve module scope from the task contract."""

    started = perf_counter()
    print("[pangea] locate_module started", flush=True)

    contract = state["task_contract"]
    scope = contract.get("source_scope") or []
    if isinstance(scope, str):
        scope = [scope]
    expansion = expand_analysis_scope(
        state.get("repositories", []),
        list(scope),
        target=str(contract.get("target", "")),
        focus=list(contract.get("focus", [])),
    )
    expanded_scope = [
        path
        for group in expansion["groups"]
        for path in group["code_paths"]
    ]
    result = {
        **state,
        "module_scope": list(dict.fromkeys(expanded_scope)),
        "scope_expansion": expansion,
    }

    run_dir = Path(state["data_root"]) / "runs" / state["run_id"]
    frozen_repositories = _freeze_inputs(state, expansion, run_dir)
    result["repositories"] = frozen_repositories
    write_json(run_dir / "inputs" / "scope-expansion.json", expansion)
    write_json(run_dir / "inputs" / "source-repositories.json", frozen_repositories)
    progress = load_progress(result)
    if progress is None or progress.phase != "PREPARING":
        raise ValueError("locate_module 完成时缺少 PREPARING progress")
    progress.init_step = "SOURCE_READY"
    save_progress(result, progress)

    print(
        f"[pangea] locate_module completed in {perf_counter() - started:.2f}s "
        f"(groups={len(expansion.get('groups', []))}, scope_files={len(result['module_scope'])})",
        flush=True,
    )
    return result
