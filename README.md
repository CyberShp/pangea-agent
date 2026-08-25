# pangea-agent

`pangea-agent` 是部署在测试人员 Windows 电脑上的 C/C++ 测试分析 Agent。它把冻结源码、经审核的历史缺陷机理、结构化需求/设计资料和相关 Coverage 转成代码流程、资料/代码差异、六维 DFX 风险、测试用例和离线报告。

Python 只负责确定性工作：文件发现、C/C++ 结构解析、Coverage 匹配、状态、JSON 契约、聚合和报告。单元规划、源码理解、独立复核和资料提取由当前客户端派发 Agent 完成。Python 不调用模型 API。

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
→ 必要时只补齐受影响单元
→ 聚合 report.md 和 report.html
```

“最多 8 个”是并发上限，不是整个 Run 的单元总数。首轮 analysis 已经负责代码/设计理解、主干与异常流程、调用链、资料/代码差异、Coverage 缺口、缺陷机理、风险和用例；独立复核用于寻找遗漏，不再做逐字段比较审计。

主 Agent 只处理 CLI 返回的 action：

1. 派发 action 指定的 Agent 和 task 文件；
2. 用 `adapter bind` 记录真实客户端任务 ID；
3. Agent 写入 task 指定的 `result_path`；
4. 用 `adapter validate` 校验当前结果；
5. 校验通过后用 `adapter settle` 推进 graph。

结果文件只包含语义内容。`run_id`、`unit_id`、Agent 任务 ID、路径和状态由 Python 保存，Agent 不重复回填这些机械字段。Python 不生成空结果骨架。

## 输入与用例设计

- 历史缺陷资料先提取“事实 + 可迁移缺陷机理”，必须人工审核后才可用于 Run。
- 需求、设计和参考资料先结构化，分析时只把相关条目送入单元。
- Coverage 只处理与当前源码唯一匹配且 `count=0` 的函数。每项都尝试转成用例；无法触达时如实记录原因。
- 与当前范围无关的 Coverage 不进入分析结果和报告。
- 用例设计顺序是：Coverage 与代码流程为基础，需求/设计约束次之，历史缺陷机理和六维 DFX 风险补充。
- 黑盒优先；纯黑盒不可行时允许灰盒，但必须保留业务入口、外部观测和清理/恢复。

## 对外 JSON 接口

CLI 每次只向 stdout 输出一个 JSON envelope。主要能力分为：

- `assets`：导入、列表、详情、结构化提取、历史缺陷审核、归档；
- `runs`：创建、列表、详情、停止、打开 Markdown/HTML 报告；
- `system capabilities`：返回当前支持语言和接口版本；
- `adapter`：供客户端绑定、校验和提交 Agent action。

当前只宣布 `c_cpp` 支持。Lua 会在 C/C++ 流程稳定后单独实现，不预建跨语言抽象层。

## 报告

每个 Run 固定生成：

```text
pangea-data/runs/<run-id>/report.md
pangea-data/runs/<run-id>/report.html
```

HTML 是无外链单文件，并直接渲染主干、分支、异常传播和恢复流程图。报告只展示与当前范围相关并已经处理的输入。
