# pangea-agent

`pangea-agent` 是部署在测试人员 Windows 电脑上的 C/C++ 与 Lua 测试分析 Agent。它把冻结源码、经审核的历史缺陷机理、结构化需求/设计资料和相关 Coverage 转成代码流程、资料/代码差异、六维 DFX 风险、测试用例和离线报告。

Python 只负责确定性工作：文件发现、语言识别、源码结构解析、Coverage 匹配、状态、JSON 契约、聚合和报告。单元规划、源码理解、独立复核和资料提取由当前客户端派发 Agent 完成。Python 不调用模型 API。

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
├── inbox/              # 导入的需求、设计、历史缺陷和参考资料
├── coverage/           # 导入的 Coverage XLSX
├── assets/             # 资产状态、提取任务和结构化结果
└── runs/               # Run 输入、Agent 任务、结果、进度和报告
```

项目不会对 `repositories/` 中的用户源码执行 `pull`、`reset`、`stash`、`checkout` 或格式化。已有用例不进入资产库；单次 Run 可附带少量用例示例，仅供表达和环境参考。

## 分析流程

```text
准备并冻结输入
→ Planning Agent 按功能模块/文件族规划单元
→ 最多 8 个 analysis Agent 并行完成首轮分析
→ 1 个看不到首轮结果的独立复核 Agent
→ 同一 Reviewer 对照首轮结果裁决 finding
→ 原 analysis worker 必要时只补齐受影响单元
→ 聚合 report.md 和 report.html
```

“最多 8 个”是并发上限，不是整个 Run 的单元总数。首轮 analysis 已经负责代码/设计理解、主干与异常流程、调用链、资料/代码差异、Coverage 提示、缺陷机理、风险和用例；独立复核寻找遗漏，comparison review 由同一 Reviewer 对照首轮结果裁决发现，不拆成逐字段审计任务。

主 Agent 只处理 CLI 返回的 action：

1. `dispatch_agent` 创建 action 指定的 Agent，`continue_agent` 恢复 action 自带的同一任务；
2. 用 `adapter bind` 记录真实客户端任务 ID；
3. Agent 写入 task 指定的 `result_path`；
4. 用 `adapter validate` 校验当前结果；
5. `status=invalid` 时按 `repair_action` 由同一 Agent 修正同一结果；
6. 校验通过后用 `adapter settle` 推进 graph。

结果文件只包含语义内容。Workflow 创建唯一 `result_path` 和对应骨架；Agent 在该文件中写入完整结果。`run_id`、`unit_id`、Agent 任务 ID、路径和状态由 Python 保存，Agent 不重复回填这些机械字段。

## 输入与用例设计

- 历史缺陷资料先提取“事实 + 可迁移缺陷机理”，必须人工审核后才可用于 Run。
- 需求、设计和参考资料先结构化，分析时只把相关条目送入单元。
- Coverage 只把当前 `source_scope` 中唯一匹配且 `count=0` 的函数作为补测提示。选择处理时保留真实 ID 和关联；不由 Python 强制生成测试用例。
- 与当前范围无关的 Coverage 不进入分析结果和报告。
- 用例设计顺序是：Coverage 与代码流程为基础，需求/设计约束次之，历史缺陷机理和六维 DFX 风险补充。
- Lua 沿用现有 Coverage XLSX 契约：导入数据中的文件路径、函数名或分支行号需要能唯一匹配 Lua inventory。当前版本未直接读取 LuaCov 等原生输出，使用时先转换为现有 XLSX 字段。
- 黑盒优先；纯黑盒不可行时允许灰盒，但必须保留业务入口、外部观测和清理/恢复。

## 分析方法论

PANGEA 从已经人工批准的历史缺陷条目准备方法论提炼 task；DSH 按 task 派发仓库内的
`methodology-worker`，再由 PANGEA 校验来源和候选结构。候选必须由用户明确启用，之后创建的 Run 才会在
`inputs/methodologies/` 中冻结独立副本。内置专项方法论和已启用的用户方法论都会进入 Run 的精简
`catalog.json`；Planning Agent 只读取其中的 ID、标题、适用条件和例外，按分析单元记录选择理由。只有选中的
方法论全文才加入该单元 analysis worker 和 reviewer 的
`rubric_paths`。旧 Run 始终使用自己的冻结副本。

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

- `assets`：导入、列表、详情、结构化提取、历史缺陷审核、归档；
- `runs`：创建、列表、详情、停止、打开 Markdown/HTML 报告；
- `system capabilities`：返回当前支持语言和接口版本；
- `adapter`：供客户端绑定、校验和提交 Agent action。

analysis/closure action 的 `methodologies` 字段列出当前单元实际冻结的方法论；`runs get` 在 Run
级别返回同结构清单。每项包含稳定 ID、标题、内容 SHA-256、通用/专项类型、选择依据、来源基线摘要和
专项来源目录路径。该字段只用于展示和追溯，不参与结果校验，也不替 Agent 判断风险或用例是否成立。

当前支持 `c_cpp` 与纯 `lua` 模块。系统根据选中模块中的源码自动判断语言；同一模块同时包含 Lua 与 C/C++ 时会明确停止，第一阶段不分析混合语言模块。

## 报告

每个 Run 固定生成：

```text
pangea-data/runs/<run-id>/report.md
pangea-data/runs/<run-id>/report.html
```

HTML 是无外链单文件，并直接渲染主干、分支、异常传播和恢复流程图。报告只展示与当前范围相关并已经处理的输入。
