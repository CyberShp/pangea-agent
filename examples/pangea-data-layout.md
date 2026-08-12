# pangea-data 本地目录

`pangea-data/` 不进入 Git。使用以下命令创建：

```bash
pangea init-data
```

目录职责：

- `repositories/`：待分析源码，可 Git，也可非 Git。
- `inbox/`：需求、设计、历史缺陷、测试报告。
- `coverage/`：覆盖率资料。
- `testcases/`：已有测试用例。
- `runs/`：每次分析的索引、证据、风险、用例和报告。

pangea-agent 不自动对用户源码执行 `git pull`、`reset`、`stash` 或 `checkout`。
