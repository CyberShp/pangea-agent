# pangea-agent

`pangea-agent` 是面向测试分析的 LangGraph 风格 Agent 骨架。目标是从源码、设计资料、覆盖率和已有用例中生成风险账本、测试点、测试用例和报告。

## 核心原则

- `graph` 是唯一流程源。
- `schemas/` 是唯一数据契约。
- `rubrics/` 是唯一分析方法论来源。
- 用户源码、输入资料、索引、Run 结果不进入 Git。
- 不做全仓 SHA、不做源码冻结快照、不做套娃审计。
- Git 信息只做版本说明；非 Git 源码目录也允许分析。

## 目录约定

```text
pangea-agent/
├── src/pangea_agent/          # 框架代码
├── schemas/                   # JSON Schema 数据契约
├── examples/                  # 示例 contract 与输出样例
└── pangea-data/               # 本地用户数据，已被 .gitignore 忽略
```

本地数据目录由命令创建：

```bash
pangea init-data
```

数据目录结构：

```text
pangea-data/
├── repositories/              # 用户待分析源码，可 Git，也可非 Git
├── inbox/                     # 需求、设计、历史缺陷、测试报告
├── coverage/                  # 覆盖率资料
├── testcases/                 # 已有测试用例
└── runs/                      # 每次分析的索引、证据、风险、用例和报告
```

## 最小流程

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

## 快速开始

```bash
pip install -e .
pangea init-data
pangea module-analysis --contract examples/task_contract.module-analysis.example.json
```

第一版为骨架，节点目前只保留清晰职责和可扩展接口。
