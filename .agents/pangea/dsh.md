# PANGEA DSH Adapter

本文件只属于当前 `pangea-agent` 仓库。只有 DSH 当前工作区位于本仓库时才使用；不得复制到 `~/.dsh/AGENTS.md`、全局 profile、其他项目或用户级 persona。

## 主 Agent

- 继续遵循本仓库 `AGENTS.md`、graph、schema 和 rubric。
- DSH 的子 Agent 使用可继续会话；analysis 的 checkpoint / risks / tests 必须续接同一 `subagent_id`，review 的 independent / comparison / rework verification 必须续接同一 reviewer。
- 首次派发后立即把 `subagent_id` 作为 `task_id` 记录到 `progress.agent_sessions`。已有 `task_id` 时恢复原会话，不重复创建。
- 不把 PANGEA 规则安装到 DSH 全局配置；工作区切换后，本文件不再适用。

## 子 Agent 角色加载

DSH 子 Agent 每次收到 PANGEA task 路径后，必须先加载对应的仓库内角色规则，再读取 task：

- `agent-tasks/analysis/*.json` 或 `agent-tasks/rework/*.json`：读取 `.opencode/agents/analysis-worker.md`。
- `agent-tasks/review-independent.json`、`agent-tasks/review.json`、`agent-tasks/rework-review.json`：读取 `.opencode/agents/review-worker.md`。

这些文件在 DSH 中只作为 PANGEA 角色契约文本使用，不表示切换到 OpenCode 运行时。不得启动一个只知道 task 路径、没有读取对应 worker 规则的通用子 Agent。

## Review 提交门禁

reviewer 写完当前阶段结果后，在返回阶段完成标记前执行：

```powershell
python -m pangea_agent.cli.main check-review-artifact --task "<review task JSON>"
```

只有输出 `PASS` 才能让主 Agent 执行 `resume-run`。若失败，由当前 reviewer 在同一结果文件中修正后重试；主 Agent不得代填、扁平化或删除字段来绕过契约。

`independent_review` 的 finding 固定为：

- `unit_id: str`
- `check_id: str`
- `finding: str`
- `evidence: list[str]`，至少 1 条，且每个数组元素必须直接是字符串

`independent_review` 禁止写 `worker_disposition`。该字段只属于 `comparison_review` 的 `IndependentFinding`。
