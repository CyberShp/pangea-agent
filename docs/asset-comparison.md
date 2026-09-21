# 资产对照实验

为同一源码范围分别创建两个 Run，仅改变选用资产或启用的方法论。两次使用相同目标、关注点、
模型、分析设置、上下文预算和运行版本。固定源码和用例附件；每次 Run 仍由正常 Workflow
生成、冻结和审核结果。不要修改既有 Run 来模拟对照。

```powershell
python -m pangea_agent.cli.main runs compare-assets --data-root 'pangea-data' --baseline-run-id 'baseline-run' --candidate-run-id 'asset-run'
```

命令只读取明确指定的两个 Run，JSON 输出沿用 CLI 的 `ok/result` 外壳，不改写输入、
结果、状态或报告。仅支持有 source-first 冻结材料的记录对照；旧 Run 缺失材料会显示
`unknown`、`null` 或 warning，不能当作零投入或零收益。

- `comparability.checks` 比较冻结源码及上下文字节、目标与设置、运行来源、内置方法论、
  用例附件。`same` 仅代表这些已记录变量一致；`different` 或 `unknown` 时应先核对差异。
- `input_linkage` 列出可用 ID、Planning 选用 ID，以及用例显式关联的已知资产条目、
  Coverage 和方法论 ID。方法论选用不等于已经提升用例。未填写关联也不等于没有采用。
- `cases` 保留交付用例原文和 action/record/result 路径，采用与报告一致的已接受定向
  修正投影，仅统计 accepted 或明确交付的 action，排除草稿和被替代记录。Run 内用例
  编号不作为跨 Run 的同义场景匹配依据。`case_records_complete=false` 时已读取的
  案例仍保留，总数及数量差为 `null`；缺失计划的选用 ID 为 `null`。
- `difference.worker_elapsed_ms` 是已记录 worker 执行累计时长之差；并发时不是 Run
  墙钟耗时。任一 Run 尚未明确 complete、action 缺少计时或仍有未结束回合时不计算总时长差。原始计时和各阶段
  数据仍可查看。没有记录的 token 或费用不作估算。

Reviewer 或人工并排阅读两组用例与冻结资产，判断新增了哪些有效场景、哪些只是改写、
是否出现误报，以及新增价值是否值得额外耗时。Python 仅统计记录与精确 ID 引用，不靠
关键词、数量或 Coverage 关联自动判断准确率、执行覆盖率、语义收益或 `PASS`。

建议先对一个小模块测试一种资产，再扩展到多个模块、历史缺陷及方法论；单次有／无资产
对照不能排除模型随机性。`semantic_assessment=requires_review` 明确保留这一步人工判断。
