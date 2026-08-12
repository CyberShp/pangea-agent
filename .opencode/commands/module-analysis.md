---
agent: pangea-agent
description: 按 task_contract 执行模块分析
---
# module-analysis

执行：

```powershell
pangea module-analysis --contract examples/task_contract.module-analysis.example.json
```

正式使用时，把 contract 指向用户准备的任务契约文件。

模块分析流程由 `src/pangea_agent/graph/graph.py` 定义，输出保存在 `pangea-data/runs/<run-id>/`。
命令可能返回 `WAITING_ANALYSIS`、`WAITING_REVIEW`、`WAITING_REWORK` 或
`WAITING_REWORK_REVIEW`。主 Agent 完成对应 JSON task 后，用同一 contract 再次执行，
直到返回固定路径 `report.md` 和 `report.html`。
