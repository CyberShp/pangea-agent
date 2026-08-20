# PANGEA DSH Adapter

本文件只属于当前 `pangea-agent` 仓库。只有 DSH 当前工作区位于本仓库时才使用；不得复制到 `~/.dsh/AGENTS.md`、全局 profile、其他项目或用户级 persona。

## 主 Agent

- 继续遵循本仓库 `AGENTS.md`、graph、schema 和 rubric。
- DSH 的 `bash` 工具执行当前宿主 shell。当前工具是 POSIX shell 时使用 POSIX 命令，
  不把 `Get-ChildItem`、`Select-Object` 等 PowerShell cmdlet 交给 `bash`。项目的
  Windows / PowerShell 兼容约定仍用于产品命令和文档，不覆盖 DSH 工具的真实 shell。
- DSH 启动时由根目录 `AGENTS.local.md` 要求先加载仓库内 `pangea-agent` Skill；该 Skill 再要求读取本 adapter。Skill 未成功加载时停止，不要靠通用会话自行摸索 PANGEA 流程。
- DSH 新建根会话时当前 Run 固定为空。即使工作区中存在 `WAITING_*` 的历史 Run，或 Companion / `pangea_status` 能读取到某个历史 Run，也不得因此执行 `resume-run`。
- 新根会话命中新分析意图后，在 `module-analysis` 返回新 `run_id` 前，不调用 `pangea_status`，不列举或读取 `pangea-data/runs/`，不读取或复用已有 pending contract。先删除固定临时路径 `pangea-data/.pangea/pending-task-contract.json`，再根据当前请求新建；当前 POSIX DSH 可对这个文件单独使用 `rm -f`。Run 创建成功后再次删除它。
- 用户用自然语言要求分析业务源码、模块、测试风险、业务流程、Coverage 缺口或生成测试用例时，与显式 `module-analysis` 完全等价：当前根会话没有明确 `run_id` 就创建新 Run，不要求用户补写命令名。
- DSH 只有在当前根会话已经由本次分析获得明确 `run_id`，或用户明确指定历史 `run_id` / 历史会话时，才能恢复 Run。不得从 `runs/`、`progress.json`、`agent_sessions` 或 Companion 的“当前/最近 Run”反推恢复目标。
- Companion 和 `pangea_status` 只提供只读观察结果；它们返回或展示的 Run 不构成当前根会话的 Run 绑定。
- DSH 可能同时看到仓库内 `CLAUDE.md`；其中共享的 graph / schema / rubric 规则继续生效，但 DSH 的启动、subagent、续接和会话记录方式以本文件为准，不套用 Claude Code 的客户端传输方式。
- DSH 的子 Agent 使用可继续会话；analysis 的 checkpoint / risks / tests 必须续接同一 `subagent_id`，review 的 independent / comparison / rework verification 必须续接同一 reviewer。
- 首次派发必须显式使用 `run_in_background=true`；省略该参数或设为 `false` 会产生不可续接的一次性结果，不得用于 PANGEA worker / reviewer。
- analysis 子 Agent 仍为 `running` 时不得预先排队下一阶段。只有该回合结束并返回对应 `STAGE`，且结果文件中的阶段标记一致，主 Agent 才能向同一 `subagent_id` 发送下一阶段消息。
- `STAGE checkpoint` 和 `STAGE risks` 后只续接同一 analysis 子 Agent，不调用 `resume-run`。只有 tests 最终校验 PASS 后才让 graph 接收结果。
- 首次派发后立即把 `subagent_id` 作为 `task_id` 记录到 `progress.agent_sessions`。已有 `task_id` 时恢复原会话，不重复创建。
- 派发返回 `subagent_id` 后，下一个工具调用必须是 `record-agent-session`，中间不执行其他动作。`subagent_id` 不是 `job_id`，不得传给 `job_output`。
- analysis 首次记录使用 `.venv/bin/python -m pangea_agent.cli.main record-agent-session --run-id <run_id> --role analysis --unit-id <unit_id> --task-id <subagent_id> --status dispatched`；review 使用同一命令但省略 `--unit-id` 并写 `--role review`。不得为确认这些现有参数调用 `--help`。
- 等待子 Agent 使用 `list_agents`。目标仍为 `running` 时，不读取结果、不发送下一阶段；当前 POSIX DSH 用单独的 `bash` 调用等待 5 秒，再次调用 `list_agents`。只有状态变为 `ready` 或 `inactive` 后才读取阶段结果并决定续接。
- graph 进入 `WAITING_*` 后，主 Agent 不得创建、填写或修正 worker / reviewer 的语义结果文件。只能把 graph 生成的 task 路径交给对应子 Agent；派发、续接或写入失败时停止并报告，不得由主 Agent 代写。
- 不把 PANGEA 规则安装到 DSH 全局配置；工作区切换后，本文件不再适用。

## 子 Agent 角色加载

DSH 子 Agent 每次收到 PANGEA task 路径后，先由 `pangea-agent` Skill 识别委派入口，
再加载对应的仓库内角色规则并读取 task；不得进入根 Agent 的新建/恢复 Run 流程：

- `agent-tasks/analysis/*.json` 或 `agent-tasks/rework/*.json`：读取 `.opencode/agents/analysis-worker.md`。
- `agent-tasks/review-independent.json`、`agent-tasks/review.json`、`agent-tasks/rework-review.json`：读取 `.opencode/agents/review-worker.md`。

这些文件在 DSH 中只作为 PANGEA 角色契约文本使用，不表示切换到 OpenCode 运行时。不得启动一个只知道 task 路径、没有读取对应 worker 规则的通用子 Agent。

## Review 提交门禁

reviewer 写完当前阶段结果后，在返回阶段完成标记前执行：

```powershell
python -m pangea_agent.cli.main check-review-artifact --task "<review task JSON>"
```

只有输出 `PASS` 才能让主 Agent 执行 `resume-run`。若失败，由当前 reviewer 在同一结果文件中修正后重试；主 Agent 不得代填、扁平化或删除字段来绕过契约。

`independent_review` 的 finding 固定为：

- `unit_id: str`
- `check_id: str`
- `finding: str`
- `evidence: list[str]`，至少 1 条，且每个数组元素必须直接是字符串

`independent_review` 禁止写 `worker_disposition`。该字段只属于 `comparison_review` 的 `IndependentFinding`。
