# pangea-agent

`pangea-agent` 是面向测试分析的 Agent 项目骨架，采用 LangGraph 风格的工作流组织方式。项目目标是把源码、设计资料、覆盖率和已有用例转化为可追溯的风险账本、测试点、测试用例和测试报告。

## 定位

`pangea-agent` 面向测试工程场景，重点支持：

- C/C++ 源码理解与测试视角转译。
- 模块级专项分析。
- DFX 与 SFMEA 风险识别。
- 风险到测试点、测试用例的结构化输出。
- OpenCode、Claude Code 等 Agent 客户端协作开发。

## 项目结构

```text
pangea-agent/
├── src/pangea_agent/          # 框架代码
├── schemas/                   # JSON Schema 数据契约
├── examples/                  # 示例 contract 与输出样例
├── AGENTS.md                  # OpenCode / 通用 Agent 项目规则
├── CLAUDE.md                  # Claude Code 项目规则
└── pangea-data/               # 本地用户数据，由命令创建
```

本地数据目录由命令创建：

```powershell
pangea init-data
```

数据目录结构：

```text
pangea-data/
├── repositories/              # 用户待分析源码，可 Git，也可普通源码目录
├── inbox/                     # 需求、设计、历史缺陷、测试报告
├── coverage/                  # 覆盖率资料
├── testcases/                 # 已有测试用例
└── runs/                      # 每次分析的索引、证据、风险、用例和报告
```

## Windows / PowerShell

项目命令按 Windows PowerShell 友好方式组织：

```powershell
pip install -e .
pangea init-data
pangea module-analysis --contract "examples/task_contract.module-analysis.example.json"
```

也可以使用 Python 模块入口，避免 PATH 尚未刷新时找不到 `pangea`：

```powershell
python -m pangea_agent.cli.main init-data
python -m pangea_agent.cli.main module-analysis --contract "examples/task_contract.module-analysis.example.json"
```

开发和 Agent 执行约定：

- 一次执行一个命令，不把正式步骤串联成一条 shell 命令。
- 路径包含空格或中文时使用引号。
- 用户源码放在 `pangea-data/repositories/`，项目不会自动对这些源码执行 `git pull`、`reset`、`stash` 或 `checkout`。
- 项目文件编辑由 Agent 客户端的 read/write/edit 能力完成，命令行主要用于运行 CLI。

## 工作流

```text
load_contract
→ resolve_repositories
→ locate_module
→ index_materials
→ build_inventory
→ make_analysis_units
→ analyze_unit
→ assemble_risks
→ generate_test_points
→ generate_test_cases
→ quality_gate
→ finalize_report
```

其中：

- `graph/` 定义流程。
- `schemas/` 定义数据结构。
- `rubrics/` 定义分析方法论。
- `index/` 负责把源码和资料转化为可检索证据。
- `inventory/` 负责提取函数、分支、资源和状态线索。

## 快速开始

```powershell
pip install -e .
pangea init-data
pangea module-analysis --contract "examples/task_contract.module-analysis.example.json"
```

## Agent 客户端

### OpenCode

从项目根目录启动：

```powershell
opencode .
```

OpenCode 会读取 `AGENTS.md`，也可以使用 `.opencode/agents/pangea-agent.md` 中的项目级 Agent 说明。

### Claude Code

从项目根目录启动 Claude Code。Claude Code 会读取 `CLAUDE.md` 作为项目规则。

## 开发约定

- 项目代码、schema、rubric 和示例进入 Git。
- 用户源码、输入资料、索引和 Run 结果保存在 `pangea-data/`。
- `tests/` 作为本地开发目录，当前不提交到 Git。
- 第一版保留清晰骨架和扩展接口，后续逐步补充节点实现。
