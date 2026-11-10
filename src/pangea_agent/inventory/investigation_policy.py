"""Freeze the explicit selected-repository read boundary, separately from ownership."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.workflow_store import run_directory

POLICY = "repository-local-v1"
INSTRUCTIONS = (
    "本策略优先于既有rubric中的冻结目录读取限制；不改变主责、结果归属或审核策略。"
    "起始范围用于确定分析目标和单元主责；可通过绑定源码工具直接追查本次选中代码仓内的调用者、"
    "被调用者、类型和配置，不申请逐文件扩展。allowed_paths/owned_regions仍描述原始主责或上下文，"
    "不代表此策略下的全部可调查文件。补充调查文件只用于相关理解和证据，不自动扩大规划、覆盖义务或"
    "产生新分析单元。不得枚举全仓代替围绕目标调查；仓外实现如实标注未调查，不猜造。"
    "分析期间按静态代码处理，工具补存后引用保存的代码，不刷新版本。搜索有过滤、截断或失败时，"
    "无命中只代表本次搜索结果，不证明整个项目不存在。当前流程不启用Reviewer。"
)


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"源码调查路径越过边界：{path}")
    return resolved


def freeze_investigation(state: dict, repositories: list[dict], incremental: dict | None) -> None:
    contract = state["task_contract"]
    if contract.get("source_investigation_policy") != POLICY:
        return
    run = run_directory(state).resolve()
    selected = set(contract.get("repositories") or [contract.get("repository")])
    roots = repositories
    parent = None
    if incremental:
        # Derivation preserves its parent's explicit policy; it never silently
        # grants a historical run broader access because the program was updated.
        parent_contract = read_json(incremental["root"] / "parent-contract.json")
        if parent_contract.get("source_investigation_policy") != POLICY:
            raise ValueError("增量分析必须沿用父 Run 的源码调查策略")
        if incremental["request"]["mode"] == "supplement":
            from pangea_agent.documents.incremental import _run
            parent = _run(state["data_root"], incremental["request"]["parent_run_id"])
            source = read_json(_inside(parent, parent / "inputs/source-investigation.json"))
            if source.get("policy") != POLICY:
                raise ValueError("父 Run 源码调查策略缺失或损坏")
            roots = source["repositories"]
    # A test-tool reference may be registered as an additional repository by
    # input preparation. It is not a user-selected source investigation root.
    roots = [r for r in roots if r["repo_id"] in selected]
    if {r["repo_id"] for r in roots} != selected:
        raise ValueError("源码调查根与本次选中代码仓不一致")
    payload = {"policy": POLICY, "repositories": [
        {"repo_id": r["repo_id"], "source_root": str(Path(r["source_root"]).resolve())}
        for r in roots
    ]}
    write_json(run / "inputs/source-investigation.json", payload)
    if parent is not None:
        _inherit_supplemental(parent, run, selected)


def _inherit_supplemental(parent: Path, run: Path, selected: set[str]) -> None:
    manifest_path = _inside(parent, parent / "inputs/source-supplemental.json")
    if not manifest_path.is_file():
        return
    manifest = read_json(manifest_path)
    if manifest.get("format_version") != "pangea-source-supplemental-v1":
        raise ValueError("父 Run 补充源码清单格式不受支持")
    files = []
    for item in manifest.get("files", []):
        repo, relative = item["repo_id"], item["path"]
        parsed = PurePosixPath(relative.replace("\\", "/"))
        if repo not in selected or not relative or parsed.is_absolute() or ".." in parsed.parts or ":" in relative:
            raise ValueError("父 Run 补充源码路径越过仓库边界")
        source_root = _inside(parent, parent / "inputs/source" / repo)
        source = _inside(source_root, source_root / str(parsed))
        target_root = _inside(run, run / "inputs/source" / repo)
        target = _inside(target_root, target_root / str(parsed))
        # Validate precisely the bytes being inherited, not a second read of
        # the parent path after validation. Never attach a manifest to differing
        # bytes already frozen in the child Run.
        content = source.read_bytes()
        if (type(item.get("bytes")) is not int or item["bytes"] != len(content)
                or item.get("sha256") != hashlib.sha256(content).hexdigest()):
            raise ValueError(f"父 Run 补充源码完整性校验失败：{repo}/{relative}")
        if target.exists():
            if target.read_bytes() != content:
                raise ValueError(f"子 Run 已保存源码与继承证据不一致：{repo}/{relative}")
        else:
            from .repository_source import atomic_bytes
            atomic_bytes(target, content)
        files.append({**item, "inherited_from_run": parent.name})
    write_json(run / "inputs/source-supplemental.json", {**manifest, "files": files})


def attach_investigation(run: Path, task: dict) -> None:
    path = run / "inputs/source-investigation.json"
    # Read the frozen contract as authority, not the mere existence of a file.
    contract = read_json(run / "inputs/task-contract.json")
    if contract.get("source_investigation_policy") != POLICY:
        return
    payload = read_json(path)
    if payload.get("policy") != POLICY:
        raise ValueError("源码调查策略缺失或损坏")
    task["source_investigation_policy"] = POLICY
    task["source_investigation_instructions"] = INSTRUCTIONS
    task.setdefault("inputs", []).append({
        "input_id": "source_investigation", "path": str(path),
        "label": "本次选中仓库的调查范围；主责仍由起始范围及单元决定",
    })
