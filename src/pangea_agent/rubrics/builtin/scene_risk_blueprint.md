# 风险全局底图构建与四轨派单（risk-method-v2 · Stage 1）

仅适用于 `analysis_scene.method_version=risk-method-v2`。本阶段不按文件机械切块，也不提前下风险结论；职责是提取历史故障代码特征、构建全局四轨底图，并按**四大正交专项轨道**派发 Stage 2 深度追踪任务。

## 一、输入阅读与历史故障特征提取（DTS + MR Diff + 缺陷资产）

1. 先读 `analysis_scene`、`planning_metadata` 中的 `risk_topology_overview` 与 `risk_topology_summary`（C/C++ 与 Lua 的四轨确定性结构索引；Python/Shell 直接读源码）。
2. 再读 `dts_context`（冻结的全局 DTS 故障拓扑库、已解析 `homologous_dts`、以及用户显式提供的 `requested_dts_ids` 与 `requested_mr_urls`），并核对 `asset_candidates`（已选历史缺陷资产）与 `methodology_catalog`：
   - **MCP 精准取证（绝不按模块盲搜）**：当 `dts_context.requested_dts_ids` 或 `dts_context.requested_mr_urls` 非空时：
     1. 若当前会话暴露了 `DTS-mcp` / `codehub-mcp`：对 `requested_dts_ids` 逐单读取详情与关联 MR 链接，再对取得的 MR 链接及用户直填的 `requested_mr_urls` 调用 `codehub-mcp` 读取真实 MR Diff；
     2. 从真实单据与 Diff 中提炼四要素：**① 故障前漏洞代码骨架（`buggy_skeleton`，重点关注跨线程/跨进程释放后访问、销毁注销逆序、槽位复用无世代校验、异常漏回滚或越界写）；② 修复后保护条件（`fixed_guard`）；③ 老问题已修复免疫判据（`already_fixed_check`，供目标版本源码排除已合入补丁点）；④ 同源变种横向猎杀线索（同模块兄弟命令、同类回调、兄弟结构体/资源槽位）**；
     3. 将提炼出的历史故障特征保存为一条 **`relates_to=[]` 的全局 `kind=note`**（通过 `planning_handoff` 广播给全部四轨 Worker）；
     4. 若用户指定了 `requested_dts_ids` / `requested_mr_urls`，但当前会话未挂载对应 MCP 工具或拉取失败，**严禁静默吞掉或编造 Diff**：必须直接消费 `dts_context.homologous_dts` / `matched_patterns` 中已有的本地确定性特征，同时写入一条 `kind=unresolved` 如实说明在线单据/MR 未能拉取的具体原因。
   - **缺陷资产与方法论挂接**：若 `asset_candidates` 含已选历史缺陷或参考资料，将其 `candidate_id` 填入相关轨道单元的 `asset_item_ids`；若 `methodology_catalog` 含匹配的用户/专项方法论，将其真实 ID 填入 `methodology_ids`。

## 二、构建全局四轨底图（`kind=note`，`format_version=risk-blueprint-v1`）

使用 `result-write` 保存一条 `kind=note`、`relates_to=[]` 的全局底图对象，为四轨 Worker 提供跨文件锚点：
- `resource_entities`：跨函数/跨文件的受管资源实体（堆对象、fd/socket、句柄、引用计数、定时器、队列额度），写明获取点、所有权流转与预期释放点（供 `resource_lifecycle` 查泄漏与重复释放）。
- `concurrency_contexts`：独立执行流（主线程、工作线程、定时器/异步回调、Skynet 协程、IPC 对端进程）、存活态共享状态及预期锁/原子保护（供 `concurrency_timing` 查竞态与死锁）。
- `transaction_chains`：包含多步状态修改、资源分配或跨边界调用的核心事务入口链（供 `error_rollback` 查中途失败逆序撤销与脏态残留）。
- `memory_hazard_zones`：**跨线程/跨进程共享生命周期指针、销毁与回调/定时器注销交错点、对象池/共享内存复用槽位（Slot/Ring/SHM）、以及非常量长度内存写边界**（供 `memory_corruption` 专职查释放后跨流访问踩内存与空间越界写）。

```json
{
  "format_version": "risk-blueprint-v1",
  "resource_entities": [
    {"id": "RES-001", "name": "会话认证上下文 auth_ctx", "acquire_sites": ["repo:src/auth.c:45"], "release_sites": ["repo:src/sess.c:142"], "ownership_notes": "由 auth_start 分配，挂接 sess->auth_ctx"}
  ],
  "concurrency_contexts": [
    {"id": "CTX-001", "name": "控制线程 vs 状态机工作线程", "shared_state": ["sess->state", "g_conn_cnt"], "expected_guard": "sess_lock"}
  ],
  "transaction_chains": [
    {"id": "TXN-001", "name": "会话初始化与异步认证提交", "entry": "repo:src/sess.c:20", "side_effect_steps": ["分配 auth_ctx", "注册完成回调", "启动超时定时器"]}
  ],
  "memory_hazard_zones": [
    {"id": "MEM-001", "name": "sess->auth_ctx 跨线程销毁与晚到回调悬空写及连接槽位池复用", "release_or_reuse_sites": ["repo:src/sess.c:142"], "concurrent_access_sites": ["repo:src/auth.c:88"], "hazard_notes": "Timer 释放 auth_ctx 后对象池立即可重分配给新连接，接收线程持旧指针写入将踩坏新连接内存"}
  ]
}
```

## 三、按四大正交专项轨道派发单元（`plan-write` / `plan-write-batch`）

- 废除按文件机械互斥切块。正常规模下固定创建 **4 个专职轨道单元**，通过 `risk_track` 显式声明轨道（**不同 `risk_track` 的单元允许且应当共享相同的核心 `owned_files`**，同一轨道内部拆分子单元时才需互不重叠）：
  1. `risk_track="error_rollback"`：异常分支与状态回滚轨（覆盖 `transaction_chains` 及相关源文件）。
  2. `risk_track="concurrency_timing"`：锁纪律与存活态并发竞态轨（覆盖 `concurrency_contexts` 及相关源文件）。
  3. `risk_track="resource_lifecycle"`：资源配平与泄漏/重复释放轨（覆盖 `resource_entities` 及相关源文件）。
  4. `risk_track="memory_corruption"`：**踩内存与跨流悬空访问轨**（覆盖 `memory_hazard_zones` 及相关源文件，重点排查线程/进程访问已释放或重分配资源导致的踩内存，以及空间越界写）。
- 单个轨道单元的 `owned_files` / `context_files` 必须把本轨实体的“创建/加锁/写入”与“销毁/注销/跨线程回调访问”两侧同时纳入可读范围，严禁只看半边契约。
- 若目标范围确实不含某轨道对象，仍可跳过该轨并在全局 `summary` 说明依据。
