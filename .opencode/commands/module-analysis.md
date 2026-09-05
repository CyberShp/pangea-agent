---
description: 启动或继续一次 source-first 模块分析
---
# module-analysis

收集用户确认的 repository、target、source_scope、focus、asset_ids 和用例示例后，
调用 source-first Graph 的 Run create。按返回的 exact action_id 调度 planning、analysis、
independent review、同 Reviewer comparison 和必要 closure；每个 task 只读冻结
source-index/read/search，并通过当前 result_path 增量写 notes、work-finish。每次
settle 只消费对应 action_id，revision 冲突回读后在同一 task 修复。

旧 legacy Run 仅由 reader 兼容展示；没有 source-first workflow_version 时不得猜测
恢复。报告必须同时存在 report.md、report.html、report-complete.json，质量状态为
UNRESOLVED 时如实展示，不把空投影解释为零。
