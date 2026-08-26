# pangea-agent Agent Rules

本项目使用中文进行需求澄清、方案说明和开发沟通。代码符号、配置键、协议字段、文件路径和错误信息保持原文。

## 判断与沟通原则

- 不一味附和用户。处理任何结论、需求或方案前，先检查其中是否存在错误前提、逻辑跳跃和会改变结论的信息缺失。
- 明确区分已经验证的事实、基于证据的推测、对未来的预测和主观观点，不把其中一类表述成另一类。
- 不同意用户判断时直接说明，并给出依据、风险和可替代的解释或方案；不得为了迎合而隐去反证。
- 主动指出用户可能忽略的变量、实施成本、机会成本、依赖条件和认知偏差。
- 以上原则不代替需求确认。存在会改变实现方向的关键信息缺口时，先澄清并等待确认。

## 项目目标

`pangea-agent` 是面向测试分析的项目级 Agent。核心工作是把源码、设计资料、覆盖率和已有用例转化为：

- 代码与流程理解。
- 六维 DFX 风险账本。
- 可执行测试用例。
- 测试报告。

## 开发边界

- `src/pangea_agent/graph/` 是流程入口。
- `schemas/` 是数据契约入口。
- `src/pangea_agent/rubrics/` 是分析方法论入口。
- `pangea-data/` 是本地用户数据目录，不作为项目源码提交内容。
- `tests/` 是本地开发目录，当前不提交到 Git。

## Windows / PowerShell 规则

- 默认按 Windows PowerShell 可执行方式组织命令，不使用 POSIX-only 写法。
- 不使用 `cd /d ... && ...`、`source .venv/bin/activate`、`export VAR=...`、`rm -rf`、`touch` 等 bash 风格命令。
- 不把多个正式步骤串成一条命令；每个命令单独执行，上一条成功后再执行下一条。
- 优先使用 Python 模块入口：`python -m pangea_agent.cli.main ...` 或安装后的 `pangea ...`。
- 文件和目录路径按字面值处理。路径中的 `\n`、`\t`、`\r` 等字符组合不得解释为换行、Tab 或其他转义字符。
- filesystem、glob、read、git 等工具返回的路径必须原样复用，不得手工重写、拆分、转义或规范化。
- PowerShell 操作已有路径时优先使用单引号和 `-LiteralPath`，例如 `Get-ChildItem -LiteralPath 'C:\code\nof\tcp'`。
- 某个文件工具因特殊路径访问失败时，可直接换用 read、Python `pathlib` 或 PowerShell `-LiteralPath`；不要仅根据该工具的路径错误判断目录不存在。
- 读写项目文件优先使用 Agent 客户端的 read/write/edit 能力；需要执行命令时只执行单一、明确、可复现的命令。
- 不自动对 `pangea-data/repositories/` 下的用户源码执行 `git pull`、`reset`、`stash`、`checkout` 或格式化。

## 源码分析原则

- 用户源码从 `pangea-data/repositories/<repo_id>/` 引用。
- 分析结果引用源码时使用 `repo_id:path:line` 形式。
- Git 信息只作为版本描述；普通源码目录同样可以分析。
- 测试用例优先用黑盒语言表达，函数、变量和行号只作为证据。

## 运行入口与会话 Run

- 用户不需要显式说 `module-analysis`。只要用户要求对业务源码、模块或目录做测试分析、风险分析、业务流程分析、Coverage 缺口分析或生成测试用例，就按一次新的 PANGEA 模块分析处理；`/module-analysis` 只是显式快捷入口。
- **自然语言分析目标是唯一必需的用户输入。** 用户没有显式提供 `data_root`、`repository`、`target`、`source_scope`、资料路径或 Coverage 路径时，客户端必须按项目目录约定自动发现，不得把内部 contract 字段作为参数表要求用户填写。
- 自动发现固定规则：`data_root` 缺省为 `pangea-data`；`repository` 从 `pangea-data/repositories/` 一级目录中根据目标自动选择；`target` 直接来自用户自然语言；`source_scope` 由客户端在选中仓库中定位入口、接口和核心实现后形成最小范围；`pangea-data/inbox/` 和 `pangea-data/coverage/` 由现有准备/索引流程自行关联。
- 只有自动发现存在真实且无法自动消歧的多个同等可信候选时才向用户提问，而且只询问造成歧义的事实。例如两个仓库都存在独立完整的同名模块实现时只问选择哪个仓库，不得退化成要求用户提供全部内部参数。
- 新根 Agent 会话默认没有当前 Run。若当前会话尚未通过本次分析创建并持有明确 `run_id`，且用户没有明确指定历史 `run_id`，创建新 Run 前不得调用 `pangea_status`，不得列举或读取 `pangea-data/runs/`、历史 `progress.json` / `agent_sessions`、报告或 Companion 状态。
- 新分析意图与显式 `/module-analysis` 执行同一入口：自动确定目标仓和最小 `source_scope`；`source_scope` 路径相对所选仓库根目录，不包含 `repo_id` 前缀，并在所有宿主上统一使用 `/` 分隔。为当前根会话新建唯一临时文件 `pangea-data/.pangea/pending-task-contract-<uuid>.json`，不得读取或复用其他 pending contract；随后执行 `python -m pangea_agent.cli.main module-analysis --contract <pending-contract>` 创建新 Run。新 Run 的 `run_id` 由 PANGEA 生成；CLI 在进入 Graph 前自动删除本会话的 pending contract，根 Agent 不再执行删除命令。
- 自动确定 repository / source_scope 时，允许并需要读取、搜索 `pangea-data/repositories/` 下的用户业务源码；“首次运行前不要研究 PANGEA 自身实现”的限制不禁止这一步业务源码定位。
- 只有两种情况允许执行 `resume-run`：当前会话已经由本次分析获得明确 `run_id`；或用户在当前请求中明确指定要恢复的历史 Run / 历史会话。新会话中的“继续之前的”如果没有明确 Run，不得自行扫描历史 Run 猜测。
- 查看历史 Run、打开报告、浏览 Companion 看板或调用只读状态工具不会把该 Run 绑定为当前会话 Run，也不会授权恢复。
- 修改 `pangea-agent` 自身代码、graph、schema、rubric、Agent 规则或 DSH/OpenCode 适配属于产品开发，不启动 PANGEA 分析 Run。

## 输出要求

- 风险必须包含复现条件、系统结果、外部观测和排除条件。
- 测试用例必须包含前置条件、步骤、预期结果、观测方式和清理/恢复。
- 风险驱动始终是测试用例基础；相关需求/设计资料必须闭环到测试用例。Coverage 文件本身是可选输入；一旦当前单元产生唯一匹配的函数级 `count=0` gap，每个 gap 都必须在 `test_generation` 通过 `coverage_decisions` 闭环：复用已有用例、生成 Coverage-only 用例，或明确 `unreachable_from_supported_entry`。闭环到用例时保持真实 Coverage ID 与 TestCase 双向关联。
- 质量门禁输出 `PASS`、`REWORK` 或 `UNRESOLVED`。
- 所有正式产物优先使用 `schemas/` 中的结构。

## 实现约定

- 新节点放在 `src/pangea_agent/graph/nodes/`。
- 新数据模型放在 `src/pangea_agent/models/`。
- 新索引能力放在 `src/pangea_agent/index/`。
- 新分析方法论放在 `src/pangea_agent/rubrics/builtin/`。
- CLI 能力放在 `src/pangea_agent/cli/`。

## Agent 客户端

- OpenCode 普通会话先遵循本文件的“运行入口与会话 Run”；选中 `pangea-agent` 或显式 `/module-analysis` 时继续使用 `.opencode/agents/pangea-agent.md` 和对应 command 细则，但不要求用户必须输入命令名。
- Claude Code 读取 `CLAUDE.md`。
- DSH 只在当前工作区属于本 `pangea-agent` 仓库时读取本文件，并额外读取仓库内 `.agents/pangea/dsh.md`。不得把 PANGEA 规则复制到 `~/.dsh/AGENTS.md`、全局 profile 或其他工作区。
- DSH 派发 PANGEA 子 Agent 时，子 Agent 先按 `.agents/pangea/dsh.md` 选择并读取仓库内对应 worker 规则；不得只把 task JSON 路径交给一个未加载 PANGEA worker 规则的通用子 Agent。
- 三个客户端应遵循同一套 graph / schema / rubric 分层。
- Python 不调用模型 API。当前主 Agent 读取 `agent-tasks/` 文件，最多并发派发
  8 个互不重叠的 `analysis-worker`；worker 不得再派发子 Agent。
- CLI 每次创建或恢复 Run 后返回 `action=<JSON>`。主 Agent 只按 action 的 `action`、`role`、`stage`、`task_path`、`task_id`、`replacement_allowed` 和 `after_completion` 派发或续接对应 Agent；不得用 `phase`、Agent 回复文本或自定阶段提示代替 action。worker 的 `validate-worker-result` 或 reviewer 的 `check-review-artifact` 只有在当前 task 校验 `PASS` 后才记录其已绑定会话完成；`after_completion=resume_run` 表示根 Agent 收到该 Agent 的回合完成报告后只执行 `resume-run`，不得自行记录完成、轮询 Agent、读取产物或决定下一阶段。Graph 会再次验证完成状态和产物，并生成唯一的下一条 action。新 Run 返回的 `data_root` 与 `run_id` 一起绑定，所有 run-scoped CLI 都必须显式传 `--data-root <data_root>`。主 Agent 不得创建、填写或修正 `agent-results/analysis/`、`agent-results/rework/` 或 review 语义结果。worker 无法完成时如实停止，不得由主 Agent 代写结果。
- analysis 结果齐备后，只启动 1 个 `review-worker`。初审和返工验证属于同一轮
  review lifecycle，返工最多一次，且返工验证必须由原 reviewer 完成。
- 初审固定为同一 reviewer 的两个 checkpoint：`independent_review` task 不提供 worker result；
  graph 接受独立结论后才生成 `comparison_review` task。两个阶段各完成一次，不按检查项拆分调用。
- analysis-worker 每回合只执行 worker task 当前 `stage`，将 `completed_stage` 写成相同值，并在结束前执行 `validate-worker-result --task "<worker task JSON>"` 到 `PASS`。review-worker 每次写完 `independent_review`、`comparison_review` 或 `rework_verification` 结果后，必须执行 `check-review-artifact --task "<review task JSON>"` 并得到 `PASS`。失败由当前 Agent 修正同一结果文件，主 Agent 不得代填或绕过契约。
- 新主 Agent 会话命中新的模块分析意图时创建新 Run。当前会话已经持有本次运行的明确 `run_id` 后，每个 action 对应的 Agent 回合完成并通过提交检查后，主 Agent立即执行 action 声明的 `resume_run`；不等待或解析 Agent 回复中的文本标记。不得要求用户为了触发流程显式说 `module-analysis`，不得扫描历史 Run 自动替用户选择恢复目标，也不得用占位风险冒充语义分析结果。

## Private House Code Policy

<!-- PRIVATE_HOUSE_CODE_PROJECT_POLICY_V1 -->

- 当 `gpt-5.6-sol` 或 `gpt-5.6-terra` 在本仓库规划、编写、修改、调试、
  测试、重构、审查或维护代码时，必须先完整读取
  `.agents/skills/private-house-code/SKILL.md`，并在该代码任务中遵循它。
- 仅当边界确实存在歧义时，才按 Skill 指引读取随附的校准示例；不要把上游
  评测记录或全局配套说明当作项目任务指引。
- 用户明确要求、本 `AGENTS.md`、真实安全与数据完整性边界、已发布兼容性和
  必需测试与 Skill 冲突时，前者优先。
- 本项目现有的 schema 契约、源码证据可追溯性、`UNRESOLVED` 真实性、
  `pangea-data/repositories/` 用户源码保护和 Windows / PowerShell 兼容要求
  都有现实用途；不得仅为减少代码、状态、检查或抽象而弱化它们。
- 普通交流和不涉及代码的研究不适用本策略。
