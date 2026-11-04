# Planning 仅分配职责

## 目标与边界

Planning 读到足以确定业务单元、主责、必要另一侧与待核实事项就停止。单元应能独立分析连贯业务，不按文件、函数数量机械切块，也不设置单元数量上限。详细调用链、分支与状态组合、风险结论、流程和测试设计由 Analysis 完成。

Planning 保留必要证据和未知边界，不要求先消除所有不确定性。purpose 说明业务责任与必要挂接点；相关 note/unresolved 用返回的 unit_id 建立 relates_to，真正共同适用的说明才使用空关系。Coverage 的真实缺口、函数内部零计数分支及零新增合法性保持原合同，不增加普通功能补测义务。

## 结构化交接

新 Planning task 冻结 `planning_policy=allocation-only-v1`。接受规划时，每个 Analysis task 获得独立授权输入 `planning_handoff`，通过原有绑定与分页 input-read 读取，不把全部规划内容塞进每个 task。

交接包含 Planning 来源 binding 与 revision、当前单元原始 unit_plan，以及相关有效记录。每条记录保留 record_id、kind、body、evidence、relates_to、supersedes、created_revision，不把 unresolved 改成普通说明。

- 空 relates_to：global，发给每个单元。
- 指向 unit_id 或该单元任一历史 plan_record_id：只发给相关单元；一个记录可关联多个单元。
- 未知目标或非标准关系：原样保留并标记 unresolved_relation，不猜测相关性、不静默丢失。worker 自行核对，不将该标记解释为全局适用。
- supersedes 沿用结果存储的当前有效记录规则；单元修改只交接当前 unit_plan，旧 record handle 仍可用于关系定位。

Python 仅做显式标识的确定性路由，不总结、修正或判定证据含义，不增加语义门禁。Analysis 必须重新核查实质证据，但不用重复目录巡检。

## 兼容与验证

已有 task 无新 policy 时不生成新交接输入，不追补旧合同。已有 Analysis task 不重写。无审核主链保持 Planning → Analysis → 报告；不派发替代 worker 或增加审核轮次。

回归入口：

- `python verification/planning_handoff.py`：真实 Graph 派发、task-open 与分页 input-read、全局/单元/共享/未知关系、unresolved、supersedes、单元修订与旧合同。
- `python verification/planning_instruction_checks.py`：Desktop CLI、DSH、OpenCode 的 Planning/Analysis 职责分离，旧 Claude Graph 不变。
- `python tools/sync_worker_prompts.py --check`：共有指令同步。

以上证明路由、读取、兼容和指令装载，不代表真实模型的耗时、token 或业务分析质量已经通过验收。真实 provider 对照仍应记录 Planning 阅读/写入轨迹、耗时与 token，再检查 Analysis 是否收到并核实必要边界、是否维持原有覆盖语义；不能以仅减少字数推断质量不降。
