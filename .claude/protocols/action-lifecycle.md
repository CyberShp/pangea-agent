# PANGEA action 协议

本仓库以 CLI action 作为唯一流程指令。主会话只创建和推进 Run，不自行分析源码或填写结果。

## Run 生命周期

- 新模块分析先在 `pangea-data/repositories/` 自动搜索目标模块并确定最小 `source_scope`，再通过当前客户端的稳定入口启动；`.c` / `.h` / `.cc` / `.cpp` / `.cxx` / `.hpp` / `.hh` 识别为 `c_cpp`，`.lua` 识别为 `lua`，用户不需要另填语言。没有当前会话明确 `run_id` 时不扫描历史 Run。
- source-first 的 analysis 最多三个 worker 并发，Graph/result 写入串行；总单元数不受并发上限限制。旧 Graph 按冻结 action 合同执行。
- `dispatch_agent` 按 role 创建专用 Agent；`continue_agent` 恢复 action 自带的同一 `task_id`，不得创建替代任务。
- 子 Agent 返回后对完成报告中的 exact `action_id` 直接执行 adapter settle。`status=invalid` 时，settle 会把同一 Action 持久化为 `continue_agent + pending + repair_status=required`；按 `repair_action` 恢复同一任务，只修正同一 `result_path`，通过后再次 settle。
- 普通结果校验失败不推进 Action，也不停止 Run。Run/action/task、冻结输入或约定 task_id 损坏才属于流程错误。
- Schema 非法结果不得自动降级或推进 Graph；达到 attention 阈值只停止盲试并保留完整 Validation Report。
- 只按 CLI JSON action 继续，不根据 Agent 回复文字推断阶段。

角色映射只用于 `dispatch_agent`：`planning`、`analysis`、`asset_extraction`。

Python 负责确定性解析、状态、契约和报告，不判断测试用例语义。新建 Run 只执行 Planning → Analysis → 报告，不派发 Reviewer、盲审、对照审核或 targeted_closure。分析完成后直接组装报告；结果未经过独立审核，不得把流程完成表述为审核通过。历史审核记录和冻结合同仅保留读取兼容，不改写用户已冻结的数据。

结果骨架和唯一结果路径由 Workflow 创建。主会话不得另建、改名、代填或从其他文件兜底读取结果。

命令按 Windows PowerShell 可执行方式组织，一次执行一个命令。不得修改 `pangea-data/repositories/` 下用户源码的 Git 状态。


当 `progress.review_policy="disabled"` 且 `lifecycle_status=complete`、正式报告齐备时，这是正常的未审核交付，明确说明“未执行模型审核，分析已完成”，给出报告路径。不得仅因 `quality_status=UNRESOLVED` 宣称质量未通过，也不得宣称审核通过；具体待确认项按报告原文呈现。旧记录无此标记时，仅依据真实历史审核结论说明其质量状态，不推测审核结论。
