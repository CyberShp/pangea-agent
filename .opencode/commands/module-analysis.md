---
agent: pangea-agent
description: 按用户当前自然语言要求执行模块分析
---
# module-analysis

这是用户在 OpenCode 中启动 PANGEA 模块分析的主要入口。用户不需要准备、查看或维护 task contract，也不需要执行任何 CLI 命令。

收到用户的模块分析要求后：

1. 先确定目标源码仓和模块核心实现范围。`source_scope` 只填写该模块明确的核心源码文件或最小实现目录；不得使用仓库根目录、明显过大的父目录，也不得因为目标关键词在其他源码中出现就扩大 `source_scope`。
2. 如果用户只给模块名，先从仓库中定位该模块的入口文件、接口文件和对应实现文件，再形成最小 `source_scope`。直接调用者、配置、规格和已有测试属于上下文，不作为核心源码范围。
3. 新 OpenCode 主会话首次执行模块分析时，由主 Agent 在 PANGEA 内部生成任务契约并使用 `module-analysis` 创建新 Run；不要要求用户提供 contract 文件，不要在项目根目录或 `pangea-data/` 一级目录创建 task contract，也不要扫描历史 Run 决定恢复目标。
4. `module-analysis` 或 `resume-run` 返回的每条 `action=<JSON>` 是唯一的派发依据。按 action 执行 `dispatch_agent` 或 `continue_agent`，消息只包含 `task_path`，不根据 `phase` 或 Agent 回复文本推测下一步。
5. 当前子 Agent 的提交检查 `PASS` 会完成 action 绑定的会话；该子 Agent 回合正常返回后，主 Agent 按 `after_completion=resume_run` 只恢复同一 `run_id` 和 `data_root`，不得自行记录完成、轮询 Agent 或读取结果。下一阶段只由 Graph 返回的新 action 决定，直到生成 `report.md` 和 `report.html`。
6. OpenCode 恢复原主会话时沿用该会话已经持有的 `run_id`；只有用户在新会话明确指定历史 Run 时才恢复历史 Run。不得读取或执行 `.agents/pangea/dsh.md`，DSH 会话行为不属于本 command。

面向用户只报告分析阶段、范围和结果，不展示内部 CLI 或 task contract 细节。
