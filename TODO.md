# TODO

## Issue 2: review.json 兼容 unit_reviews 非契约字段

- [ ] 在 `src/pangea_agent/graph/run_store.py` 的 `load_review_result()` 中，对已知历史/Agent 漂移字段 `unit_reviews` 做定向归一化：`payload.pop("unit_reviews", None)`；其余未知字段仍保持 `extra="forbid"` 严格校验。
- [ ] 保留现有 `ReviewResult` / `schemas/review_result.schema.json` 正式契约，不把 `unit_reviews` 加入主 schema。
- [ ] 在 `.opencode/agents/review-worker.md` 与 `.claude/agents/review-worker.md` 中明确限制最终 JSON 顶层字段只能为 `schema_version`、`run_id`、`reviewer_id`、`task_digest`、`finish_reason`、`status`、`summary`、`issues`；逐单元复核过程只用于形成 `summary` / `issues`，不得输出 `unit_reviews`、`details`、`checks` 等额外顶层字段。
- [ ] 验证已卡在 `WAITING_REVIEW` 的旧 Run：恢复时能读取含 `unit_reviews` 的 `agent-results/review.json`，规范化重写后推进到后续阶段。
