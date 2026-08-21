from __future__ import annotations

from pangea_agent.models.worker import AnalysisUnit


_LUA_FAILURE_ENDPOINTS = {"assert", "error"}
_LUA_PROTECTED_CALLS = {"pcall", "xpcall"}


def build_runtime_semantic_checks(
    unit: AnalysisUnit,
    inventory: dict,
    unresolved_dependencies: list[dict] | None = None,
) -> list[dict]:
    if "lua" not in unit.languages:
        return []

    owned_paths = set(unit.source_scope) | set(unit.context_scope)
    files = sorted(
        (
            item
            for item in inventory.get("files", [])
            if item.get("repo_id") == unit.repo_id
            and item.get("path") in owned_paths
            and item.get("language") == "lua"
        ),
        key=lambda item: item["path"],
    )
    checks: list[dict] = []
    lua_error_index = 0
    lifecycle_index = 0
    signal_index = 0

    for item in files:
        path = item["path"]
        calls_by_function: dict[str, list[dict]] = {}
        for call in item.get("calls", []):
            calls_by_function.setdefault(
                call.get("function_symbol", "<top-level>"), []
            ).append(call)
        for function_symbol, function_calls in calls_by_function.items():
            failure_calls = [
                call
                for call in function_calls
                if call.get("callee") in _LUA_FAILURE_ENDPOINTS
            ]
            if not failure_calls:
                continue
            lua_error_index += 1
            protected_calls = [
                call
                for call in function_calls
                if call.get("callee") in _LUA_PROTECTED_CALLS
            ]
            rendered = ", ".join(
                f"{call['callee']}@{call['line']}" for call in failure_calls
            )
            protected = ", ".join(
                f"{call['callee']}@{call['line']}" for call in protected_calls
            ) or "同函数未见 pcall/xpcall"
            checks.append({
                "check_id": f"SC-LUA-ERROR-{lua_error_index:02d}",
                "kind": "runtime_semantics",
                "subject_path": path,
                "instruction": (
                    f"按执行顺序重放 {path} 的 {function_symbol} 中 Lua 失败终点（{rendered}；"
                    f"保护调用：{protected}）：先记录失败前已发生的状态修改，再判断 error/assert "
                    "是否被真实调用链上的 pcall/xpcall 覆盖、调用方实际收到什么结果、"
                    "是否执行恢复或继续使用部分状态。不要把出现 pcall 本身当成已正确恢复。"
                ),
                "context_paths": [path],
            })

        framework_signals = item.get("framework_signals", [])
        lifecycle_signals = [
            signal
            for signal in framework_signals
            if signal.get("kind") in {"class_declaration", "class_lifecycle"}
        ]
        if lifecycle_signals:
            lifecycle_index += 1
            locations = ", ".join(
                f"{signal.get('symbol', '<unknown>')}@{signal['line']}"
                for signal in lifecycle_signals
            )
            checks.append({
                "check_id": f"SC-OPENUBMC-LIFECYCLE-{lifecycle_index:02d}",
                "kind": "runtime_semantics",
                "subject_path": path,
                "instruction": (
                    f"核对 {path} 的 openUBMC 组件生命周期（{locations}）：从 ctor、pre_init 到 init"
                    "逐阶段列出依赖、状态写入和可对外使用时点；分别重放任一阶段失败后的已完成副作用、"
                    "后续阶段是否仍可能运行及组件对外观测。不得把方法名称出现当成调用顺序证据。"
                ),
                "context_paths": [path],
            })

        emit_signals = [
            signal
            for signal in framework_signals
            if signal.get("kind") == "signal_emit"
        ]
        for signal in emit_signals:
            signal_index += 1
            checks.append({
                "check_id": f"SC-OPENUBMC-SIGNAL-{signal_index:02d}",
                "kind": "runtime_semantics",
                "subject_path": path,
                "instruction": (
                    f"从 {path}:{signal['line']} 的 {signal.get('symbol', 'signal')} emit 入口按 callback"
                    "注册顺序重放：记录每个 callback 的副作用、单个 callback 抛错后后续 callback 是否"
                    "继续、emit 最终向调用方返回的错误，以及调用方是否错误地按“没有发生修改”处理。"
                    "若冻结范围不足以确认 callback 顺序或框架行为，写 unresolved，不得直接制造风险。"
                ),
                "context_paths": [path],
            })

    unresolved_index = 0
    source_paths = set(unit.source_scope)
    for item in unresolved_dependencies or []:
        if item.get("repo_id") != unit.repo_id or item.get("path") not in source_paths:
            continue
        unresolved_index += 1
        reason = item.get("reason", "unknown")
        module = item.get("module") or item.get("expression") or "<dynamic>"
        candidates = item.get("candidates", [])
        candidate_text = f"，候选={candidates}" if candidates else ""
        checks.append({
            "check_id": f"SC-LUA-REQUIRE-{unresolved_index:02d}",
            "kind": "runtime_semantics",
            "subject_path": item["path"],
            "instruction": (
                f"处理 {item['path']}:{item.get('line', '?')} 的 require({module}) 未解析项"
                f"（{reason}{candidate_text}）：只能依据冻结源码和资料判断实际依赖。能证明不属于"
                "本分析对象时写 excluded；范围或 package.path 信息不足时写 unresolved；不得猜测"
                "文件、递归扩展或静默跳过。"
            ),
            "context_paths": [item["path"]],
        })

    return checks
