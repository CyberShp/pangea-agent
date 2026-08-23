---
description: 按用户当前自然语言要求执行 PANGEA 模块分析
---
# module_analysis

这是用户启动 PANGEA 模块分析的主要入口。用户不需要准备、查看或维护 task contract，也不需要执行任何 PANGEA CLI 命令。

收到用户的模块分析要求后：

1. 先确定目标源码仓和模块核心实现范围。`source_scope` 只填写该模块明确的核心源码文件或最小实现目录；不得使用仓库根目录、明显过大的父目录，也不得因为目标关键词在其他源码中出现就扩大 `source_scope`。
2. 如果用户只给模块名，先定位该模块的入口文件、接口文件和对应实现文件，再形成最小 `source_scope`。直接调用者、配置、规格和已有测试属于上下文，不作为核心源码范围。
3. 新主 Agent 会话首次执行模块分析时，由主 Agent 在 PANGEA 内部生成任务契约并使用 `module-analysis` 创建新 Run；不要要求用户提供 contract 文件，不要在项目根目录或 `pangea-data/` 一级目录创建 task contract，也不要扫描历史 Run 决定恢复目标。
4. `module-analysis` 或 `resume-run` 返回的每条 `action=<JSON>` 是唯一的派发依据。按 action 执行 `dispatch_agent` 或 `continue_agent`，消息只包含 `task_path`，不根据 `phase` 或 Agent 回复文本推测下一步。
5. analysis-worker 每回合只执行 `task.stage`，写入相同的 `completed_stage`，并执行 `validate-worker-result` 到 `PASS`。JSON/schema 失败由同一 Worker 在同一 result/attempt 内修正，不属于正式 rework，也不得由主 Agent 用临时脚本代修。
6. `validate-worker-result` 或 `check-review-artifact` 的 `PASS` 会完成当前 task 已绑定的会话；该 Agent 报告回合结束后，主 Agent 按 `after_completion=resume_run` 只恢复同一 `run_id` 和 `data_root`，不得自行记录完成、轮询 Agent、读取产物或把 schema FAIL 的结果留给后续 review 处理。
7. 只对 `dispatch_agent` 立即用 `record-agent-session` 绑定返回的 `task_id`；`continue_agent` 恢复 action 指定的原会话，不重复绑定，返工复核不得换 reviewer。下一阶段只由 Graph 返回的新 action 决定。
8. 重复执行 graph 返回的 action，直到生成 `report.md` 和 `report.html`。恢复原主会话时沿用该会话原有 `run_id`；只有用户在新会话明确指定历史 Run 时才恢复历史 Run。

面向用户只报告分析阶段、范围和结果，不展示内部 CLI 或 task contract 细节。
