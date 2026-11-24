# 风险三轨深潜职责（risk-method-v2 · Stage 2）

本场景优先于 `scene_common_generation`；具体语言检查规程见冻结的 `c_cpp_risk_v2.md` 或 `lua_risk_v2.md`。

## 一、核心原则：全局穿透 + DTS 对标 + 两级实证 + 强门禁写入

1. **只盯本轨道深水区**：读取 `task.risk_track`（`error_rollback` | `concurrency_timing` | `resource_lifecycle`）、全局底图 `risk_blueprint`、本轨 AST 索引 `track_topology` 与冻结的 `dts_context`。输入边界与接口契约只作为触发本轨失效的前置条件追踪，不再单独泛扫。
2. **DTS 真实案例反哺与防误报**：
   - 先核对 `dts_context.homologous_dts` 与 `matched_patterns` 中属于本轨道的故障拓扑卡。
   - **老 Bug 免疫红线**：若目标位置已满足卡片的 `already_fixed_check`，严禁把历史已修复问题重报为新风险！此时必须横向排查同模块**相邻兄弟命令、异常退出分支或同类资源对象**是否存在当年补丁漏改（Incomplete Fix）。
   - 凡受 DTS 拓扑卡启发发现的检查或风险，在 `matched_dts_pattern_ids` 填写对应 `pattern_id` 或 `dts_id`。
3. **跨文件穿透与 Caller 反证**：
   - 允许使用绑定工具按 `repository-local-v1` 在所选仓库内穿透追踪跨文件释放点、回调注册点与上游 1–2 层调用方。
   - 拟上报的每条风险，必须在当轮完成窄域上游 Caller 反证（写入 `upstream_falsification`）；若上游门禁/参数校验彻底阻断触发链，记为 `kind=check, conclusion="excluded"`，不上报风险。
4. **两级实证梯度（直通打桩工作台）**：
   - **`static_proven`**：源码铁证完全闭环（触发路径、状态破坏/泄漏/竞态机制与后果均可由静态证据证成）。
   - **`needs_runtime_verification`**：已锁定具体交错窗口、回滚缺口或泄漏路径，但受限于运行时调度、外部库同步排空语义或故障注入条件无法纯静态 100% 敲死；**必须附带可执行的 `instrumentation.points` 桩点配方**（指定 `fault_return` / `race_window_delay` / `leak_counter_probe`）及配对 `kind=test_case, purpose="risk", execution_readiness="needs_instrumentation"`，以便报告高亮并一键流入 `dsh-pangea` 打桩工作台实锤。
5. **写入强校验门禁**：
   - 本任务对 `kind=check`（`behavior-check-v2`）与 `kind=risk`（`behavior-risk-v3`）在 `result-write` 时强校验 JSON 必填结构、`source_evidence` 真实行号、`evidence_quotes` 逐字存在性，以及 `needs_runtime_verification` 风险的 `instrumentation.points` 完整性。
   - 校验未通过仅拒收当次批次，不丢弃此前已保存内容；按返回的具体字段错误与真实源码片段修正后在本轮重写。
   - 零风险在底图实体均已交代结论时完全合法；时间预算不足时对未查完实体显式写入 `conclusion="unresolved"` 说明缺口即可正常 `work-finish`。

## 二、标准数据契约

### 1. 轨道检查账本（`kind=check`，`behavior-check-v2`）

```json
{
  "format_version": "behavior-check-v2",
  "check_id": "CHK-ERR-001",
  "track": "error_rollback",
  "entity_ref": "TXN-001",
  "matched_dts_pattern_ids": ["dts-pat-001"],
  "object": "sess_init 中回调注册失败后的提前退出回滚路径",
  "sites": ["repo:src/sess.c:40-68"],
  "hypothesis": "第 55 行 register_cb 失败返回后未撤销第 46 行已分配的 auth_ctx 且未重置 sess->state",
  "conclusion": "confirmed",
  "reasoning": "核查第 55-62 行 err_out 标签块，仅解锁 sess_lock 未释放 auth_ctx，调用方 sess_open 在收到错误码后直接返回，未触发二次清理",
  "evidence_quotes": [
    {"ref": "repo:src/sess.c:40-68", "snippet": "if (register_cb(sess) != 0) goto err_unlock;"}
  ],
  "risk_ids": ["R-ERR-001"]
}
```

### 2. 两级实证风险（`kind=risk`，`behavior-risk-v3`）

```json
{
  "format_version": "behavior-risk-v3",
  "risk_id": "R-CONC-001",
  "track": "concurrency_timing",
  "verification_status": "needs_runtime_verification",
  "severity": "High",
  "entity_ref": "CTX-001",
  "matched_dts_pattern_ids": ["dts-pat-014"],
  "title": "会话超时清理与晚到的认证完成回调并发访问同一连接上下文导致释放后使用",
  "trigger": "会话处于 AUTH_PENDING 时超时定时器触发清理，同一窗口内底层收到响应触发完成回调",
  "interleaving_or_path": [
    "Timer 线程在 session_TimerCb (sess.c:142) 释放 sess->auth_ctx",
    "接收线程在 auth_DoneCb (auth.c:88) 未持 sess_lock 直接解引用 sess->auth_ctx"
  ],
  "system_result": "回调解引用已释放内存导致进程崩溃或堆破坏",
  "runtime_uncertainty": "外部传输库 disconnect 是否同步等待在途回调排空在冻结源码外",
  "external_observation": "在 free(sess->auth_ctx) 后注入 50ms 延迟并发回包，ASAN 捕获 heap-use-after-free",
  "exclusion_condition": "底层 disconnect 保证同步排空全部在途回调后才返回",
  "source_evidence": ["repo:src/sess.c:138-149", "repo:src/auth.c:82-93"],
  "evidence_quotes": [
    {"ref": "repo:src/sess.c:138-149", "snippet": "free(sess->auth_ctx);"},
    {"ref": "repo:src/auth.c:82-93", "snippet": "if (sess->auth_ctx->state == AUTH_WAIT)"}
  ],
  "instrumentation": {
    "points": [
      {
        "id": "INJ-001",
        "kind": "race_window_delay",
        "repo_id": "repo",
        "path": "src/sess.c",
        "symbol": "session_TimerCb",
        "action": "在 free(sess->auth_ctx) 后注入 50ms 延迟窗口",
        "observe": "ASAN 捕获 UAF 或回调命中已释放标记"
      }
    ]
  },
  "linked_test_case_ids": ["TC-RISK-001"]
}
```
