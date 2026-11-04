# PANGEA Claude 客户端入口

独立 Claude 主会话先读取本客户端 `.claude/protocols/action-lifecycle.md`，只执行 Graph 返回的 action；角色定义在 `.claude/agents/`，旧 Analysis/Closure 按角色指针读取本客户端旧协议。

Desktop 派发的外部 source-first worker 使用宿主提供的通用会话和 `docs/source-first-cli-worker.md`，只执行绑定 action，不读取本客户端主会话生命周期协议。开发与修改仓库遵循 `AGENTS.md`。

新建 Run 只执行 Planning → Analysis → 报告，不派发 Reviewer、盲审、对照审核或 targeted_closure。分析完成后直接组装报告；结果未经过独立审核，不得把流程完成表述为审核通过。历史审核记录和冻结合同仅保留读取兼容，不改写用户已冻结的数据。


当 `progress.review_policy="disabled"` 且 `lifecycle_status=complete`、正式报告齐备时，这是正常的未审核交付，明确说明“未执行模型审核，分析已完成”，给出报告路径。不得仅因 `quality_status=UNRESOLVED` 宣称质量未通过，也不得宣称审核通过；具体待确认项按报告原文呈现。旧记录无此标记时，仅依据真实历史审核结论说明其质量状态，不推测审核结论。
