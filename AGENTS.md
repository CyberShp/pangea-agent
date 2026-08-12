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
