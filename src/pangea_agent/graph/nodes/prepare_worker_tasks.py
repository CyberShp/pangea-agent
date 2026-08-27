from __future__ import annotations

import re
from pathlib import Path

from pangea_agent.agent_io import write_json
from pangea_agent.documents.coverage import filter_inventory_to_sources
from pangea_agent.graph.actions import MAX_PARALLEL_ACTIONS, agent_action
from pangea_agent.graph.run_store import (
    analysis_result_path,
    analysis_task_path,
    run_directory,
    save_progress,
    worker_result_skeleton,
)
from pangea_agent.graph.semantic_checks import build_runtime_semantic_checks
from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.validation import (
    validate_complete_unit_coverage,
    validate_nonoverlapping_units,
)
from pangea_agent.inventory.source_languages import (
    checkpoint_rubrics,
    semantic_providers,
)
from pangea_agent.models.run import AgentSession, RunProgress
from pangea_agent.models.worker import AnalysisUnit, WorkerTask


_HIGH_IMPACT_ASSERT_RE = re.compile(
    r"\bassert\s*\([^\n]*(?:\bfalse\b|(?:STAILQ|TAILQ|SLIST|LIST)_EMPTY|\bref\s*>\s*0)[^\n]*\)"
)
_ABORT_RE = re.compile(r"\babort\s*\(")
_STATE_ASSERT_RE = re.compile(
    r"\bassert\s*\(\s*(?P<state>[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*)+)\s*==\s*false"
)
_CODE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}
_IMPLEMENTATION_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
_MAP_INSERT_CALL_RE = re.compile(r"\bspdk_sock_map_insert\s*\([^;{}]*\)\s*;", re.DOTALL)
_MAP_RELEASE_CALL_RE = re.compile(r"\bspdk_sock_map_release\s*\([^;{}]*\)\s*;", re.DOTALL)
_DOCA_TASK_ALLOC_RE = re.compile(
    r"\bdoca_[A-Za-z0-9_]*task[A-Za-z0-9_]*alloc_init\s*\("
)
_DOCA_TASK_SUBMIT_RE = re.compile(r"\bdoca_task_submit\s*\(")


def _checkpoint_rubric_paths(unit: AnalysisUnit, inventory: dict) -> list[str]:
    return checkpoint_rubrics(
        unit.languages,
        unit.frameworks,
        repo_id=unit.repo_id,
        source_paths=unit.source_scope,
        inventory=inventory,
    )


def _failure_signal_focus(signal: str) -> str:
    if re.search(r"\bassert\s*\(\s*false\s*\)", signal):
        return (
            "先定位直接支配 assert 的失败条件，再分别重放 Debug 与 Release。受支持模式中的底层"
            "操作若可返回失败，且公开契约或入口没有阻断，Debug 终止必须保留为风险；不能用"
            " assert 后的清理或返回排除，Release 继续核对清理后的最终状态。条件含数值句柄时，"
            "必须从创建函数的失败返回值确认哨兵，并把 0 作为独立边界重放。"
        )
    if re.search(r"(?:STAILQ|TAILQ|SLIST|LIST)_EMPTY", signal):
        return (
            "追踪容器元素的产生、归还和公开移除入口；实现注释或 assert 本身不是调用方契约，"
            "只有公开契约或入口强制检查才能证明该状态不可达。"
        )
    if re.search(r"\bref\s*>\s*0", signal):
        return (
            "按任务提供的每个直接实现写出实际的增加与减少调用序列，并追踪错误日志之后的函数"
            "返回值和上层是否真正绑定对象。只有证明某次 release/decrement 前没有成功 insert/"
            "increment 才能判定失衡；lookup 不增加引用本身不足以证明风险。一个实现的结论不能"
            "覆盖另一个实现。"
        )
    if _ABORT_RE.search(signal):
        return "反向确认公开入口和支持模式是否可达该终止点，并检查终止前已经发生的副作用。"
    return (
        "把断言可达性与重配置后的状态残留拆成两条 failure path。先判断断言本身，再从状态置位"
        "重放 related_state_context 中的 destroy/NULL/setter；即使断言不可达，重配置仍可能独立"
        "造成数据丢失或残留状态。当前分支没有写入者不能证明先前状态不会残留。"
    )


def _related_state_context(relative: str, lines: list[str], signal: str) -> list[str]:
    match = _STATE_ASSERT_RE.search(signal)
    if match is None:
        return []
    state = match.group("state")
    member = re.split(r"->|\.", state)[-1]
    stem = member.split("_has_", 1)[0]
    if stem == member and member.startswith("pending_"):
        stem = member.removeprefix("pending_")
    assignment = re.compile(rf"(?:->|\.){re.escape(member)}\s*=(?!=)")
    assignments: list[str] = []
    destructive_reconfigurations: list[str] = []
    setter_reconfigurations: list[str] = []
    other_reconfigurations: list[str] = []
    for line_number, line in enumerate(lines, 1):
        lowered = line.lower()
        is_assignment = assignment.search(line) is not None
        is_reconfiguration = stem in lowered and any(
            token in lowered
            for token in ("alloc", "destroy", "set_", "enable", "disable", "= null")
        )
        entry = f"{relative}:{line_number}: {line.strip()}"
        if is_assignment:
            assignments.append(entry)
        elif is_reconfiguration:
            if "destroy" in lowered or "= null" in lowered:
                destructive_reconfigurations.append(entry)
            elif "set_" in lowered and "set_field" not in lowered:
                setter_reconfigurations.append(entry)
            else:
                other_reconfigurations.append(entry)

    def lifecycle_slice(entries: list[str]) -> list[str]:
        if len(entries) <= 6:
            return entries
        return [*entries[:3], *entries[-3:]]

    destructive = lifecycle_slice(destructive_reconfigurations)
    setters = lifecycle_slice(setter_reconfigurations)
    others = lifecycle_slice(other_reconfigurations)
    reconfigurations: list[str] = []
    for entry in [*destructive[:3], *setters[:2], *destructive, *setters, *others]:
        if entry not in reconfigurations:
            reconfigurations.append(entry)
        if len(reconfigurations) == 6:
            break
    return [*lifecycle_slice(assignments), *reconfigurations]


def _coverage_context(unit: AnalysisUnit, coverage_report: dict) -> list[dict]:
    source_scopes = [scope.replace("\\", "/").strip("/") or "." for scope in unit.source_scope]
    context: list[dict] = []
    for record in coverage_report.get("matched", []):
        if record.get("coverage_type", "function") != "function" or record.get("count") != 0:
            continue
        match = record["matches"][0]
        path = match["path"].replace("\\", "/")
        if match["repo_id"] != unit.repo_id:
            continue
        if not any(
            scope == "." or path == scope or path.startswith(f"{scope}/")
            for scope in source_scopes
        ):
            continue
        context.append({
            "repo_id": match["repo_id"],
            "path": path,
            "function": record["function"],
            "count": record["count"],
            "line": match.get("line"),
            "module": record.get("module", ""),
            "coverage_type": record.get("coverage_type", "function"),
            "branch_id": record.get("branch_id"),
            "condition": record.get("condition"),
            "true_count": record.get("true_count"),
            "false_count": record.get("false_count"),
        })
    return sorted(
        context,
        key=lambda item: (
            item["path"], item["line"] or 0, item["function"], item.get("branch_id") or ""
        ),
    )


def _allowed_material_paths(source_manifest: dict) -> list[str]:
    return sorted({
        item["path"]
        for item in source_manifest.get("material_catalog", [])
        if isinstance(item, dict)
        and item.get("type") == "material"
        and str(item.get("parse_status", "")).startswith("parsed")
        and item.get("path")
    })


def _source_inventory(unit: AnalysisUnit, inventory: dict) -> dict:
    return filter_inventory_to_sources(
        inventory,
        {(unit.repo_id, path) for path in unit.source_scope},
    )


def _path_coverage_inventory(unit: AnalysisUnit, inventory: dict) -> dict:
    return filter_inventory_to_sources(
        inventory,
        {
            (unit.repo_id, path)
            for path in [*unit.source_scope, *unit.context_scope]
        },
    )


def _failure_signal_context(
    unit: AnalysisUnit,
    repositories: list[dict],
) -> list[dict]:
    repository = next((item for item in repositories if item["repo_id"] == unit.repo_id), None)
    if repository is None:
        return []
    root = Path(repository["source_root"])
    signals: list[dict] = []
    # Context files explain callers and callees, but they are not owned by this
    # Analysis Unit.  Turning their assertions into mandatory semantic checks
    # lets a worker manufacture ownership by adding any source path to the same
    # RiskCard.  Only source_scope may seed a formal failure signal/check.
    for relative in sorted(dict.fromkeys(unit.source_scope)):
        path = root / relative
        if path.suffix.lower() not in _CODE_SUFFIXES or not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_number, line in enumerate(lines, 1):
            match = _HIGH_IMPACT_ASSERT_RE.search(line) or _ABORT_RE.search(line)
            if match:
                signal = line.strip()
                signals.append({
                    "path": relative,
                    "line": line_number,
                    "signal": signal,
                    "analysis_focus": _failure_signal_focus(signal),
                    "related_state_context": _related_state_context(relative, lines, signal),
                })
    return signals


def _prioritize_discovery(unit: AnalysisUnit, checks: list[dict]) -> list[dict]:
    instruction = (
        "在执行本项以及后续 semantic_check_items / failure_signal_context 前，先对 unit.source_scope 全部"
        "冻结源码做一轮独立风险发现。不要把后续清单当作风险候选目录或结论边界；先自行建立入口、"
        "状态、资源、副作用、错误传播、清理与恢复关系，主动寻找预定义检查没有点名、但从真实入口"
        "可达并形成独立最终状态的异常链。每条可信新发现直接新增独立 failure path，path_id 使用"
        " `DISC:<稳定短名>` 并按正常规则关联风险；这些新发现不能塞进本项的同 ID failure path。"
        "完成并记录自由发现后，再执行本项原定检查。"
    )
    if checks:
        first = dict(checks[0])
        first["instruction"] = f"{instruction}{first['instruction']}"
        return [first, *checks[1:]]
    return [{
        "check_id": "DISCOVERY-FIRST",
        "kind": "runtime_semantics",
        "subject_path": unit.source_scope[0],
        "instruction": (
            f"{instruction}当前单元没有其他预定义 semantic check；主 DISCOVERY-FIRST failure path 只记录"
            "自由发现扫描已经完成，linked_risk_ids 保持空，disposition=excluded；没有可信新路径时不为"
            "凑数制造风险。"
        ),
        "context_paths": [unit.source_scope[0]],
    }]


def _semantic_check_items(
    unit: AnalysisUnit,
    repositories: list[dict],
    signals: list[dict],
) -> list[dict]:
    repository = next((item for item in repositories if item["repo_id"] == unit.repo_id), None)
    if repository is None:
        return []
    root = Path(repository["source_root"])
    paths = sorted(dict.fromkeys(unit.source_scope))
    paired_paths: list[str] = []
    for relative in paths:
        path = root / relative
        if path.suffix.lower() not in _IMPLEMENTATION_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _MAP_INSERT_CALL_RE.search(text) and _MAP_RELEASE_CALL_RE.search(text):
            paired_paths.append(relative)

    checks: list[dict] = []
    state_reconfiguration_keys: set[tuple[str, str]] = set()
    for signal_index, signal in enumerate(signals, 1):
        state_match = _STATE_ASSERT_RE.search(signal["signal"])
        if state_match is not None:
            state = state_match.group("state")
            checks.append({
                "check_id": f"SC-{signal_index:02d}-ASSERT",
                "kind": "assertion_reachability",
                "subject_path": signal["path"],
                "instruction": (
                    f"单独判断 {signal['path']}:{signal['line']} 的断言是否能从公开入口到达，"
                    "分别写明 Debug 与 Release 的最终状态；不要用资源重配置结论替代本项。"
                ),
                "context_paths": [signal["path"]],
            })
            reconfiguration_key = (signal["path"], state)
            if signal["related_state_context"] and reconfiguration_key not in state_reconfiguration_keys:
                state_reconfiguration_keys.add(reconfiguration_key)
                checks.append({
                    "check_id": f"SC-{signal_index:02d}-RECONFIG",
                    "kind": "resource_reconfiguration",
                    "subject_path": signal["path"],
                    "instruction": (
                        f"从 {state} 的置位位置重放 related_state_context 中的 destroy、NULL 与 setter，"
                        "单独判断数据丢失、虚假通知或残留状态；即使断言不可达也必须完成本项。"
                    ),
                    "context_paths": [signal["path"]],
                })
                checks.append({
                    "check_id": f"SC-{signal_index:02d}-CONTINUATION",
                    "kind": "resource_reconfiguration",
                    "subject_path": signal["path"],
                    "instruction": (
                        f"只检查 {state} 在资源重配置后的后续公开操作。若结论声称持续通知、"
                        "持续阻塞、重复移除或容器破坏，按调用顺序证明每一步所依赖的第二状态、"
                        "容器成员关系、计数器以及全局与对象配置可以同时成立；缺少状态转换时缩小"
                        "结论，不得从残留标志直接推导持续或破坏性终态。"
                    ),
                    "context_paths": [signal["path"]],
                })

        elif not re.search(r"\bref\s*>\s*0", signal["signal"]):
            check_suffix = "TERMINATION" if _ABORT_RE.search(signal["signal"]) else "ASSERT"
            checks.append({
                "check_id": f"SC-{signal_index:02d}-{check_suffix}",
                "kind": "assertion_reachability",
                "subject_path": signal["path"],
                "instruction": (
                    f"单独核对 {signal['path']}:{signal['line']} 的高影响终点。"
                    f"{signal['analysis_focus']} 必须写入同 check_id 的 failure path，"
                    "不得只在正常流程摘要中提到或由另一条相邻断言代替。"
                ),
                "context_paths": [signal["path"]],
            })

        if re.search(r"\bref\s*>\s*0", signal["signal"]):
            candidates = paired_paths or [signal["path"]]
            for path_index, relative in enumerate(candidates, 1):
                checks.append({
                    "check_id": f"SC-{signal_index:02d}-PAIR-{path_index:02d}",
                    "kind": "paired_operation",
                    "subject_path": relative,
                    "instruction": (
                        f"只重放 {relative} 的配对操作链：从增加操作的返回值追到当前函数最终返回，"
                        "再追到上层是否真正完成绑定、入队或状态提交，最后核对减少操作。"
                        "声称增加操作失败前，先证明分支实际调用了该操作；被 guard 跳过与调用后失败"
                        "是不同路径，不能混写。"
                        "增加操作返回失败时，只追公开契约允许的正常恢复、关闭和清理，不用无效状态下的成员操作制造风险。"
                        "给出本实现的独立结论，不与其他实现合并；若形成风险，风险 affected_paths "
                        f"必须明确包含且仅就本项声称 {relative} 受影响。"
                    ),
                    "context_paths": list(dict.fromkeys([signal["path"], relative])),
                })
    return checks


def _doca_semantic_check_items(
    unit: AnalysisUnit, repositories: list[dict]
) -> list[dict]:
    repository = next((item for item in repositories if item["repo_id"] == unit.repo_id), None)
    if repository is None:
        return []
    root = Path(repository["source_root"])
    checks = []
    for relative in unit.source_scope:
        path = root / relative
        if path.suffix.lower() not in _IMPLEMENTATION_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not (_DOCA_TASK_ALLOC_RE.search(text) and _DOCA_TASK_SUBMIT_RE.search(text)):
            continue
        checks.append({
            "check_id": f"DOCA-SUBMIT-{len(checks) + 1:02d}",
            "kind": "paired_operation",
            "subject_path": relative,
            "instruction": (
                "从 DOCA task 分配成功开始，分别重放 doca_task_submit 成功与失败。"
                "submit 失败分支必须逐步说明 task 所有权、是否调用 task free、context stop 前"
                "是否仍有 in-flight task，以及清理后的下一次提交能力；不能用返回错误或不重试"
                "代替资源终态。"
            ),
            "context_paths": [relative],
        })
    return checks


def prepare_worker_tasks(state: PangeaState) -> PangeaState:
    units = state.get("analysis_units", [])
    if not units:
        raise ValueError("用户指定范围没有可分析源码")
    inventory = state.get("inventory", {})
    inventory_files = {
        (item.get("repo_id"), item.get("path", "").replace("\\", "/"))
        for item in inventory.get("files", [])
    }
    empty_units = []
    for unit in units:
        scopes = [scope.replace("\\", "/").strip("/") or "." for scope in unit["source_scope"]]
        if not any(
            repo_id == unit["repo_id"] and any(
                scope == "." or path == scope or path.startswith(f"{scope}/") for scope in scopes
            )
            for repo_id, path in inventory_files
        ):
            empty_units.append(unit["unit_id"])
    if empty_units:
        raise ValueError(f"分析单元没有可分析源码：{', '.join(empty_units)}")
    validate_nonoverlapping_units(units)
    validate_complete_unit_coverage(
        units,
        state.get("scope_expansion", {}).get("groups", []),
    )
    run_dir = run_directory(state)
    inventory_path = run_dir / "inputs" / "inventory.json"
    source_manifest_path = run_dir / "inputs" / "source-manifest.json"
    source_manifest = state.get("source_manifest", {})
    write_json(run_dir / "inputs" / "task-contract.json", state["task_contract"])
    write_json(source_manifest_path, source_manifest)
    write_json(inventory_path, inventory)
    repositories = [
        {"repo_id": repo["repo_id"], "source_root": repo["source_root"], "git": repo.get("git", {})}
        for repo in state.get("repositories", [])
    ]
    missing_inputs = [
        str(path) for path in (inventory_path, source_manifest_path, Path(state["index_path"]))
        if not path.is_file()
    ]
    if missing_inputs:
        raise ValueError(f"worker 冻结输入不存在：{missing_inputs}")
    for raw_unit in units:
        unit = AnalysisUnit.model_validate(raw_unit)
        unit_repositories = [repo for repo in repositories if repo["repo_id"] == unit.repo_id]
        failure_signal_context = _failure_signal_context(unit, unit_repositories)
        providers = semantic_providers(unit.languages, unit.frameworks)
        unsupported_providers = providers - {"c_cpp", "lua", "openubmc"}
        if unsupported_providers:
            raise ValueError(
                f"unsupported semantic providers: {sorted(unsupported_providers)}"
            )
        semantic_check_items = []
        if "c_cpp" in providers:
            semantic_check_items.extend(_semantic_check_items(
                unit,
                unit_repositories,
                failure_signal_context,
            ))
            semantic_check_items.extend(
                _doca_semantic_check_items(unit, unit_repositories)
            )
        if providers & {"lua", "openubmc"}:
            semantic_check_items.extend(build_runtime_semantic_checks(
                unit,
                inventory,
                state.get("scope_expansion", {}).get("unresolved_dependencies", []),
            ))
        semantic_check_items = _prioritize_discovery(unit, semantic_check_items)
        task = WorkerTask(
            task_type="analysis",
            stage="source_checkpoint",
            run_id=state["run_id"],
            target=state["task_contract"]["target"],
            unit=unit,
            repositories=unit_repositories,
            index_path=state["index_path"],
            inventory_path=None,
            source_manifest_path=None,
            allowed_material_paths=[],
            checkpoint_rubric_paths=_checkpoint_rubric_paths(unit, inventory),
            coverage_context=[],
            failure_signal_context=failure_signal_context,
            semantic_check_items=semantic_check_items,
            attempt=0,
            result_path=str(analysis_result_path(state, unit.unit_id, 0)),
        )
        path = analysis_task_path(state, unit.unit_id, "source_checkpoint")
        write_json(path, task.model_dump(mode="json"))
        result_path = Path(task.result_path)
        if not result_path.exists():
            write_json(result_path, worker_result_skeleton(task))
    progress = RunProgress(
        workflow_version=2,
        run_id=state["run_id"],
        phase="WAITING_SOURCE_CHECKPOINT",
        analysis_units=[unit["unit_id"] for unit in units],
        agent_sessions={
            f"analysis:{unit['unit_id']}": AgentSession(
                role="analysis", unit_id=unit["unit_id"], stage="source_checkpoint"
            )
            for unit in units
        },
    )
    save_progress(state, progress)
    actions = [
        agent_action(
            progress,
            session_key=f"analysis:{unit['unit_id']}",
            role="analysis",
            stage="source_checkpoint",
            unit_id=unit["unit_id"],
            task_path=analysis_task_path(state, unit["unit_id"], "source_checkpoint"),
        )
        for unit in units[:MAX_PARALLEL_ACTIONS]
    ]
    return {
        **state,
        "phase": progress.phase,
        "agent_task_paths": [action["task_path"] for action in actions],
        "agent_actions": actions,
    }
