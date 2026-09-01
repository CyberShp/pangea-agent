# PANGEA action 协议

本仓库以 CLI action 作为唯一流程指令。主会话只创建和推进 Run，不自行分析源码或填写结果。

## Run 生命周期

- 新模块分析先在 `pangea-data/repositories/` 自动搜索目标模块并确定最小 `source_scope`，再通过当前客户端的稳定入口启动；`.c` / `.h` / `.cc` / `.cpp` / `.cxx` / `.hpp` / `.hh` 识别为 `c_cpp`，`.lua` 识别为 `lua`，用户不需要另填语言。没有当前会话明确 `run_id` 时不扫描历史 Run。
- 同时派发最多 8 个 action；8 是并发上限，不是整个 Run 的单元总数。
- `dispatch_agent` 按 role 创建专用 Agent；`continue_agent` 恢复 action 自带的同一 `task_id`，不得创建替代任务。
- 子 Agent 返回后先执行 adapter validate。`status=invalid` 时按 `repair_action` 把错误交回同一任务，只修正同一 `result_path`；通过后再 settle。
- 普通结果校验失败不推进 Action，也不停止 Run。Run/action/task、冻结输入或约定 task_id 损坏才属于流程错误。
- 只按 CLI JSON action 继续，不根据 Agent 回复文字推断阶段。

角色映射只用于 `dispatch_agent`：`planning`、`analysis`、`review`、`asset_extraction`。

Python 负责确定性解析、状态、契约和报告，不判断测试用例或 Reviewer 的语义结论。Review 由盲审 Reviewer 完成 `independent_review`，再由独立 Adjudicator Session 完成 `comparison_review`。定向补齐以 `continue_agent` 续接对应单元首轮 analysis worker；Graph 预先复制原结果，worker 只修改 closure `result_path`。

结果骨架和唯一结果路径由 Workflow 创建。主会话不得另建、改名、代填或从其他文件兜底读取结果。

命令按 Windows PowerShell 可执行方式组织，一次执行一个命令。不得修改 `pangea-data/repositories/` 下用户源码的 Git 状态。
