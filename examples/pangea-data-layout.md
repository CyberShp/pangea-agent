# pangea-data 本地目录

`pangea-data/` 不进入 Git。使用以下命令创建：

```powershell
pangea init-data
```

目录职责：

- `repositories/`：待分析源码，可 Git，也可非 Git。
- `inbox/`：需求、设计、历史缺陷和其他参考资料的原始文件。
- `coverage/`：覆盖率资料。
- `assets/`：资产元数据、结构化提取任务和提取结果。
- `runs/`：每次分析的索引、证据、风险、用例和报告。

pangea-agent 不自动对用户源码执行 `git pull`、`reset`、`stash` 或 `checkout`。
