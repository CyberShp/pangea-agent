---
description: 启动或继续一次 PANGEA C/C++ 模块分析
---
# module_analysis

定位最小核心源码范围并创建新 Run。严格按 CLI action 派发相应子 Agent，每个 action 绑定真实任务 ID、通过 adapter 校验后再提交；同时最多 8 个。持续到报告生成或真实 `UNRESOLVED`，不扫描历史 Run 猜测恢复对象，不代写语义结果。
