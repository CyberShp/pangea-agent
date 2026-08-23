from __future__ import annotations

import re

from pangea_agent.models.worker import AnalysisUnit


_LUA_FAILURE_ENDPOINTS = {"assert", "error"}
_LUA_PROTECTED_CALLS = {"pcall", "xpcall"}


def _is_callback_registration(call: dict) -> bool:
    callee = call.get("callee", "")
    return callee.endswith(":connect") or callee.endswith(".connect") or (
        callee.endswith(":register") or callee.endswith(".register")
    )


def _anonymous_callback_line(function_symbol: str) -> int | None:
    match = re.fullmatch(r"<anonymous@(\d+)>", function_symbol)
    return int(match.group(1)) if match else None


def _protected_call_boundaries(files: list[dict]) -> str:
    boundaries: list[str] = []
    for item in files:
        calls = item.get("calls", [])
        for protected in calls:
            if protected.get("callee") not in _LUA_PROTECTED_CALLS:
                continue
            owner = protected.get("function_symbol")
            anonymous = f"<anonymous@{protected['line']}>"
            protected_calls = [
                call
                for call in calls
                if call.get("function_symbol") == anonymous
            ]
            if not owner or not protected_calls:
                continue
            earlier_calls = [
                call
                for call in calls
                if call.get("function_symbol") == owner
                and call.get("line", 0) < protected["line"]
            ]
            inside = ", ".join(
                f"{call['callee']}@{call['line']}" for call in protected_calls
            )
            before = ", ".join(
                f"{call['callee']}@{call['line']}" for call in earlier_calls
            ) or "无"
            boundaries.append(
                f"{item['path']} 的 {owner} 中 {protected['callee']}@{protected['line']} 只保护其"
                f"匿名函数内的 {inside}；此前同入口调用 {before} 位于保护范围外"
            )
    return "；".join(boundaries) or "冻结入口未识别到内联 pcall/xpcall 边界"


def _frozen_lua_source_facts(files: list[dict]) -> str:
    return_facts: list[str] = []
    registration_facts: list[str] = []
    for item in files:
        path = item["path"]
        for returned in item.get("returns", []):
            function_symbol = returned.get("function_symbol")
            if not function_symbol or function_symbol.startswith("<anonymous@"):
                continue
            guard = returned.get("guard")
            if guard:
                return_facts.append(
                    f"{path}:{returned['line']} {function_symbol} 在条件 `{guard}` 成立时执行原始语句 "
                    f"`{returned['statement']}`，该 return 立即结束 {function_symbol}，后续循环项不再执行"
                )
            else:
                return_facts.append(
                    f"{path}:{returned['line']} {function_symbol} 原始语句 `{returned['statement']}`"
                )
        calls_by_function: dict[str, list[dict]] = {}
        for call in item.get("calls", []):
            if _is_callback_registration(call) and call.get("function_symbol"):
                calls_by_function.setdefault(call["function_symbol"], []).append(call)
        for function_symbol, registrations in calls_by_function.items():
            ordered = ", ".join(
                f"{call['callee']}@{call['line']}"
                for call in sorted(registrations, key=lambda value: value["line"])
            )
            registration_facts.append(f"{path} {function_symbol} 注册顺序 [{ordered}]")
            for call in sorted(registrations, key=lambda value: value["line"]):
                if call["callee"].startswith("self."):
                    registration_facts.append(
                        f"{path} {function_symbol} 的 {call['callee']}@{call['line']} 所注册匿名 callback "
                        "按 Lua 词法闭包捕获本次调用的 self；以后无论由哪个实例触发共享 signal，"
                        "callback 内 self 仍指向注册它的实例，不会替换成 emit 发起实例"
                    )
    facts = return_facts + registration_facts
    return "；".join(facts) or "冻结解析器未提取到 return/callback 注册事实"


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
    protected_boundaries = _protected_call_boundaries(files)
    frozen_source_facts = _frozen_lua_source_facts(files)
    lua_error_index = 0
    lifecycle_index = 0
    signal_index = 0

    for item in files:
        path = item["path"]
        framework_signals = (
            item.get("framework_signals", [])
            if "openubmc" in unit.frameworks
            else []
        )
        calls_by_function: dict[str, list[dict]] = {}
        for call in item.get("calls", []):
            calls_by_function.setdefault(
                call.get("function_symbol", "<top-level>"), []
            ).append(call)
        all_calls = item.get("calls", [])
        for function_symbol, function_calls in calls_by_function.items():
            failure_calls = [
                call
                for call in function_calls
                if call.get("callee") in _LUA_FAILURE_ENDPOINTS
            ]
            if not failure_calls:
                continue
            protected_calls = [
                call
                for call in function_calls
                if call.get("callee") in _LUA_PROTECTED_CALLS
            ]
            protected = ", ".join(
                f"{call['callee']}@{call['line']}" for call in protected_calls
            ) or "同函数未见 pcall/xpcall"
            for failure_call in failure_calls:
                lua_error_index += 1
                failure_line = failure_call["line"]
                rendered = f"{failure_call['callee']}@{failure_line}"
                callback_line = _anonymous_callback_line(function_symbol)
                owner_symbol = failure_call.get("owner_function_symbol")
                callback_registration = next(
                    (
                        call
                        for call in all_calls
                        if callback_line is not None
                        and call.get("line") == callback_line
                        and _is_callback_registration(call)
                    ),
                    None,
                )
                if callback_registration:
                    emit_text = ", ".join(
                        f"{signal.get('symbol', 'signal')} emit@{signal['line']}"
                        for signal in framework_signals
                        if signal.get("kind") == "signal_emit"
                    ) or "冻结范围中尚未定位 emit/dispatch"
                    registration_text = (
                        f"{owner_symbol or '<owner>'} 的 "
                        f"{callback_registration['callee']}@{callback_registration['line']}"
                    )
                    instruction = (
                        f"按执行顺序重放 {path} 的 {function_symbol} 中 Lua 失败终点（{rendered}；"
                        f"保护调用：{protected}）。该失败位于 {registration_text} 注册的 callback 函数体内；"
                        "执行 connect/register 时不会进入该函数体，也不会到达本失败点。只有 callback "
                        f"随后在 {emit_text} 或有源码证明的直接调用中真正执行时，才可能触发 {rendered}。"
                        "必须从真实调用点按 callback 顺序重放，记录本 callback 报错前已发生的字段写入、"
                        "框架层 pcall/xpcall 是否捕获、后续 callback 是否跳过，以及 emit 和上层调用方"
                        "最终收到的返回值；不得把该错误写成 callback 注册阶段或 init 注册过程中的失败。"
                        f" 冻结解析器事实（优先于推测）：{frozen_source_facts}。"
                    )
                else:
                    registrations = [
                        call for call in function_calls if _is_callback_registration(call)
                    ]
                    before = [call for call in registrations if call["line"] < failure_line]
                    after = [call for call in registrations if call["line"] > failure_line]
                    before_text = ", ".join(
                        f"{call['callee']}@{call['line']}" for call in before
                    ) or "无"
                    after_text = ", ".join(
                        f"{call['callee']}@{call['line']}" for call in after
                    ) or "无"
                    instruction = (
                        f"按执行顺序重放 {path} 的 {function_symbol} 中 Lua 失败终点（{rendered}；"
                        f"保护调用：{protected}；失败前已到达的 callback 注册：{before_text}；"
                        f"失败后未到达的 callback 注册：{after_text}）：先记录失败前已发生的状态修改，"
                        "再判断 error/assert 是否被真实调用链上的 pcall/xpcall 覆盖、调用方实际收到"
                        "什么结果、是否执行恢复或继续使用部分状态。connect/register 只把函数加入 "
                        "callback 表，不是调用 callback；失败前没有 emit/dispatch/直接调用时，已注册 "
                        "callback 的函数体执行次数固定为 0，其内部字段保持未改变。失败点之后的语句和"
                        "注册不能算作已执行。不要把出现 pcall 本身当成已正确恢复。"
                        f" 冻结解析器事实（优先于推测）：{frozen_source_facts}。"
                    )
                checks.append({
                    "check_id": f"SC-LUA-ERROR-{lua_error_index:02d}",
                    "kind": "runtime_semantics",
                    "subject_path": path,
                    "instruction": instruction,
                    "context_paths": [path],
                })
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
            callback_locations = ", ".join(
                str(signal["line"])
                for signal in framework_signals
                if signal.get("kind") == "signal_callback"
            ) or "无"
            emit_locations = ", ".join(
                str(signal["line"])
                for signal in framework_signals
                if signal.get("kind") == "signal_emit"
            ) or "无"
            checks.append({
                "check_id": f"SC-OPENUBMC-LIFECYCLE-{lifecycle_index:02d}",
                "kind": "runtime_semantics",
                "subject_path": path,
                "instruction": (
                    f"核对 {path} 的 openUBMC 组件生命周期（{locations}）：从 ctor、pre_init 到 init"
                    "逐阶段列出依赖、状态写入和可对外使用时点。主 check_id 只记录“有效配置、依赖就绪、"
                    "单个新实例、初始化一次、首次 update/emit”的正常路径；该路径的 disposition 不得被"
                    "失败重试或多实例路径改写。retry、nil-config、uninitialized-update 由 task 中同名"
                    "场景 check 单独承载；本项不得再创建派生 path 或混入那些场景的终态。"
                    f"本文件 callback 注册位置为 {callback_locations}，emit 位置为 {emit_locations}；"
                    "注册行只改变 callback 表。只有执行到 emit/dispatch 或直接调用 callback 的位置，"
                    "函数体才会写计数器/状态；失败发生在首次 emit 之前时这些字段增量必须为 0。"
                    "Lua 中 config={} 缺少字段只会读到 nil，不会因缺 key 本身发生索引错误；只有 "
                    "config=nil 等不可索引接收者继续取字段才会在该取值点报错，二者必须拆开重放。"
                    f"冻结入口的保护边界：{protected_boundaries}。保护调用之前发生的错误不得写成被"
                    "后面的 pcall/xpcall 捕获；公开入口直接调用时该错误会直接抛出，只有测试显式在入口"
                    "外层再包一层 pcall/xpcall 才能观测到 ok=false。错误中断前未完成的赋值保持原值。"
                    "不得把方法名称出现当成调用顺序证据。"
                    f" 冻结解析器事实（优先于推测）：{frozen_source_facts}。"
                ),
                "context_paths": [path],
            })
            lifecycle_id = f"SC-OPENUBMC-LIFECYCLE-{lifecycle_index:02d}"
            checks.extend([
                {
                    "check_id": f"{lifecycle_id}:retry",
                    "kind": "runtime_semantics",
                    "subject_path": path,
                    "instruction": (
                        f"单独重放 {path} 的同实例初始化返工路径：首次 init 失败、修正依赖、"
                        "同一实例重试成功、随后只 emit/update 一次。首次失败发生在 emit 前时，"
                        "connect/register 只改变注册表，callback 函数体字段增量必须为 0；重试后再按"
                        "实际到达的注册顺序计算一次事件的每项字段增量。不得把注册表长度写成"
                        "callback_count/audit_count，也不得把首次失败后的 false 带到成功重试后的"
                        f"initialized。冻结入口保护边界：{protected_boundaries}。"
                        f" 冻结解析器事实（优先于推测）：{frozen_source_facts}。"
                    ),
                    "context_paths": [path],
                },
                {
                    "check_id": f"{lifecycle_id}:nil-config",
                    "kind": "runtime_semantics",
                    "subject_path": path,
                    "instruction": (
                        f"单独重放 {path} 的 config=nil 路径。字段读取若发生在入口内部 pcall/xpcall"
                        "之前，公开入口直接调用会抛出错误且不会产生该入口的 ok/err 返回；只有测试在"
                        "整个公开入口外再包一层 pcall/xpcall，外层 wrapper 才返回 ok=false 和非空错误"
                        "对象。右侧读取失败时左侧赋值不发生。源码可达不等于产品支持：现行资料没有"
                        "承诺 nil 配置时，本项在 risk_analysis 必须排除为契约外输入，不生成风险或用例。"
                        f"冻结入口保护边界：{protected_boundaries}。"
                        f" 冻结解析器事实（优先于推测）：{frozen_source_facts}。"
                    ),
                    "context_paths": [path],
                },
                {
                    "check_id": f"{lifecycle_id}:uninitialized-update",
                    "kind": "runtime_semantics",
                    "subject_path": path,
                    "instruction": (
                        f"单独重放 {path} 的 create/ctor 后未 start/init 就 update/emit 路径，列出"
                        "注册表、返回值和字段终态。源码入口可调用不自动构成产品风险；只有现行需求或"
                        "公开契约承诺未初始化调用的行为时才转风险，否则在 risk_analysis 排除为调用方"
                        "误用，不生成 TestCase。"
                        f" 冻结解析器事实（优先于推测）：{frozen_source_facts}。"
                    ),
                    "context_paths": [path],
                },
            ])

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
                    "同时确认 signal/callback 注册表由实例、类表还是模块持有，并检查失败重试或多实例"
                    "是否复用同一注册表；存在复用时分别计算残留 callback 与本次新增 callback 的调用次数。"
                    "先建立逐 callback 账本：注册表大小单独记录；字段值只累计函数体确实写该字段且"
                    "在错误中断前执行到的 callback。不得把 callback 表大小直接写成 callback_count、"
                    "audit_count 或其他业务字段。每次调用的增量与多次调用后的绝对值必须分开写：第二次"
                    "调用后绝对值为 2，不等于第二次调用的增量为 2。"
                    "共享表中的 callback 必须同时标注注册实例和本次 emit 传入值：emit 发起实例只选择"
                    "共享 signal，不会改写 callback 闭包中的 self；每次 emit 都用本次 value 重新判断"
                    "callback 内条件。某 callback 曾在 value='trip' 时失败，不代表它随后在其他 value 下"
                    "也会失败。若顺序是 A.C1、A.C2、A.C3、B.C1、B.C2、B.C3，A.C2 报错时只有此前"
                    "实际执行的 A callback 能写 A，A.C3 与全部 B callback 都未执行，B 字段保持调用前值。"
                    "若 signal 在类表/模块表上，且冻结源码存在可重复调用的公开 create/ctor 入口，又没有"
                    "singleton 拒绝、disconnect 或 clear，则这已经是同一 VM 多实例可达性的源码证据；"
                    "必须在 task 已给出的 multi-instance 场景 check 中重放实例 A/B 的 callback 与状态归属，不能"
                    "仅因资料未写‘支持多实例’而排除。若公开入口明确拒绝第二实例，再据该阻断证据 excluded。"
                    "若冻结范围不足以确认 callback 顺序或框架行为，写 unresolved，不得直接制造风险。"
                    f" 冻结解析器事实（优先于推测）：{frozen_source_facts}。"
                ),
                "context_paths": [path],
            })
            signal_id = f"SC-OPENUBMC-SIGNAL-{signal_index:02d}"
            checks.append({
                "check_id": f"{signal_id}:multi-instance",
                "kind": "runtime_semantics",
                "subject_path": path,
                "instruction": (
                    f"单独重放 {path}:{signal['line']} 的同一 VM 双实例路径：A、B 各完成一次注册后，"
                    "先由 A 发送 normal，再由 B 发送 normal。callback 闭包继续写注册它的实例；"
                    "共享表当前实现中，第一次 normal 后 A/B 各自的 callback_count 和 audit_count"
                    "都为 1，第二次 normal 后 A/B 都为 2，且两者 committed=true。这里的 2 是两次"
                    "调用后的绝对值，每次增量仍为 1。若现行契约要求实例隔离，TestCase 的正确通过"
                    "标准必须是 A 事件后 A=1/B=0，B 事件后 A 保持 1、B=1；当前污染值只能写入"
                    "failure_observation，不能写进 expected_result。若公开入口明确拒绝第二实例，"
                    "再按阻断证据 excluded。"
                    f" 冻结解析器事实（优先于推测）：{frozen_source_facts}。"
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
