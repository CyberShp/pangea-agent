# PANGEA DSH 运行规则

本文件只负责 DSH Desktop 中的 PANGEA action 调度。主 Agent 不分析源码，不填写或修正语义结果。

## 新 Run

当前会话没有明确 `run_id` 时，准备新 Run 只允许在 `pangea-data/repositories/` 下列目录、按文件名搜索或 grep 符号，用于确定仓库和最小 `source_scope`。创建 Run 前不得调用 Read、分段读取或通读业务源码，不得统计文件行数后继续展开阅读。不得列举或读取 `pangea-data/runs/`、历史契约、历史报告或 Companion 历史状态；不得读取 PANGEA 的 CLI、graph、schema 或其他内部实现来学习调用方法；不得调用 `pangea_status` 猜测恢复对象。

从用户要求确定仓库、目标、最小 `source_scope`、重点、资产 ID 和可选用例示例后，直接调用 `pangea_run_create`。业务源码的内容理解和调用链分析由 Planning/Analysis Agent 完成。该工具负责创建并删除临时契约，不得自行写 pending contract。`pangea_status` 只能使用当前会话已持有或用户明确给出的 `run_id`。

`pangea_run_create` 和 action 工具会加载当前工作区的 `src`。主 Agent 不用 shell 启动 PANGEA，不用 `--help`、版本探测或源码阅读代替正式工具调用。

## 调度

工具返回 action 后，对每个 action 直接调用 `pangea_action_dispatch` 并传入原始 `action_id`。`dispatch_agent` 才按 role 创建新任务：

- `planning`：`planning-worker.md`
- `analysis`：`analysis-worker.md`
- `review`：`review-worker.md`
- `asset_extraction`：`asset-extraction-worker.md`

`continue_agent` 必须恢复 action 自带的 `task_id`。Comparison review 续接原 Reviewer；正常 workflow 的 `targeted_closure` 续接该单元首轮 `analysis-worker`，不得创建替代 worker。

主 Agent 不自行调用通用 `subagent`，不读取 task，也不重写子 Agent 提示。最多同时派发 8 个 action；8 是并发数量，不限制 Run 的总单元数。子 Agent 不得继续派发。

派发 action 后如果尚未收到子 Agent 完成通知，主 Agent 立即结束当前回合，把控制权交还 DSH；不得调用通用等待/轮询工具，不得反复输出“继续等待”，也不得在同一回合循环检查子 Agent。DSH 会在子 Agent 结束后注入完成通知并唤醒主 Agent。若等待接口只返回 unchanged/idle 而没有完成通知，视为没有新事件并结束当前回合；不得无限等待。

`pangea_action_dispatch` 会自动把 action 与 DSH 真实任务 ID 绑定。新 task 还包含同一个不可变 `action_id`，子 Agent 的一行完成报告会原样回显它。子 Agent 回合结束后，主 Agent 的第一且唯一动作是对完成报告中这个 exact `action_id` 调用 `pangea_action_settle`；该工具在同一次调用中校验当前结果并推进 Workflow。不得先读取结果、查询状态、检查其他 Agent 或发送普通消息。并行 Agent 的完成通知即使同时到达，也必须逐条保留各自的 exact `action_id`，先处理当前 action 的 settle 返回，再处理下一条。不得根据 DSH 子任务 UUID、单元名、通知先后或记忆猜测待结算 action；已经 settle 成功的 action 不得因另一个修复完成而重新当成待处理 action。历史 task 没有 `action_id` 时，才使用 `pangea_action_dispatch` 已保存的 action 与 DSH 任务绑定：

独立 `pangea_action_validate` 已停用，不再执行校验，也不改变 action 状态；误调用只会返回 `status=settle_required`。收到该状态时不得重试 validate，直接对同一 `action_id` 调用 `pangea_action_settle`。

- 返回下一批 `agent_actions` 或完成状态：按返回值继续，不再补调 validate。
- 返回 `validation.status=invalid`：该次 settle **没有自动派发修复**。返回中的 `repair_dispatched=false`、`next_required_tool=pangea_action_dispatch` 和 `repair_action.status=pending` 是唯一真实状态。主 Agent 的下一个且唯一个工具调用必须是 `pangea_action_dispatch`，并原样传入 `next_required_action_id` / `repair_action.action_id`；该调用成功返回真实子任务信息后，才算已派发。不得因 progress 中原 action 仍为 `dispatched`、因 action_id 与首轮相同，或因 settle 返回了 `agent_actions` 就声称“repair 已自动 dispatch”。

  修复 action 必须恢复同一个 `task_id`，把返回的 `error`（包括有界 `details`、`detail_count` 和截断标记）交回该任务，只修正同一 `result_path` 后再次 settle。错误很多时让原 Agent 以 `result_skeleton_path` 为唯一字段基线，保留可用语义后重建结构；不得套用旧版或其他项目的字段，不得用普通 `send_message` 或通用子 Agent 代替 `repair_action`。修复派发后立即结束当前回合，收到该修复任务的完成通知后再 settle 同一 action。

  并行 Agent 的其他完成通知只能按各自 exact `action_id` 排队。当前 action 返回 invalid 后，在它的 repair 已真实 dispatch 之前，不得 settle 另一个已完成 action、读状态、猜测哪个任务已推进，也不得跳到下一条 repair。先完成这一次 `settle -> dispatch repair`，再处理队列中的下一个完成通知。多个 repair 完成后，逐个 settle 它们各自回显的 action；不得改成等待另一个已经 settled 的 action，也不得在仍有已完成但未 settle 的 exact action 时结束会话。
- 返回 `validation.attention_required=true`：说明同一结构错误已连续出现 3 次，或该 action 累计结构修复已达到 6 次。主 Agent 停止盲目重试该 Run，保留现场并把它记为“未完成”，不得让 Python 把 Run 判死，也不得把占位报告当成正式报告。

Run/action/task 丢失、冻结输入损坏、`continue_agent` 缺少约定的 `task_id` 或 Workflow 返回未持久化 action 才属于流程错误。无法解析或缺少下游必需结构的结果由当前 worker 原地修复；内部编号悬空、evidence 超出声明单元、`basis` 声明与实际链接不一致，以及 Coverage 取舍、finding 是否成立等分歧，由 Workflow 保留原结果并标记降级。这些检查只说明契约关联状态，不裁决风险、流程或用例语义。返修时保留已有有效语义内容，编辑方法由当前 Agent 自己选择；不得把语义判断交给 Python 或脚本。主 Agent 不读取或代改结果，不得换 worker。重试是否暂停由 DSH 主 Agent 根据 `attention_required` 决定，Python 只记录次数和提示。

Worker 结束前可调用 `check-result-json --task`。DSH 在 POSIX 工作区固定使用 `.venv/bin/python -m pangea_agent.cli.main check-result-json --task '<当前 task JSON 路径>'`，Windows 工作区使用 `.venv\Scripts\python.exe`；不要用 `PYTHONPATH` 或系统 `python3` 绕过项目运行环境。该命令只读取 task 指向的结果，确认 JSON 能否被下游消费，并以 `advisories` 提示内部编号、声明链接和证据路径问题。`submission_ready=false` 时由当前 Agent 修正无法读取的结构；`submission_ready=true` 时允许结束回合，`status=WARN` 由 settle 保留为降级提示并继续流程。它不判断语义，任何内容修正仍由当前 Agent 自己决定。

Review 固定分为同一 Reviewer 的两个 checkpoint：`independent_review` 不提供首轮结果；`comparison_review` 才提供首轮结果做对照。定向补齐后直接聚合，不启动新的复核 Agent。

资料提取和方法论提炼由资产插件管理。历史缺陷提取使用
`asset-extraction-worker.md`，完成后等待人工审核，不自动批准。方法论提炼只接受已批准历史缺陷，
使用 `methodology-worker.md` 写入 task 的 `result_path`；资产插件再调用
`methodologies complete-derivation --task <task_path>` 校验并登记为待确认候选，不自动启用。

结果骨架由 Workflow 创建，主 Agent 不得另建、替换或用占位内容推进流程。最终必须同时满足 Run `lifecycle_status=complete`、`report-complete.json` 完成标记以及实际存在的 `report.md`、`report.html`；单独存在的报告文件不是正式产物。
