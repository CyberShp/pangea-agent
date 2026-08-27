---
description: 启动或继续一次 PANGEA C/C++ 模块分析
---
# module_analysis

定位最小核心源码范围并创建新 Run。严格执行 CLI action：`dispatch_agent` 创建对应子 Agent，`continue_agent` 恢复 action 自带的同一任务；每个 action 通过 adapter 校验后再提交，`status=invalid` 时按 `repair_action` 原地修复。同时最多 8 个。持续到报告生成或真实 `UNRESOLVED`，不扫描历史 Run 猜测恢复对象，不代写语义结果。
