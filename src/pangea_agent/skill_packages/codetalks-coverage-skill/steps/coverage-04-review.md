# 阶段 04：复核与定向修订

深度型由宿主启动与 Producer 分离的真实 Reviewer，读取 `references/worker-judge-protocol.md` 的 Judge 要求。
复核覆盖来源/缺口定位、入口/分支、源码证据、实际参数类型、保护条件、支持范围、异常传播、遗漏以及测试的外部可执行性、独立观测、Oracle 和恢复。主动尝试反例，不能只重复 Producer 结论。

集中写 `活文档/复核记录.md`：review_issue_id、受影响 Flow/Risk/Case ID、证据、保留/驳回/合并/未决及理由、原文件路径、Producer 定向修订和 Reviewer 复查结果。
直接修订受影响的原缺口分析、风险或用例，同步台账与投影；不重开阶段 03、不清空完成状态、不重新写全部报告。阶段 04 内可以多次 progress/publish-stage，新增发现保持稳定 ID。
在现有 `内部索引/独立审查状态.json` 记录 independent、checked_artifacts、真实会话 ID/调用证据、semantic_verdict（PASS/UNRESOLVED）和 summary。不得伪造身份或把自审说成独立审查。
深度型无法独立复核时保存当前进度并报告受阻；不要切换 speed 绕过。复核结束但争议未解可记录 UNRESOLVED，正式报告必须呈现；待分析内容仍是未完成。
