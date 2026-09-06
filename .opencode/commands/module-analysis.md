---
description: 启动或继续一次 source-first 模块分析
---
# module-analysis

收集用户确认的 repository、target、source_scope、focus、asset_ids 和用例示例后，
调用 pangea_run_create 创建 source-first Graph Run；仅恢复用户明确选择的 Run 时调用
pangea_run_resume。按返回的 exact action_id 调度 planning、analysis、
independent review、同 Reviewer comparison 和必要 closure；每个 action 只通过
pangea_action_dispatch 派发，它会先用真实 OpenCode session ID bind，再发送任务，回合
结束后按同一个 exact action_id settle。每个 task 只读冻结 task/input/source 工具，
并通过当前 result 增量写 notes、work-finish；宿主管理保存顺序和版本，真实并发冲突
回读确认变化后在同一 task 修复。

新 Run 冻结 `behavior-test-v1`：以正常主干、业务分支、异常传播、清理恢复和真实
Coverage 补测用例为交付主体，不要求先建立 Risk。Analysis 与 Comparison 首次完整完成后
直接 settle，不追加固定的第二轮全文复读。

旧 legacy Run 仅由 reader 兼容展示；没有 source-first workflow_version 时不得猜测
恢复。报告必须同时存在 report.md、report.html、report-complete.json，质量状态为
UNRESOLVED 时如实展示，不把空投影解释为零。
