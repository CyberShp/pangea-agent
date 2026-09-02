# pangea-agent

`pangea-agent` 是部署在测试人员 Windows 电脑上的 Codetalks Skill 运行时与本地资料管理组件。源码、需求/设计资料、Coverage 和历史问题都由 `codetalks-skill 1.2.0` 直接消费并形成 Markdown 活文档与正式输出。

Python 只负责仓库/资料登记、创建 Skill Run、冻结 Skill 包和只读解释 `run_guard.py` 状态。Python 不规划分析单元、不编排 Agent、不校验语义结果，也不生成报告。

每个新 Run 都冻结一份完整 `codetalks-skill 1.2.0`，并在 Run 内冻结所选 Asset Management 2.0 资产和启用的方法论。DSH 分析会话读取该 Skill 后，使用它自己的 Step 01–09、`run_guard.py`、Producer/Judge 分工和正式输出契约走完整流程；语言 Profile 会根据已验证源码范围自动选择 C/C++、Lua 或混合模式。`内部索引/运行状态.json` 是唯一生命周期真相。

## 初始化

支持 Windows x86-64 和 Python 3.10～3.12：

```powershell
py -3.12 -m venv ".venv"
```

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -e .
```

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main init-data
```

安装沿用电脑已有的内部 pip 源。只有用户明确同意后，才使用仓库提供的 Windows 离线 wheel。

## 本地数据

```text
pangea-data/
├── repositories/       # 用户源码仓，可为 Git 或普通目录
├── inbox/              # 导入的需求、设计、历史缺陷、参考资料和用例示例
├── coverage/           # 导入的 Coverage XLSX
├── assets/             # 资产状态、提取任务和结构化结果
└── runs/               # Run 输入、Skill 状态、Markdown 活文档和正式报告
```

项目不会对 `repositories/` 中的用户源码执行 `pull`、`reset`、`stash`、`checkout` 或格式化。用例示例作为资产导入，只能在 Step 07 参考格式和粒度；新建 Run 不接受 focus、手写结构化资产 ID 或示例文件路径。

## 分析流程

```text
创建 Skill Run 并冻结 Skill、资产和方法论
→ 当前分析会话执行 Step 01–07
→ 独立 Judge 执行 Step 08
→ 当前分析会话根据审查结果执行 Step 09
→ run_guard validate / handoff / finalize
→ 正式输出/完整分析报告.md
```

旧 Graph、Planning/Analysis/Review/Closure action、schema、settle 和 Python Reporting 已删除。分析过程不再写 `progress.json`、`final-state.json`、`agent-results/`、`report.md` 或 `report.html`。

## 输入与用例设计

- 历史缺陷资料先提取“事实 + 可迁移缺陷机理”，必须人工审核后才可用于 Run。
- 需求、设计和参考资料在导入时确定性规范化，Run 只读取冻结副本。
- Coverage 只把当前 `source_scope` 中唯一匹配且 `count=0` 的函数作为补测提示。选择处理时保留真实 ID 和关联；不由 Python 强制生成测试用例。
- 与当前范围无关的 Coverage 不进入分析结果和报告。
- 用例设计顺序是：Coverage 与代码流程为基础，需求/设计约束次之，历史缺陷机理和六维 DFX 风险补充。
- Lua 沿用现有 Coverage XLSX 契约：导入数据中的文件路径、函数名或分支行号需要能唯一匹配 Lua inventory。当前版本未直接读取 LuaCov 等原生输出，使用时先转换为现有 XLSX 字段。
- 黑盒优先；纯黑盒不可行时允许灰盒，但必须保留业务入口、外部观测和清理/恢复。

## 分析方法论

PANGEA 从已经人工批准的历史缺陷条目准备方法论提炼 task；DSH 按 task 派发仓库内的
`methodology-worker`，再由 PANGEA 校验来源和候选结构。候选必须由用户明确启用，之后创建的 Run 才会在
`inputs/methodologies/` 中冻结独立副本。内置专项方法论和已启用的用户方法论都会进入 Run 的精简
`catalog.json`；Skill 执行会读取其中的 ID、标题、适用条件和例外，并在 Markdown 运行计划中记录选择理由。旧 Run 始终使用自己的冻结副本。

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main methodologies derive --data-root "pangea-data" --asset-id "asset-260830-001"
```

DSH 使用返回的 `task_path` 派发 `.agents/pangea/methodology-worker.md`。Agent 完成后提交 task：

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main methodologies complete-derivation --task "pangea-data\methodologies\tasks\<task-id>\task.json"
```

提炼 task 和完成回执保存在 `pangea-data/methodologies/tasks/`。客户端重启后通过以下接口查询
`pending`、`ready` 或 `completed`，复用原 task 或提交已经写好的结果：

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main methodologies derivations list --data-root "pangea-data"
```

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main methodologies import --data-root "pangea-data" --input "methodology-candidates.json"
```

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main methodologies list --data-root "pangea-data" --status candidate
```

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main methodologies enable --data-root "pangea-data" --id "iscsi-session-recovery"
```

再次导入同一 ID 且内容未变化时保留状态；内容变化后回到 `candidate`，等待重新确认。方法论只提供检查方向，
不能直接充当当前风险或测试预期的证据。

## 对外 JSON 接口

CLI 每次只向 stdout 输出一个 JSON envelope。主要能力分为：

- `assets`：导入、列表、详情、原文提取、历史缺陷审核、归档；
- `runs`：创建、列表、详情、停止、打开正式 Markdown 报告；
- `system capabilities`：返回当前支持语言和接口版本；

当前支持 `c_cpp` 与纯 `lua` 模块。系统根据选中模块中的源码自动判断语言；同一模块同时包含 Lua 与 C/C++ 时会明确停止，第一阶段不分析混合语言模块。

## 报告

每个完整 Run 固定生成：

```text
pangea-data/runs/<run-id>/正式输出/完整分析报告.md
```

HTML 是无外链单文件，并直接渲染主干、分支、异常传播和恢复流程图。报告只展示与当前范围相关并已经处理的输入。
