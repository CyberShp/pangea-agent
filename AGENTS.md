# pangea-agent Agent Rules

本项目使用中文进行需求澄清、方案说明和开发沟通。代码符号、配置键、协议字段、文件路径和错误信息保持原文。

## 项目目标

`pangea-agent` 是面向测试分析的项目级 Agent。核心工作是把源码、结构化资料和覆盖率转化为：

- 代码与流程理解。
- 六维 DFX 风险账本。
- 可执行测试用例。
- 测试报告。

已有用例不作为长期管理资产。用户可以在单次 Run 中提供少量用例示例，它们只用于表达和环境参考。

## 独立判断

- 先检查用户说法是否包含错误前提、逻辑跳跃或缺失信息，不以附和代替判断。
- 区分已验证事实、基于证据的推测和主观取舍。
- 不同意时直接说明依据、风险和替代解释，并主动指出容易忽略的变量、成本和偏差。

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

## 输出要求

- 风险必须包含复现条件、系统结果、外部观测和排除条件。
- 测试用例必须包含前置条件、步骤、预期结果、观测方式和清理/恢复。
- 最终质量门禁输出 `PASS` 或 `UNRESOLVED`；不能把未完成工作描述为完成。
- 所有正式产物优先使用 `schemas/` 中的结构。

## 实现约定

- 新节点放在 `src/pangea_agent/graph/nodes/`。
- 新数据模型放在 `src/pangea_agent/models/`。
- 新的确定性输入处理放在 `src/pangea_agent/documents/` 或 `inventory/`。
- 新分析方法论放在 `src/pangea_agent/rubrics/builtin/`。
- CLI 能力放在 `src/pangea_agent/cli/`。

## Agent 客户端

- 各客户端只读取自己的入口和 Agent 规则。客户端目录中的内容不得引用或调用另一个客户端的规则文件。
- 共享范围只包括 graph、schema、rubric 和 CLI 契约；客户端专有的命令、会话轨迹和 Agent 调用方式不得写入共享方法论。
- Python 不调用模型 API，也不做语义拆分。一个 Planning Agent 按功能模块或文件族规划单元。
- 首轮 analysis 最多同时派发 8 个互不重叠单元，总单元数不受 8 限制；worker 不得再派发子 Agent。
- analysis 结果齐备后只启动 1 个独立 Reviewer。`independent_review` task 不包含 analysis result；随后由同一 Reviewer 续接 `comparison_review`，对照首轮结果裁决和补充 finding。
- comparison review 保留的 finding 只为受影响单元生成一次 `targeted_closure`；该 action 必须续接对应单元首轮 analysis worker 的真实 `task_id`，在 Workflow 预先复制的 closure 结果中定向补齐，不能创建替代 worker，也不能修改原始 analysis 结果。
- 主 Agent 只执行 CLI 返回的 action，并通过 adapter 完成 `bind`、`validate`、`settle`。不得自行填写或修正语义结果。
- Workflow 创建正式 task 时同时创建唯一 `result_path` 和对应结果骨架。客户端与 Adapter 不得另建、改名或从其他结果文件兜底读取。
- `adapter validate` 返回 `status=invalid` 时，主 Agent 按 `repair_action` 续接同一个 `task_id`，把错误交回原 worker 修正同一 `result_path` 后重试。普通结果校验失败不推进 Action，也不停止 Run；Run/action/task、冻结输入或约定 task_id 损坏才属于流程错误。
- Python 只校验 schema、真实 ID、路径归属、引用关系和流程结构等确定性契约，不根据行号重叠、finding category 或测试用例内容推翻 Agent/Reviewer 的语义结论。Coverage 只提供当前 `source_scope` 中唯一匹配的零覆盖提示，不强制生成测试用例。

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
