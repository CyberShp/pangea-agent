---
description: 启动或继续一次 PANGEA C/C++ 模块分析
---
# module-analysis

确定用户指定仓库和最小核心 `source_scope`，创建新 Run 后严格执行 CLI 返回的 action。每个 action 必须绑定真实子任务 ID。`adapter validate` 返回 `status=valid` 后再 settle；返回 `status=invalid` 时不是 Run 失败，必须立即执行返回的 `repair_action`，续接其中同一个 `task_id`，把校验错误交回原 worker 修同一 `result_path` 后再次 validate。普通结果校验失败不得询问用户是否新建 Run或修改流程代码。一次最多并发 8 个。Review 先独立检查，再由同一 Reviewer 按 `continue_agent` 对照首轮结果；targeted closure 续接受影响单元的原 analysis worker。持续到报告生成或出现真实 `UNRESOLVED`，不扫描历史 Run 猜测恢复目标，不自行修改语义结果。
