# 风险全局底图构建与三轨派单（risk-method-v2 · Stage 1）

仅适用于 `analysis_scene.method_version=risk-method-v2`。本阶段不按文件切块，也不提前下风险结论；职责是构建全局三轨底图并按**三大专项轨道**派发 Stage 2 深度追踪任务。

## 一、输入阅读顺序

1. 先读 `analysis_scene`、`planning_metadata` 中的 `risk_topology_overview` 与 `risk_topology_summary`（C/C++ 与 Lua 的三轨确定性结构索引；Python/Shell 直接读源码）。
2. 再读 `dts_context`（冻结的全局 DTS 故障拓扑库、已解析同源案例及用户显式提供的 `requested_dts_ids`）。
   - **MCP 精准取证（不按模块盲搜）**：仅当 `dts_context.requested_dts_ids` 非空且当前会话配置了 `DTS-mcp` / `codehub-mcp` 时，对用户给出的具体 DTS 单号依次执行：**调用 `DTS-mcp` 读取该 DTS 单详情获取关联 MR 链接 → 拿着 MR 链接调用 `codehub-mcp` 读取真实 MR Diff 提取故障前/修复后代码特征**，作为 `note` 交接给 Stage 2 并注明已核对；未提供单号或未装 MCP 时直接消费冻结的 `dts_context`，绝不按模块/目录名盲搜 DTS 库，也不报错或编造。

## 二、构建全局三轨底图（`kind=note`，`format_version=risk-blueprint-v1`）

使用 `result-write` 保存一条 `kind=note` 的全局底图对象（结构见下方），为三轨 Worker 提供跨文件锚点：
- `resource_entities`：跨函数/跨文件的受管资源实体（堆对象、fd/socket、句柄、引用计数、定时器、回调注册项、队列额度），写明创建点、所有权流转路径和预期释放/注销点。
- `concurrency_contexts`：独立执行上下文（主线程、工作线程、定时器/事件回调、Skynet 协程、信号/中断上下文）、共享状态及预期保护机制（锁、原子操作、单协程假设）。
- `transaction_chains`：包含多步状态修改、资源分配或跨边界调用的核心事务入口链。

```json
{
  "format_version": "risk-blueprint-v1",
  "resource_entities": [
    {"id": "RES-001", "name": "会话认证上下文 auth_ctx", "acquire_sites": ["repo:src/auth.c:45"], "release_sites": ["repo:src/sess.c:142"], "ownership_notes": "由 auth_start 分配，挂接 sess->auth_ctx"}
  ],
  "concurrency_contexts": [
    {"id": "CTX-001", "name": "超时定时器线程 vs 接收回调线程", "shared_state": ["sess->auth_ctx", "sess->state"], "expected_guard": "sess_lock"}
  ],
  "transaction_chains": [
    {"id": "TXN-001", "name": "会话初始化与异步认证提交", "entry": "repo:src/sess.c:20", "side_effect_steps": ["分配 auth_ctx", "注册完成回调", "启动超时定时器"]}
  ]
}
```

## 三、按三大专项轨道派发单元（`plan-write` / `plan-write-batch`）

- 废除按文件机械拆分。正常规模下固定创建 3 个专职轨道单元，通过 `risk_track` 显式声明轨道：
  1. `risk_track="error_rollback"`：异常分支与状态回滚轨（覆盖 `transaction_chains` 及相关源文件）。
  2. `risk_track="concurrency_timing"`：时序并发与异步交错轨（覆盖 `concurrency_contexts` 及相关源文件）。
  3. `risk_track="resource_lifecycle"`：资源全生命周期与泄漏/悬空轨（覆盖 `resource_entities` 及相关源文件）。
- 若某轨道涉及的源码规模过大，仅在该轨道内部按互不重叠的独立实体簇拆分为多个同轨单元；单个单元的 `owned_files` / `context_files` 必须把该实体簇的创建、消费、回调、释放两侧同时纳入可读范围，避免“只看半边契约”。
- 若目标范围确实不含某轨道对象（例如纯单线程无回调配置解析），仍可保留空结论说明或跳过该轨并在 `summary` 写明依据。
