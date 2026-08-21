# DSH Runtime Overlay

本文件只在 DSH 当前工作区为本仓库时生效。DSH 会自动加载根目录的
`AGENTS.local.md`。根 Agent 收到 PANGEA 模块分析、风险、流程、资料、Coverage、
用例或显式 Run 续跑请求时，第一步必须调用 `skill` 加载仓库内的
`pangea-agent` Skill；Skill 成功加载前不得执行其他工具。

被派发的子 Agent 收到 `agent-tasks/analysis/`、`agent-tasks/rework/` 或 review task
JSON 路径时，先加载 `pangea-agent` Skill；Skill 会识别为委派调用，读取
`.agents/pangea/dsh.md` 和对应 `.opencode/agents/*-worker.md`，然后只处理该 task，
不会进入根 Agent 的新建/恢复 Run 流程。

DSH 的 `bash` 工具按当前宿主 shell 执行；POSIX 宿主不得向该工具发送 PowerShell
cmdlet。Windows / PowerShell 兼容要求不改变 DSH 工具的真实 shell。

- 新根会话收到新的模块分析要求时，当前 Run 固定为空。创建新 Run 前不得调用
  `pangea_status`，不得列举或读取 `pangea-data/runs/`，不得读取或复用已有
  `pending-task-contract.json`。
- CLI 返回 `action=<JSON>` 后，主 Agent 只按 action 派发或续接对应子 Agent、
  记录 `subagent_id`，并在该回合通过提交检查后立即执行 `resume-run`。
  主 Agent 不根据 `phase` 或子 Agent 回复文本推测下一步，也不得创建、填写或修正
  worker / reviewer 的语义结果文件。
- 主 Agent 不读取 worker 角色文件，也不复述 task 字段、源码位置、分析步骤或结论。
  `dispatch_agent` 的 `prompt` 和 `continue_agent` 的续接消息都必须只包含 action 返回的
  `task_path`；续接必须使用 action 中的 `task_id`。
- 子 Agent 无法派发、续接或写入结果时，停止并如实报告；不得由主 Agent 代写结果
  绕过失败。
