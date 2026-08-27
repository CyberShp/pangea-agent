# PANGEA DSH 运行规则

本文件只负责 DSH Desktop 中的 PANGEA action 调度。主 Agent 不分析源码，不填写或修正语义结果。

## 新 Run

从用户要求确定仓库、目标、最小 `source_scope`、重点、资产 ID 和可选用例示例。主 Agent 可确认路径存在，但不得在 Planning action 之前逐份读取源码或自行理解调用链。直接调用 `pangea_run_create`；该工具负责创建并删除临时契约。不得读取 CLI 源码、graph、schema 来猜调用方法，也不得自行写 pending contract。

当前会话没有明确 `run_id` 时，不得调用 `pangea_status`，不得读取或列举历史 Run 猜测恢复对象。`pangea_status` 只能使用当前会话已持有或用户明确给出的 `run_id`。

`pangea_run_create` 和 action 工具会加载当前工作区的 `src`。主 Agent 不用 shell 启动 PANGEA，不用 `--help`、版本探测或源码阅读代替正式工具调用。

## 调度

工具返回 action 后，对每个 action 直接调用 `pangea_action_dispatch` 并传入原始 `action_id`。`dispatch_agent` 才按 role 创建新任务：

- `planning`：`planning-worker.md`
- `analysis`：`analysis-worker.md`
- `review`：`review-worker.md`
- `asset_extraction`：`asset-extraction-worker.md`

`continue_agent` 必须恢复 action 自带的 `task_id`。Comparison review 续接原 Reviewer；正常 workflow 的 `targeted_closure` 续接该单元首轮 `analysis-worker`，不得创建替代 worker。

主 Agent 不自行调用通用 `subagent`，不读取 task，也不重写子 Agent 提示。最多同时派发 8 个 action；8 是并发数量，不限制 Run 的总单元数。子 Agent 不得继续派发。

`pangea_action_dispatch` 会自动把 action 与 DSH 真实任务 ID 绑定。子 Agent 回合结束后调用 `pangea_action_validate`：

- `status=valid`：调用 `pangea_action_settle`。
- `status=invalid`：按返回的 `repair_action` 恢复同一个 `task_id`，把 `error.message` 交回该任务，只修正同一 `result_path` 后再次 validate。
- settle 防御性返回 `validation.status=invalid` 时，执行同一恢复动作。

Run/action/task 丢失、冻结输入损坏、`continue_agent` 缺少约定的 `task_id` 或 Workflow 返回未持久化 action 才属于流程错误。普通 JSON、schema、引用、evidence、Coverage 或 finding 校验错误由当前 worker 原地修复。主 Agent 不读取或代改结果，不得换 worker。

Review 固定分为同一 Reviewer 的两个 checkpoint：`independent_review` 不提供首轮结果；`comparison_review` 才提供首轮结果做对照。定向补齐后直接聚合，不启动新的复核 Agent。

资料提取由资产插件管理。历史缺陷提取完成后等待人工审核，不自动批准。

结果骨架由 Workflow 创建，主 Agent 不得另建、替换或用占位内容推进流程。最终以 Run JSON 状态和实际存在的 `report.md`、`report.html` 为准。
