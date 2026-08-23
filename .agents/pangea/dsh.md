# PANGEA DSH Adapter

本文件只属于当前 `pangea-agent` 仓库。只有 DSH 当前工作区位于本仓库时才使用；不得复制到 `~/.dsh/AGENTS.md`、全局 profile、其他项目或用户级 persona。

## 主 Agent

- 继续遵循本仓库 `AGENTS.md`、graph、schema 和 rubric。
- DSH 的 `bash` 工具执行当前宿主 shell。当前工具是 POSIX shell 时使用 POSIX 命令，
  不把 `Get-ChildItem`、`Select-Object` 等 PowerShell cmdlet 交给 `bash`。项目的
  Windows / PowerShell 兼容约定仍用于产品命令和文档，不覆盖 DSH 工具的真实 shell。
- 当前根会话只选择一次仓库虚拟环境解释器并在所有 PANGEA CLI 调用中复用：POSIX 使用
  `.venv/bin/python`，Windows PowerShell 使用 `& '.\.venv\Scripts\python.exe'`。选定路径不存在时
  停止并说明需要初始化；不再尝试系统 Python、其他虚拟环境或安装依赖。
- `module-analysis` 创建 Run 后，把返回的 `data_root` 与 `run_id` 一起绑定到当前根会话。后续每条 run-scoped CLI（`record-agent-session`、`resume-run`、`mark-reviewer-unavailable`）都必须原样传入 `--data-root <data_root>`，包括默认 `pangea-data`；不得依赖 CLI 默认值或执行失败后再探测。
- DSH 启动时由根目录 `AGENTS.local.md` 要求先加载仓库内 `pangea-agent` Skill；该 Skill 再要求读取本 adapter。Skill 未成功加载时停止，不要靠通用会话自行摸索 PANGEA 流程。
- DSH 新建根会话时当前 Run 固定为空。即使工作区中存在未完成的历史 Run，或 Companion / `pangea_status` 能读取到某个历史 Run，也不得因此执行 `resume-run`。
- 新根会话命中新分析意图后，在 `module-analysis` 返回新 `run_id` 前，不调用 `pangea_status`，不列举或读取 `pangea-data/runs/`，不读取或复用已有 pending contract。先删除固定临时路径 `pangea-data/.pangea/pending-task-contract.json`，再根据当前请求新建；POSIX 对这个文件单独使用 `rm -f`，PowerShell 使用 `Remove-Item -LiteralPath 'pangea-data/.pangea/pending-task-contract.json' -Force -ErrorAction SilentlyContinue`。Run 创建成功后再次用同一条单独删除命令删除它；命令中不能追加 `&& echo`、目录检查或第二个动作。
- 上述 pending 路径是仓库级固定字面值，绝不改成 `<data_root>/.pangea/pending-task-contract.json`；
  自定义 `data_root` 只写入 contract 字段和后续 CLI 参数。根 Agent 不预先创建或检查 `data_root`，也不
  把删除 pending、建目录或其他动作合并进同一条 bash 命令。
- pending contract 中的 `source_scope` 始终写成仓库根目录下使用 `/` 分隔的相对路径；即使宿主是 Windows，也不得把工具返回的反斜杠路径直接写入 JSON。
- 用户用自然语言要求分析业务源码、模块、测试风险、业务流程、Coverage 缺口或生成测试用例时，与显式 `module-analysis` 完全等价：当前根会话没有明确 `run_id` 就创建新 Run，不要求用户补写命令名。
- DSH 只有在当前根会话已经由本次分析获得明确 `run_id`，或用户明确指定历史 `run_id` / 历史会话时，才能恢复 Run。不得从 `runs/`、`progress.json`、`agent_sessions` 或 Companion 的“当前/最近 Run”反推恢复目标。
- Companion 和 `pangea_status` 只提供只读观察结果；它们返回或展示的 Run 不构成当前根会话的 Run 绑定。
- DSH 可能同时看到仓库内 `CLAUDE.md`；其中共享的 graph / schema / rubric 规则继续生效，但 DSH 的启动、subagent、续接和会话记录方式以本文件为准，不套用 Claude Code 的客户端传输方式。
- CLI 输出的每条 `action=<JSON>` 是客户端唯一的派发依据；`phase` 只用于展示和故障说明。不得根据 `phase`、子 Agent 回复文本或主 Agent 自定提示决定阶段。
- action 角色使用唯一映射：`analysis`、`rework` 加载 `.opencode/agents/analysis-worker.md`，`review` 加载 `.opencode/agents/review-worker.md`。不得沿用上一回合角色，也不得根据 phase、回复摘要或“返工/复核”等自然语言重新判断。
- 这里的“加载角色”由派发出的子 Agent 完成。根 Agent 禁止调用 `read` 打开
  `.opencode/agents/analysis-worker.md`、`.opencode/agents/review-worker.md`、action task 或任何
  `agent-results/` 语义文件；根 Agent 只把 action 的原始 `task_path` 交给子 Agent。
- `dispatch_agent` 必须显式使用 `run_in_background=true`，prompt 只包含 action 的 `task_path`；`continue_agent` 必须向 action 的 `task_id` 只发送 `task_path`。
- analysis 的 `source_checkpoint`、`risk_analysis`、`test_generation` 续接同一 `subagent_id`；review 的 `independent_review`、`comparison_review`、`rework_verification` 续接同一 reviewer。正式 `rework` 优先续接原 analysis worker；只有 action 明确 `dispatch_agent` 且 `replacement_allowed=true` 时才允许替代。
- 子 Agent 仍为 `running` 时不得发送其他消息。worker 的 `validate-worker-result PASS` 或 reviewer 的 `check-review-artifact PASS` 只完成当前 task 已绑定的会话；当前 action settled 后，主 Agent 按 `after_completion=resume_run` 只执行一次 `resume-run --run-id <run_id> --data-root <data_root>`。Graph 会再次验证完成状态和产物，并由下一条 action 决定是否续接及执行哪个阶段。
- `dispatch_agent` 的 action `task_id` 必须为 null，因为此时尚未创建会话。先完成派发，取得 DSH 返回的真实 UUID `subagent_id`，再把这个返回值作为 `task_id` 记录到 `progress.agent_sessions`；绝不把 action 中的 null、字符串 `"null"`、临时占位值或自造 ID 传给 `record-agent-session`。已有 `task_id` 时恢复原会话，不重复创建，也不重复记录。
- 派发返回 `subagent_id` 后，下一个工具调用必须是 `record-agent-session`，中间不执行其他动作。这个 ID 只用于会话绑定和后续续接，不通过其他执行通道查询。主 Agent没有记录 `completed` 的命令。
- analysis 首次记录时，用本会话选定的解释器执行 `-m pangea_agent.cli.main record-agent-session --run-id <run_id> --data-root <data_root> --role analysis --unit-id <unit_id> --task-id <subagent_id>`；review 使用同一命令但省略 `--unit-id` 并写 `--role review`。正式返工优先恢复原 analysis worker；只有原会话不可恢复且 rework task 允许替代时，替代 worker 才用 `--role rework --unit-id <unit_id>` 记录新 ID。不得为确认这些现有参数调用 `--help`。
- 返工复核必须恢复原 reviewer。缺少原 `task_id` 或恢复失败时，不派新 reviewer；用本会话选定解释器执行 `-m pangea_agent.cli.main mark-reviewer-unavailable --run-id <run_id> --data-root <data_root> --reviewer-id <same_reviewer_id> --reason "<真实原因>"`，再用同一解释器执行 `-m pangea_agent.cli.main resume-run --run-id <run_id> --data-root <data_root>` 形成 `UNRESOLVED`。
- 新派发记录会话后，根 Agent 立即结束当前回合；`send_message` 成功接受续接后也立即结束当前回合。
  不得监控或探测子 Agent、查看 task/result，也不得再补发消息。匹配的
  当前 action settled 后，根 Agent 只执行一次 action 的 `resume_run`。命令失败时如实停止；命令成功时，
  Graph 返回的 action 即使与上一条具有相同 role、stage、task_path 或 task_id，仍是新的唯一执行依据，
  必须按其 `dispatch_agent` / `continue_agent` 执行，不得通过比较新旧 action 自行决定停止。
  `continue_agent` 必须先对 action 的 `task_id` 发起真实续接，只有调用明确拒绝或找不到会话才算恢复失败。
- 统一插件 `dsh-pangea` 内置的唤醒策略在本工作区把 `subagent-report` 静默投递，避免它提前唤醒根 Agent；规则层仍把 report 只作信息展示，不解析其语义、不据此读取产物、不给子 Agent 发送修正建议，也不据此执行 `resume-run`。当前 action 对应的 `subagent-settled` 才负责唤醒根 Agent；根 Agent 不推断 PASS，直接且只执行一次 action 的 `after_completion=resume_run`。Graph 会重新校验绑定会话和产物，只有 Graph 能推进阶段。resume 命令失败时如实停止；成功返回的 action 一律按其内容执行，不因“看起来相同”而停止。一个 action 已执行过一次 resume 后，在返回 action 真正派发或续接前忽略重复的 report/settled 通知。
- 主 Agent 不得创建、填写或修正 worker / reviewer 的语义结果文件。只能把 action 的 `task_path` 交给对应子 Agent；派发、续接或写入失败时停止并报告，不得由主 Agent 代写。
- 不把 PANGEA 工作流规则安装到 DSH 全局配置；Companion 可以安装在 DSH profile，但其唤醒策略
  只在向上找到本文件时启用静默投递。工作区切换后，本文件及其静默策略都不再适用。

## 子 Agent 角色加载

DSH 子 Agent 每次收到 PANGEA task 路径后，先由 `pangea-agent` Skill 识别委派入口，
再加载对应的仓库内角色规则并读取 task；不得进入根 Agent 的新建/恢复 Run 流程：

- `agent-tasks/analysis/*.json` 或 `agent-tasks/rework/*.json`：读取 `.opencode/agents/analysis-worker.md`。
- `agent-tasks/review-independent.json`、`agent-tasks/review.json`、`agent-tasks/rework-review.json`：读取 `.opencode/agents/review-worker.md`。

子 Agent 只负责写入并验证当前 task 的产物；验证 `PASS` 会完成当前已绑定会话，然后把控制权交还主 Agent。子 Agent
不得执行 `module-analysis`、`record-agent-session`、`resume-run`、
`mark-reviewer-unavailable`，也不得派发或监控其他 Agent；这些 Run 生命周期动作只由主
Agent 根据 graph 返回的 action 执行。

这些文件在 DSH 中只作为 PANGEA 角色契约文本使用，不表示切换到 OpenCode 运行时。不得启动一个只知道 task 路径、没有读取对应 worker 规则的通用子 Agent。

## Review 提交门禁

reviewer 写完当前 task 结果后，在结束当前回合前执行：

使用当前根会话选定的解释器执行对应命令：

```text
POSIX: .venv/bin/python -m pangea_agent.cli.main check-review-artifact --task "<review task JSON>"
PowerShell: & '.\.venv\Scripts\python.exe' -m pangea_agent.cli.main check-review-artifact --task '<review task JSON>'
```

只有输出 `PASS` 才能完成当前已绑定 reviewer 会话。若失败，由当前 reviewer 在同一结果文件中修正后重试；主 Agent 不得代填、扁平化或删除字段来绕过契约。当前 action settled 后，主 Agent 只执行一次 action 声明的 `resume_run`，由 Graph 判断 reviewer 是否真的完成。

`independent_review` 的 finding 固定为：

- `unit_id: str`
- `check_id: str`
- `finding: str`
- `evidence: list[str]`，至少 1 条，且每个数组元素必须直接是字符串

`independent_review` 禁止写 `worker_disposition`。该字段只属于 `comparison_review` 的 `IndependentFinding`。
