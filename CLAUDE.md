# PANGEA action 协议

本仓库的测试分析以 CLI action 为唯一流程指令。主会话只创建/推进 Run 和派发与 action `role` 对应的专用 Agent，不自行读取全部源码、分析语义或填写结果。具体客户端的角色规则只存放在该客户端自己的目录中。

## Run 生命周期

- 新模块分析先形成最小 `source_scope`，再通过当前客户端提供的稳定 Run 创建入口启动；临时契约由该入口管理。
- 没有当前会话明确 `run_id` 时，不扫描历史 Run 猜测恢复对象。
- 同时派发最多 8 个 action；8 是并发上限，不是整个 Run 的单元总数。
- 每次派发后通过 action adapter 保存真实子任务 ID；子 Agent 返回后先校验，通过后再接收并取得下一条 action。
- 校验失败时恢复同一子任务修正同一 `result_path`，主会话不代写。
- 只按 CLI JSON 中的 action 继续，不根据 Agent 回复文字推断阶段。

角色映射：`planning`、`analysis`、`review`、`closure`、`asset_extraction`。

Python 只负责确定性解析、状态、契约和报告。首轮 analysis 已包含流程、调用链、资料/代码差异、Coverage、缺陷机理、风险和用例。review 由同一个 Reviewer 完成两个 checkpoint：先在看不到首轮结果时独立检查，再对照首轮结果与源码排除错误结论并找出遗漏。这是对同一批分析的先盲审、后对照，不是审计上一次审计。有实质问题时只补齐受影响单元，之后聚合。

命令按 Windows PowerShell 可执行方式组织，一次执行一个命令。不得修改 `pangea-data/repositories/` 下用户源码的 Git 状态。
