# 风险四轨深潜职责（risk-method-v2 · Stage 2）

本场景优先于 `scene_common_generation`；具体语言检查规程见冻结的 `c_cpp_risk_v2.md` 或 `lua_risk_v2.md`。

## 一、核心原则：运行态优先四轨深潜 + 历史特征猎变种 + 两级实证 + 零推诿闭环

1. **本轨主责 + 跨轨实锤零推诿 + 单轨画图**：读取 `task.risk_track`（`error_rollback` | `concurrency_timing` | `resource_lifecycle` | `memory_corruption`）、`risk_blueprint`、`track_topology`、`planning_handoff`、`dts_context` 与 `asset_items`。日常扫描聚焦本轨防重复；**但四轨并发无二次转办——若你在源码中已铁证锁定某处跨轨真缺陷（如查锁时证成指针覆写泄漏，或查泄漏时证成先释放后注销 UAF），严禁以“归他轨管”丢弃，须直接提交 `kind=risk` 并填真实 `track` 落袋**。**仅 `purpose` 指定为主干流程 owner 的单元按 `scene_flow` 交付 `flow`，其余轨道免画图**。
2. **历史 DTS / MR Diff 反哺与认领兜底**：
   - 逐项核对 `purpose` 点名指派给本单元的全部 DTS/MR 特征（含兜底跨轨特征）及 `dts_context` / `planning_handoff` / `asset_items` 同轨模式；凡受启发核查或命中，填 `matched_dts_pattern_ids`。
   - **老单免疫**：已具备补丁等价防御（满足 `already_fixed_check`）的位置记 `kind=check, conclusion="excluded"`，严禁重报。
   - **横向猎变种**：提炼历史反模式（前部申请缓冲后多步校验失败早退漏释放、浅释放漏子成员、释放槽位未排空回调踩内存），在 `owned_regions` 横向深挖兄弟命令、同类结构体/槽位与对称清理接口。
3. **运行态优先序、全量分配对账与排除举证硬约束**：
   - **先看 `track_topology.file_directory`（反 `init/` 刷屏）**：优先吃透 `phase_hint="runtime"` 运行期文件（建连会话、收发包、命令处理、升级传包校验、超时断链恢复），最后查 `init_or_boot`。启动期一次性 `ENOMEM` 随后退出进程由 OS 回收的路径记 `excluded`（确有存活期残留仅标 `Low`），**严禁在 `init/` 刷满 `High` 卡挤占运行态预算**。
   - **全量原生深潜**：无论是否导入 DTS，`resource_lifecycle` 均须对 `owned_regions` 全部真实分配点逐函数对账：① 紧盯 `alloc_flow_slices`（含封装分配器与 `&out` 传出切片）及非空 `uncleaned_exit_lines`，**用 `source-read` 读完 `[fn_start_line, fn_end_line]` 全函数**核对全出口释放与挂接；② 核查 `global_alloc_assign` 并发建连/重入路径无锁“判空—申请—赋值”竞态覆写泄漏及恢复路径未判空覆写（严禁将 `if (P == NULL) P = alloc()` 按单线程直接放行）。见 `truncated` 时补读全函数。
   - **`excluded` 必填真实释放/挂接/加锁引文**：将分配切片或高危出口记 `excluded` 时，**`evidence_quotes` 必须逐字贴出真实释放行、所有权挂接行（如 `ctx->buf = buf`）或覆盖完整判空-申请-赋值窗口的锁/单线程启动/诊断重入保护代码**，严禁空口放行。
   - **完备性对账（`uncovered_alloc_functions`）**：结束前核对 `work-finish` 提示的未覆盖分配函数，优先补查运行态项；预算耗尽对未查项写 `conclusion="unresolved"`。
4. **两级实证梯度**：`static_proven`（静态铁证闭环）；`needs_runtime_verification`（受运行时调度/内部失败限制无法纯静态敲死，必填 `runtime_uncertainty`、可执行 `instrumentation.points` 及配对 `kind=test_case, purpose="risk", execution_readiness="needs_instrumentation"`）。
5. **确定性核对**：`result-write` 校验必填结构、真实行号与逐字 `evidence_quotes`；遇 `warnings` 用 `result-supersede` 局部修正。全部高危切片与 DTS 均附引文交代结论时零风险合法。

## 二、标准数据契约

### 1. 轨道检查账本（`kind=check`，`behavior-check-v2`）

```json
{
  "format_version": "behavior-check-v2",
  "check_id": "CHK-MEM-001",
  "track": "memory_corruption",
  "entity_ref": "MEM-001",
  "matched_dts_pattern_ids": ["DTS20260001"],
  "object": "chan_reset 释放 chan->io_ctx 与异步回调互斥",
  "sites": ["repo:src/chan.c:112-135", "repo:src/io_cb.c:64-79"],
  "hypothesis": "对照 DTS20260001 核查兄弟路径 chan_reset 是否未排空 io_cb 即归还 io_ctx",
  "conclusion": "confirmed",
  "reasoning": "sess_close 已合入 drain_io_cb 保护；但 chan_reset 直接 pool_put 未排空未置空，io_DoneCb 无锁写 io_ctx->status 必踩复用内存",
  "evidence_quotes": [{"ref": "repo:src/chan.c:112-135", "snippet": "pool_put(g_ctx_pool, chan->io_ctx);"}],
  "risk_ids": ["R-MEM-001"]
}
```

### 2. 两级实证风险（`kind=risk`，`behavior-risk-v3`）

样例 A（`resource_lifecycle` · 早退漏释放，`static_proven`）：

```json
{
  "format_version": "behavior-risk-v3",
  "risk_id": "R-RES-001",
  "track": "resource_lifecycle",
  "verification_status": "static_proven",
  "severity": "High",
  "title": "parse_cfg_entry 版本校验失败提前 return 漏释放 tmp_buf",
  "trigger": "传入合法长度但版本号不匹配的 TLV 配置触发 parse_cfg_entry",
  "interleaving_or_path": [
    "cfg.c:84 调用 tmp_buf = vos_malloc(len) 申请工作缓冲",
    "cfg.c:91 命中 hdr->ver != CFG_VER_V2 直接 return -EINVAL，漏释放 tmp_buf"
  ],
  "system_result": "单次拒单泄漏 len 字节（最大 16KB），随每次非法请求呈 O(N) 无界线性累加，进程不重启永久驻留耗尽内存",
  "external_observation": "构造非法版本请求后 LSan 报 parse_cfg_entry Direct leak",
  "exclusion_condition": "上游 arena 分配器在请求结束整块回收",
  "source_evidence": ["repo:src/cfg.c:82-95"],
  "evidence_quotes": [
    {"ref": "repo:src/cfg.c:82-95", "snippet": "tmp_buf = vos_malloc(len);"},
    {"ref": "repo:src/cfg.c:82-95", "snippet": "return -EINVAL;"}
  ],
  "linked_test_case_ids": ["TC-RISK-RES-001"]
}
```

样例 B（`memory_corruption` · 释放后跨流踩内存，`needs_runtime_verification`）：

```json
{
  "format_version": "behavior-risk-v3",
  "risk_id": "R-MEM-001",
  "track": "memory_corruption",
  "verification_status": "needs_runtime_verification",
  "severity": "Critical",
  "entity_ref": "MEM-001",
  "matched_dts_pattern_ids": ["DTS20260001"],
  "title": "chan_reset 提前归还 io_ctx 槽位与晚到回调并发写踩内存",
  "trigger": "IO_INFLIGHT 期间执行 chan_reset 同时底层回包触发 io_DoneCb",
  "interleaving_or_path": [
    "chan.c:124 未排空回调即 pool_put(g_ctx_pool, chan->io_ctx)，槽位被新连接复用",
    "io_cb.c:71 持旧指针写 io_ctx->status = DONE 踩坏新连接内存"
  ],
  "system_result": "复用该池槽位的新连接字段被静默篡改导致崩溃",
  "runtime_uncertainty": "底层 reset_ep 是否同步排空在途 io_DoneCb 在冻结源码外",
  "external_observation": "槽位毒化 + 50ms 延迟并发回包，ASAN 捕获 UAF write",
  "exclusion_condition": "reset_ep 返回前已同步排空全部在途回调",
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
        "action": "pool_put 后填充 0xdeadbeef 并延迟 50ms",
        "observe": "ASAN 捕获 heap-use-after-free write"
      }
    ]
  },
  "linked_test_case_ids": ["TC-RISK-001"]
}
```
