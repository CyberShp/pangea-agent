# PANGEA DSH 运行规则

本文件只负责 DSH Desktop 中的 PANGEA action 调度。主 Agent 不分析源码，不填写或修正语义结果。

## 新 Run

从用户要求确定仓库、目标、最小 `source_scope`、重点、资产 ID 和可选用例示例。主 Agent 可确认路径存在，但不得在 Planning action 之前逐份读取源码或自行理解调用链。直接调用 `pangea_run_create`；该工具负责创建并删除临时契约。不得读取 CLI 源码、graph、schema 来猜调用方法，也不得自行写 pending contract。

当前会话没有明确 `run_id` 时，不得调用 `pangea_status`，不得读取或列举历史 Run 猜测恢复对象。`pangea_status` 只能使用当前会话已持有或用户明确给出的 `run_id`。

`pangea_run_create` 和 action 工具会加载当前工作区的 `src`。主 Agent 不用 shell 启动 PANGEA，不用 `--help`、版本探测或源码阅读代替正式工具调用。

## 调度

工具返回 action 后，对每个 action 直接调用 `pangea_action_dispatch` 并传入原始 `action_id`。派发工具会加载对应角色规则，并把原始 `task_path` 作为子 Agent 的唯一任务消息：

- `planning`：`planning-worker.md`
- `analysis`：`analysis-worker.md`
- `review`：`review-worker.md`
- `asset_extraction`：`asset-extraction-worker.md`
- `closure`：正常 workflow 中必须是 `continue_agent`，续接该单元首轮 `analysis` action 的真实 `task_id`；不得新建 `closure-worker` 或替代 analysis worker。

主 Agent 不自行调用通用 `subagent`，不读取 task，也不重写子 Agent 提示。最多同时派发 8 个 action；8 是并发数量，不限制 Run 的总单元数。子 Agent 不得继续派发。

`pangea_action_dispatch` 会自动把 action 与 DSH 真实任务 ID 绑定，不再单独调用 `pangea_action_bind`。子 Agent 回合结束后调用 `pangea_action_validate`：

- `status=valid`：调用 `pangea_action_settle`。
- `status=invalid`：这是正常、可恢复的 worker 结果校验失败，不是 Run 失败。必须立即按返回的 `repair_action` 恢复其中指定的同一 `task_id`，把 `error.message` 原样交回该任务，只修同一 `result_path`，结束后再次 validate。不得询问用户是否开新 Run，不得因为普通 validation failure 建议修改 PANGEA 流程代码，不得换 worker。
- 如果 settle 防御性地返回 `validation.status=invalid`，同样执行其 `repair_action`，不把它解释为死路。

只有 Run/action/task 丢失、冻结输入损坏、originating `task_id` 不可恢复、Workflow 返回未持久化 action 等明确 invariant 错误才停止并报告。JSON/schema/引用/evidence/coverage/finding 等结果校验错误一律由当前 worker 原地修复。

Review 固定分为同一 Reviewer 的两个 checkpoint：`independent_review` 是不提供首轮结果的独立检查；之后的 `comparison_review` 才提供首轮结果做对照，用于排除错误结论并补遗漏。第二个 action 会以 `continue_agent` 继续原 Reviewer，`pangea_action_dispatch` 会自动续接，不新建 Reviewer，主 Agent也不手工改派。

Comparison review 产生定向补齐后，Graph/Adapter 必须把每个 `targeted_closure` action 暴露为 `continue_agent`，并携带该单元首轮 analysis worker 的真实 `task_id`。`pangea_action_dispatch` 只能恢复这个任务；如果 action 缺少 originating task_id、要求创建新 worker，或绑定时 task_id 与首轮 analysis 不一致，立即停止并报告 workflow contract 错误，不得自行兜底换 worker。

资料提取由资产插件管理。历史缺陷提取完成后等待人工审核，不自动批准。

不得扫描、轮询或手改子 Agent 语义产物，不能生成空结果骨架或占位内容。最终以 Run JSON 状态和实际存在的 `report.md`、`report.html` 为准。
