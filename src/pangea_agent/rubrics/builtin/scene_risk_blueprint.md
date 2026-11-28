# 风险全局底图构建与四轨派单（risk-method-v2 · Stage 1）

仅适用于 `analysis_scene.method_version=risk-method-v2`。本阶段不按文件机械切块，也不提前下风险结论；职责是提取历史故障代码特征、构建全局四轨底图，并按**四大正交专项轨道**派发 Stage 2 深度追踪任务。

## 一、输入阅读与历史故障特征提取（DTS + MR Diff + 缺陷资产）

1. 先读 `analysis_scene`、`planning_metadata` 中的 `risk_topology_overview`（已按运行态 `phase_hint="runtime"`、高危未清理出口 `uncleaned_exit_slices` 与全局分配优先排序，`phase_hint="init_or_boot"` 模块启动初始化文件排在末位）与 `risk_topology_summary`（C/C++ 与 Lua 的四轨确定性结构索引；Python/Shell 直接读源码）。
2. 再读 `dts_context`（冻结的全局 DTS 故障拓扑库、已解析 `homologous_dts`、以及用户显式提供的 `requested_dts_ids` 与 `requested_mr_urls`），并核对 `asset_candidates`（已选历史缺陷资产）与 `methodology_catalog`：
   - **MCP 精准取证（绝不按模块盲搜）**：当 `dts_context.requested_dts_ids` 或 `dts_context.requested_mr_urls` 非空时：
     1. 若当前会话暴露了 `DTS-mcp` / `codehub-mcp`：对 `requested_dts_ids` 逐单读取详情与关联 MR 链接，再对取得的 MR 链接及用户直填的 `requested_mr_urls` 调用 `codehub-mcp` 读取真实 MR Diff；
     2. 从真实单据与 Diff 中提炼四要素：**① 故障前漏洞代码骨架（`buggy_skeleton`，重点关注内存泄漏、跨线程/跨进程释放后访问、销毁注销逆序、槽位复用无世代校验、异常漏回滚或越界写）；② 修复后保护条件（`fixed_guard`）；③ 老问题已修复免疫判据（`already_fixed_check`，供目标版本源码排除已合入补丁点）；④ 同源变种横向猎杀线索（同模块兄弟命令、同类回调、兄弟结构体/资源槽位）**；
     3. 将提炼出的历史故障特征保存为一条 **`relates_to=[]` 的全局 `kind=note`**（通过 `planning_handoff` 广播给全部四轨 Worker），并在下述派单时为每条特征**显式点名唯一负责轨道**；
     4. 若用户指定了 `requested_dts_ids` / `requested_mr_urls`，但当前会话未挂载对应 MCP 工具或拉取失败，**严禁静默吞掉或编造 Diff**：必须直接消费 `dts_context.homologous_dts` / `matched_patterns` 中已有的本地确定性特征，同时写入一条 `kind=unresolved` 如实说明在线单据/MR 未能拉取的具体原因。
   - **缺陷资产与方法论挂接**：若 `asset_candidates` 含已选历史缺陷或参考资料，将其 `candidate_id` 填入相关轨道单元的 `asset_item_ids`；若 `methodology_catalog` 含匹配的用户/专项方法论，将其真实 ID 填入 `methodology_ids`。

## 二、构建全局四轨底图（`kind=note`，`format_version=risk-blueprint-v1`）

使用 `result-write` 保存一条 `kind=note`、`relates_to=[]` 的全局底图对象，为四轨 Worker 提供跨文件锚点。**运行态主战场铁律**：真实线上泄漏、竞态与踩内存绝大部分发生在**运行期业务路径**（建连/会话、收发包与 IO、管理/配置命令、固件升级传包与合法性校验、超时断链恢复），而非进程/模块启动引导期（`init/`、`*_init.c`）；当范围含运行期源码时，**底图至少 80% 的实体必须聚焦 `phase_hint="runtime"` 的业务路径及 `uncleaned_exit_candidates` / `global_alloc_candidates` 高危函数**，严禁让启动期一次性池表初始化占满底图：
- `resource_entities`：运行期跨函数/跨文件的受管资源实体（会话/请求/升级阶段上下文、堆缓冲、fd/socket、句柄、引用计数、定时器、队列额度）、**并发/建连/恢复路径上惰性初始化或重复赋值的全局/静态缓冲区指针（对照 `risk_topology_summary` 的 `global_alloc_candidates`，切勿因体积小如 4KB 暂存/丢弃缓冲而遗漏）**，以及**含 `uncleaned_exit_candidates` 的多出口校验/局部工作缓冲分配密集函数族**，写明获取点、所有权流转与预期释放点（供 `resource_lifecycle` 查 L1–L8 泄漏、并发惰性初始化覆写与重复释放）。
- `concurrency_contexts`：独立执行流（主线程、并发建连/请求工作线程、定时器/异步回调、Skynet 协程、IPC 对端进程）、**同一注册/处理函数被多线程并发重入的全局/静态共享指针或状态**及预期锁/原子保护（供 `concurrency_timing` 与 `resource_lifecycle` 核查竞态、无锁惰性初始化与死锁）。
- `transaction_chains`：包含多步状态修改、资源分配或跨边界调用的核心业务事务入口链（供 `error_rollback` 查中途失败逆序撤销与脏态残留）。
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

- 废除按文件机械互斥切块。正常规模下按 **4 个专职轨道**齐整派单，通过 `risk_track` 显式声明轨道（**不同 `risk_track` 的单元允许且应当共享相同的核心 `owned_files`**，同一轨道内部拆分子单元时须互不重叠）：
  1. `risk_track="error_rollback"`：异常分支与状态回滚轨（覆盖 `transaction_chains` 及相关源文件；**默认兼任模块主干流程 owner 与跨轨/非四轨 DTS 特征兜底认领轨**）。
  2. `risk_track="concurrency_timing"`：锁纪律与存活态并发竞态轨（覆盖 `concurrency_contexts` 及相关源文件；目标范围跨越多个独立子业务或文件较多时，可按并发域拆分为互不重叠子单元）。
  3. `risk_track="resource_lifecycle"`：资源配平与内存/句柄泄漏、并发全局惰性初始化覆写、跨阶段孤儿残留及重复释放轨（对照 `risk_topology_overview.counts` 的 `uncleaned_exit_slices`、`alloc_flow_slices`、`global_lazy_or_alloc_writes` 与 `resource_lifecycle`，**必须把全部含堆分配/全局缓冲初始化/资源获取的目标文件纳入覆盖**）。
  4. `risk_track="memory_corruption"`：**踩内存与跨流悬空访问轨**（覆盖 `memory_hazard_zones` 及相关源文件，重点排查线程/进程访问已释放或重分配资源导致的踩内存，以及空间越界写）。
- **四轨完备红线（严禁砍掉第四轨 `memory_corruption`）**：对 C/C++ 源码，**严禁仅因用户 `target`/`focus` 提及“内存泄漏”就跳过 `memory_corruption` 或其他轨道**——资源生命周期管理混乱往往伴随异常销毁后异步回调悬空写与越界踩踏；除非用户显式要求仅跑单一轨道或目标范围纯无指针/缓冲/并发共享态，否则四轨必须全部派发。
- **启动目录（`init/`）与运行期业务目录物理拆单隔离（防偏航硬约束）**：当目标范围同时包含模块初始化/启动文件（`phase_hint="init_or_boot"`，如 `init/`、`*_init.c`）与运行期业务文件（`phase_hint="runtime"`，如连接会话、IO 收发、命令处理、固件升级传包校验、故障恢复）时，`resource_lifecycle`（及多文件 `concurrency_timing`）**严禁将 `init/` 目录与大批运行期业务文件混塞进同一个大单元**！必须按运行期业务契约闭包将**运行态子域拆分为独立的专职高优单元**（每单元聚焦数个紧密关联的运行态源文件，确保 Worker 逐函数吃透业务运行期分配与退出路径），而将 `init/` 启动初始化文件单独隔离为独立尾单，彻底防止 Worker 在启动期代码里耗尽推理预算。
- **单主干轨道画图（消除重复消耗）**：仅指定 **1 个主干单元**（默认 `error_rollback`）在 `purpose` 中承担本模块结构化流程图交付（`kind=flow`，含 `sequence`/`call_graph`），其余轨道单元在 `purpose` 中写明“免重复交付模块流程，专职本轨风险深潜”。
- **DTS / MR 特征点名认领（严禁无人认领）**：每条导入的 `dts_context` / MR Diff / 缺陷资产特征，必须在对应轨道单元的 `purpose` 中**显式写出其单号/ID 归该单元负责横向核查**（LSan/内存泄漏归 `resource_lifecycle`，踩内存/空指针归 `memory_corruption`，并发/锁归 `concurrency_timing`，跨轨或不易归类的其他代码缺陷统一指派给 `error_rollback` 兜底）。
- 单个轨道单元的 `owned_files` / `context_files` 必须把本轨实体的“创建/加锁/写入”与“销毁/注销/跨线程回调访问”两侧同时纳入可读范围（注意只读参考放 `context_files`，避免把无关大目录塞入 `owned_files`），严禁只看半边契约。
- 若目标范围确实不含某轨道物理对象（如纯配置脚本无任何指针/并发），方可跳过该轨并在全局 `summary` 说明依据。
