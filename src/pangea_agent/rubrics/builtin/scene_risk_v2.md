# 风险四轨深潜职责（risk-method-v2 · Stage 2）

本场景优先于 `scene_common_generation`；具体语言检查规程见冻结的 `c_cpp_risk_v2.md` 或 `lua_risk_v2.md`。

## 一、核心原则：运行态优先四轨深潜 + 历史特征对标猎变种 + 两级实证 + 零推诿闭环

1. **本轨主责深潜 + 跨轨实锤零推诿闭环 + 流程单轨交付**：读取 `task.risk_track`（`error_rollback` | `concurrency_timing` | `resource_lifecycle` | `memory_corruption`）、全局底图 `risk_blueprint`、本轨 AST 索引 `track_topology`、`planning_handoff`（含 Stage 1 提取的全局 DTS/MR Diff 特征 `note`）、冻结的 `dts_context`，以及已选 `asset_items`（历史缺陷资产）。主动巡检聚焦本 `task.risk_track` 避免四轨重复扫描；**但因四轨并发执行且无二次转办队列，若你在深挖本轨源码时已顺手查清并由源码铁证锁定某处跨轨真实缺陷（如查锁竞态时直接证成全局指针覆写泄漏，或查泄漏/回滚时直接证成先释放后注销导致跨流 UAF 踩内存），严禁以“归其他轨道管”为由丢弃！必须直接提交 `kind=risk` 并填写该缺陷真实的 `track` 归属，做到见洞必收、零推诿漏报**。**仅被 Stage 1 在 `purpose` 中指定为主干流程 owner 的单元按 `scene_flow` 交付模块流程图（含 `sequence`/`call_graph`），其余轨道免重复画图，把全部上下文和推理预算集中于源码漏洞深潜**。
2. **历史 DTS / MR Diff / 缺陷资产反哺与点名认领兜底**：
   - 逐项核对 `purpose` 中**点名指派给本单元**的全部 DTS/MR 特征（含不易归类而指派给本轨兜底的跨轨缺陷特征，不得推诿），以及 `dts_context.homologous_dts`、`dts_context.matched_patterns`、`planning_handoff` 与 `asset_items` 中属于本轨道的历史缺陷模式。
   - **老 Bug 免疫红线（严防重报已修点）**：若当前版本目标位置已具备历史补丁的等价防御（满足 `already_fixed_check`，例如已在 6.1.6 合入加锁/置空/排空回调/释放修复），**严禁把已修复位置重报为风险**，在 `kind=check` 记为 `conclusion="excluded"` 并注明已具备补丁保护。
   - **跨路径同源潜伏猎杀（核心价值）**：提取历史缺陷的底层代码反模式（如“函数前部申请工作缓冲后多步校验失败提前 `return` 漏释放”、“浅释放外层结构体漏 `free` 子成员”、“某线程/进程释放或回收槽位时未同步排空异步回调导致另一执行流踩内存”），在当前 `owned_regions` 及其仓内关联路径中，**横向深挖同模块相邻兄弟命令、其他消息处理分支、同类资源结构体/对象池槽位或对称清理接口**是否存在自早期版本一直遗留未修的同源隐患！
   - 凡受历史 DTS、MR Diff 或缺陷资产启发完成核查或发现的新隐患，在 `matched_dts_pattern_ids` 填写对应 `dts_id`、`pattern_id` 或 `candidate_id`。
3. **运行态主战场优先序、全量分配函数对账与排除举证硬约束**：
   - **先看 `track_topology.file_directory`，严禁倒挂在 `init/` 启动目录刷屏**：打开 `track_topology` 后先读顶层 `file_directory`（已按运行态 `phase_hint="runtime"`、`uncleaned_exit_candidates` 与 `global_lazy_or_alloc_writes` 降序排列，`phase_hint="init_or_boot"` 排在最后）。**必须优先逐文件吃透 `phase_hint="runtime"` 的运行期业务路径**（建连/会话注册、收发包、命令处理、固件升级传包校验、断链/超时恢复），最后再核查 `init_or_boot` 启动初始化代码。对于进程/模块启动期一次性执行的 `ENOMEM` 早退，若启动失败直接退出进程或卸载模块由 OS/框架整体回收，记为 `excluded`（确有进程存活期残留的仅标 `Low`），**严禁把 `init/` 启动初始化分支刷成满屏 `High` 风险卡而挤占运行态深潜预算**。
   - **全量原生深潜（不受限于历史 DTS 与宏观底图）**：`risk_blueprint` 只是宏观锚点，**无论是否导入历史 DTS，`resource_lifecycle` 均须对 `owned_regions` 全部真实分配点做逐函数通读对账**：① 对照 `alloc_flow_slices`（含封装分配器与 `&out` 二级指针传出传播切片），重点紧盯 `uncleaned_exit_lines` 非空项，**用 `source-read` 一次读完 `[fn_start_line, fn_end_line]` 完整函数体**做全出口释放与所有权移交配平对账；② 对照 `resource_anchors` 的 `global_alloc_assign` 与全局/静态指针惰性初始化，核查并发建连/重入路径上的无锁“判空—申请—赋值”竞态覆写泄漏及恢复路径未判空覆写（严禁把 `if (P == NULL) P = alloc()` 按单线程假设直接放行）。若切片标有 `truncated` 或 `post_alloc_exits_truncated`，须按源码读完全函数。
   - **`excluded` 排除结论必须附真实释放/接管/加锁引文（严禁空口放行）**：允许按 `repository-local-v1` 穿透追踪跨文件释放点、Callee 分配传出点、调用链并发度及上游 1–2 层调用方。在将分配切片或高危出口记为 `kind=check, conclusion="excluded"` 时，**`evidence_quotes` 必须逐字贴出真实的释放语句（如 `free(buf)` 或 `goto` 目标标签下的释放行）、所有权挂接语句（如 `ctx->buf = buf`）或覆盖完整“判空-申请-赋值”窗口的锁/单线程启动证据**（诊断/故障注入命令带重入保护的故意泄漏亦引用对应保护代码排除）；严禁未读到释放代码就凭主观臆测排除。
   - **分配函数完备性自检（`uncovered_alloc_functions`）**：`resource_lifecycle` 调用 `work-finish` 时系统会返回非阻断对账提示 `uncovered_alloc_functions`（列出 `owned_regions` 内尚未被任何 `check.sites` 或 `risk.source_evidence` 覆盖的分配函数）；结束前须优先补查其中运行态函数，预算耗尽时对未查完项显式写 `conclusion="unresolved"` 说明缺口。
4. **两级实证梯度（直通打桩工作台）**：
   - **`static_proven`**：源码铁证完全闭环（触发路径、泄漏/回滚缺失/跨流交错/释放后踩踏机制与量化后果均可由静态证据证成）。
   - **`needs_runtime_verification`**：已锁定具体泄漏路径、回滚缺口或悬空访问窗口，但受限于运行时内部失败分支、线程/进程调度、外部库回调排空语义或底层故障条件无法纯静态 100% 敲死；**必须附带可执行的 `instrumentation.points` 桩点配方**（指定 `fault_return` / `race_window_delay` / `leak_counter_probe` / `memory_poison_or_bound_probe`）及配对 `kind=test_case, purpose="risk", execution_readiness="needs_instrumentation"`，供报告高亮并一键流入 `dsh-pangea` 打桩工作台实锤。
5. **确定性引文与结构核对**：
   - 本任务对 `kind=check`（`behavior-check-v2`）与 `kind=risk`（`behavior-risk-v3`）在 `result-write` 时核对 JSON 必填结构、`source_evidence` 真实行号、`evidence_quotes` 逐字存在性，以及 `needs_runtime_verification` 风险的 `instrumentation.points` 完整性。
   - 出现 `warnings` 诊断提示时保留已保存正文，按返回的具体字段错误与真实源码片段在本轮通过 `result-supersede` 局部修正。
   - 零风险在底图实体、本轨高危切片（如 `alloc_flow_slices`、`global_alloc_assign`）与指派 DTS 均已附真实引文交代结论时完全合法；时间预算不足时对未查完实体显式写入 `conclusion="unresolved"` 说明缺口即可正常 `work-finish`。

## 二、标准数据契约

### 1. 轨道检查账本（`kind=check`，`behavior-check-v2`）

```json
{
  "format_version": "behavior-check-v2",
  "check_id": "CHK-MEM-001",
  "track": "memory_corruption",
  "entity_ref": "MEM-001",
  "matched_dts_pattern_ids": ["DTS20260001"],
  "object": "兄弟通道管理命令 chan_reset 中释放 chan->io_ctx 与异步完成回调的生命周期互斥",
  "sites": ["repo:src/chan.c:112-135", "repo:src/io_cb.c:64-79"],
  "hypothesis": "对照 DTS20260001 在 sess_close 已补齐排空等待的特征，核查兄弟路径 chan_reset 是否仍在未排空 io_cb 时提前归还 io_ctx 至对象池导致跨线程踩内存",
  "conclusion": "confirmed",
  "reasoning": "sess_close (sess.c:140) 已合入 drain_io_cb 保护（已排除老单）；但兄弟路径 chan_reset (chan.c:124) 直接调用 pool_put(g_ctx_pool, chan->io_ctx) 且未置空，接收线程在 io_DoneCb (io_cb.c:71) 无世代号与锁保护直接写 io_ctx->status，重分配后必踩新连接内存",
  "evidence_quotes": [
    {"ref": "repo:src/chan.c:112-135", "snippet": "pool_put(g_ctx_pool, chan->io_ctx);"}
  ],
  "risk_ids": ["R-MEM-001"]
}
```

### 2. 两级实证风险（`kind=risk`，`behavior-risk-v3`）

样例 A（`resource_lifecycle` · LSan 局部堆内存异常早退泄漏，`static_proven`）：

```json
{
  "format_version": "behavior-risk-v3",
  "risk_id": "R-RES-001",
  "track": "resource_lifecycle",
  "verification_status": "static_proven",
  "severity": "High",
  "title": "parse_cfg_entry 校验版本号失败提前 return 漏释放 vos_malloc 分配的 tmp_buf 导致堆内存泄漏",
  "trigger": "传入合法长度但版本号不匹配的 TLV 配置项触发 parse_cfg_entry",
  "interleaving_or_path": [
    "parse_cfg_entry (cfg.c:84) 调用 tmp_buf = vos_malloc(len) 申请堆缓冲区",
    "cfg.c:91 校验 hdr->ver != CFG_VER_V2 命中错误分支，直接 return -EINVAL，未跳转 err_free 释放 tmp_buf"
  ],
  "system_result": "每次收到非法版本配置请求均永久泄漏 len 字节堆内存，LSan 报 Direct leak，长稳运行耗尽进程内存",
  "external_observation": "开启 ASAN_OPTIONS=detect_leaks=1 构造非法版本号请求后退出或查堆计数，LSan 报告 parse_cfg_entry (cfg.c:84) Direct leak",
  "exclusion_condition": "上游调用方统一由 arena/bump allocator 托管 tmp_buf 且在请求结束时整块回收",
  "source_evidence": ["repo:src/cfg.c:82-95"],
  "evidence_quotes": [
    {"ref": "repo:src/cfg.c:82-95", "snippet": "tmp_buf = vos_malloc(len);"},
    {"ref": "repo:src/cfg.c:82-95", "snippet": "return -EINVAL;"}
  ],
  "linked_test_case_ids": ["TC-RISK-RES-001"]
}
```

样例 B（`memory_corruption` · 跨流释放后踩内存，`needs_runtime_verification`）：

```json
{
  "format_version": "behavior-risk-v3",
  "risk_id": "R-MEM-001",
  "track": "memory_corruption",
  "verification_status": "needs_runtime_verification",
  "severity": "Critical",
  "entity_ref": "MEM-001",
  "matched_dts_pattern_ids": ["DTS20260001"],
  "title": "通道重置提前归还 io_ctx 槽位与晚到异步回调并发写入导致跨连接踩内存",
  "trigger": "通道处于 IO_INFLIGHT 时执行 chan_reset 归还 io_ctx，同时底层回包触发 io_DoneCb 写入旧指针",
  "interleaving_or_path": [
    "管理线程在 chan_reset (chan.c:124) 未排空回调即调用 pool_put(g_ctx_pool, chan->io_ctx)，该槽位被新连接重分配",
    "接收线程在 io_DoneCb (io_cb.c:71) 持残留裸指针解引用写入 io_ctx->status = DONE，踩坏新连接上下文"
  ],
  "system_result": "新分配该池槽位的连接结构体字段被静默篡改，引发后续异构数据破坏或进程崩溃",
  "runtime_uncertainty": "底层传输层 reset_ep 是否同步阻塞等待全部在途 io_DoneCb 执行完毕在冻结源码外",
  "external_observation": "在 pool_put 前对槽位填充毒化标记并注入 50ms 延迟窗口并发回包，ASAN/毒化断言捕获释放后写（UAF memory stomp）",
  "exclusion_condition": "reset_ep 在返回前已同步排空所有在途回调线程且禁止新回调入队",
  "source_evidence": ["repo:src/chan.c:118-130", "repo:src/io_cb.c:66-76"],
  "evidence_quotes": [
    {"ref": "repo:src/chan.c:118-130", "snippet": "pool_put(g_ctx_pool, chan->io_ctx);"},
    {"ref": "repo:src/io_cb.c:66-76", "snippet": "io_ctx->status = IO_STATUS_DONE;"}
  ],
  "instrumentation": {
    "points": [
      {
        "id": "INJ-001",
        "kind": "memory_poison_or_bound_probe",
        "repo_id": "repo",
        "path": "src/chan.c",
        "symbol": "chan_reset",
        "action": "在 pool_put(g_ctx_pool, chan->io_ctx) 后填充 0xdeadbeef 毒化字节并注入 50ms 延迟窗口",
        "observe": "ASAN 捕获 heap-use-after-free write 或毒化守卫检测到释放后写踩内存"
      }
    ]
  },
  "linked_test_case_ids": ["TC-RISK-001"]
}
```
