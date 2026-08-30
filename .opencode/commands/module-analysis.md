---
description: 启动或继续一次 PANGEA C/C++ 或 Lua 模块分析
---
# module-analysis

确定用户指定仓库和最小核心 `source_scope`，创建新 Run 后严格执行 CLI 返回的 action。每个 action 必须绑定真实子任务 ID、通过 adapter 校验后再 settle；一次最多并发 8 个。Review 先独立检查，再由同一 Reviewer 按 `continue_agent` 对照首轮结果。持续到报告生成或出现真实 `UNRESOLVED`，不扫描历史 Run 猜测恢复目标，不自行修改语义结果。
