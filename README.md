# pangea-agent

`pangea-agent` 是部署在测试人员 Windows 电脑上的项目级测试分析 Agent。用户在
OpenCode 或 Claude Code 中用自然语言提出模块分析任务，Agent 结合当前本地 C/C++
源码、长期资料和函数覆盖率，输出可追溯风险、测试用例以及离线报告。

Python 只负责确定性解析、索引、任务拆分、状态、校验和报告，不调用模型 API。
语义分析由当前客户端最多并发派发 4 个 `analysis-worker` 完成，再由 1 个
`review-worker` 做独立复核。

## 初始化

支持 Windows x86-64 和 Python 3.10～3.12。用户告诉 Agent“初始化 PANGEA”后，
Agent 先明确回复“正在初始化 PANGEA”，检查 `py -0p`、现有 `.venv` 和 pip；若没有
兼容版本则停止并说明，若需要新建或重建环境则先列出 Python 版本、目标路径和安装
动作，得到确认后一次执行一个命令。下面以 Python 3.12 为例，Agent 应自动选择电脑
已有的 3.10、3.11 或 3.12：

```powershell
py -3.12 -m venv ".venv"
```

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -e .
```

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main init-data
```

`pip` 直接使用电脑已配置的内部源，项目不指定或尝试公网源。内部源安装失败时，
Agent 应先说明失败，再询问是否使用仓库随版本提供的 Windows x86-64 离线 wheel；
不得静默切换安装来源。

用户确认使用离线 wheel 后执行：

```powershell
& ".\.venv\Scripts\python.exe" -m pip install --no-index --find-links "vendor\wheels\win_amd64" "setuptools>=68"
```

```powershell
& ".\.venv\Scripts\python.exe" -m pip install --no-index --find-links "vendor\wheels\win_amd64" --no-build-isolation -e .
```

维护者在可访问内部 Python 源的环境执行
`python tools/download_offline_wheels.py`，生成并提交
`vendor/wheels/win_amd64/`。脚本只下载 Python 3.10、3.11、3.12 的 Windows
x86-64 wheel，不下载源码包。

## 本地数据

```text
pangea-data/
├── repositories/              # 每个一级目录视为一个源码仓，可为 Git 或普通目录
├── inbox/                     # 长期资料：Markdown/TXT/PDF/DOCX/XLSX
├── coverage/                  # 模块—函数—覆盖次数 Excel
└── runs/                      # 任务、结果、进度、证据和报告
```

项目不自动对 `repositories/` 内的用户源码执行 `git pull`、`reset`、`stash`、
`checkout` 或格式化。`inbox/` 文件不会被移动；其文本索引长期保存在
`pangea-data/.pangea/materials.sqlite`。资料中的测试用例只作为表达和环境参考，
不能证明某项风险已经覆盖。

## 分析流程

准备任务契约后运行：

```powershell
& ".\.venv\Scripts\python.exe" -m pangea_agent.cli.main module-analysis --contract "examples/task_contract.module-analysis.example.json"
```

命令是可恢复的阶段推进器：

```text
准备源码、资料和 inventory
→ WAITING_ANALYSIS（最多 4 个 analysis-worker）
→ WAITING_REVIEW（1 个 review-worker）
→ 可选 WAITING_REWORK（最多一次）
→ 可选 WAITING_REWORK_REVIEW（原 reviewer 验证）
→ COMPLETE / INCOMPLETE
```

用户指定的 `source_scope` 是起点，不是盲目的硬边界。准备阶段会做一次有界扩展：
加入直接调用该范围公开函数的源码，以及与目标直接相关的配置入口、规格和测试；不做
递归调用链扩张，也不因此扫描整仓。每个 worker 同时收到必须分析的源码清单和上游
语义清单。风险进入报告前必须核对入口可达性、调用方限制或补救、规格/API 定义和已有
测试；已经被定义为预期行为的结论不能列为风险。该规则不增加新的 Agent 类型或复核层。

当前 Agent 读取 `pangea-data/runs/<run-id>/agent-tasks/`，把结果写到 task 声明的
`result_path`，再使用同一 contract 重复运行命令推进。worker 禁止派生子 Agent；
返工 worker 可以替代失败的原 worker，但不增加返工轮次；返工复核必须沿用原
reviewer，否则生成不完整报告。

截断、格式错误、任务摘要不匹配、范围遗漏或缺少证据的结果不会被接受。最终固定输出：

```text
pangea-data/runs/<run-id>/report.md
pangea-data/runs/<run-id>/report.html
```

HTML 是无外链的离线单文件，支持目录跳转和内容折叠。Mermaid 在未内嵌运行库时
保留可读源码，不虚假显示为已渲染图。

## V1 能力边界

- C/C++ 使用 Python `tree-sitter` 提取函数、类型、分支和条件编译；失败文件继续
  原始文本分析，并在报告中标明范围。
- 文本型 PDF、Word、Excel 和 Markdown 可索引；文档图片提取为 evidence
  attachment，客户端不能看图时标为未解析。
- Coverage 只证明函数执行线索，不证明代码分支或风险已覆盖。
- 使用六维 DFX：功能与状态、资源与规格、性能与压力、并发与异常、升级与兼容、
  可靠性与一致性。
- V1 不包含安全专项、SFMEA、OCR、向量数据库、编译工具链、测试自动执行、代码
  改进建议、实现质量评价以及自动更新用户源码。

## 项目结构

- `src/pangea_agent/graph/`：阶段流程和恢复状态。
- `src/pangea_agent/index/`、`documents/`、`inventory/`：确定性解析与检索。
- `src/pangea_agent/models/`、`schemas/`：worker 和报告数据契约。
- `src/pangea_agent/rubrics/builtin/`：V1 分析方法。
- `src/pangea_agent/report/`：Markdown 和离线 HTML。

更新源码后无需重装 editable package；只有依赖清单变化时才需要再次安装。
