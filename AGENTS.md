# pangea-agent Agent Rules

本项目使用中文进行需求澄清、方案说明和开发沟通。代码符号、配置键、协议字段、文件路径和错误信息保持原文。

## 项目目标

`pangea-agent` 是面向测试分析的 Agent 框架骨架。核心工作是把源码、设计资料、覆盖率和已有用例转化为：

- 代码与流程理解。
- DFX / SFMEA 风险账本。
- 测试点。
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
- 路径参数使用引号包裹，避免空格、中文路径和反斜杠被错误解析。
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
- 质量门禁输出 `PASS`、`REWORK` 或 `UNRESOLVED`。
- 所有正式产物优先使用 `schemas/` 中的结构。

## 实现约定

- 新节点放在 `src/pangea_agent/graph/nodes/`。
- 新数据模型放在 `src/pangea_agent/models/`。
- 新索引能力放在 `src/pangea_agent/index/`。
- 新分析方法论放在 `src/pangea_agent/rubrics/builtin/`。
- CLI 能力放在 `src/pangea_agent/cli/`。

## Agent 客户端

- OpenCode 读取本文件和 `.opencode/agents/pangea-agent.md`。
- Claude Code 读取 `CLAUDE.md`。
- 两个客户端应遵循同一套 graph / schema / rubric 分层。
