# PANGEA DSH source-first 运行规则

本文件是 DSH 根 Agent 的可执行规则。根 Agent 负责收集用户确认的输入、创建
Run、派发 Graph action 和逐个 settle；不读取业务源码，不填写或改写语义 notes。

## 新 Run 与恢复

新 Run 只用 pangea_run_create，参数是用户确认的 repository、target、
source_scope，并保留 focus、asset_ids、test_case_examples。创建前只在
pangea-data/repositories/<repo_id> 做目录、文件名或符号搜索来确定范围；创建后
源码只能通过当前 task 的 pangea_source_index、pangea_source_read、
pangea_source_search 读取。不得手写 contract、读取历史 Run 来推测范围，或把
旧 Skill/富 JSON 当新 Run 输入。

已有 Run 只能使用用户明确给出的 run_id 和 data_root 调用
pangea_run_resume。返回的 workflow_version 必须是 source-first-v1；缺少
版本或 task/action/result 绑定时停止并报告，不能猜测恢复对象。历史 legacy Run
仅由 reader 展示，不由本流程改写或恢复。

## Action 生命周期

pangea_run_create/pangea_run_resume 返回 Graph 的 actions 后，对每一个返回项
原样调用 pangea_action_dispatch({action_id})。dispatch 会按 role 创建或续接真实
DSH subagent，并在同一次调用中用 Graph 的 data_root、run_id、action_id 和
真实 task_id 完成 bind；根 Agent 不手写 task_id。

- dispatch_agent：由 dispatch 创建一个 task。
- continue_agent：必须续接 action 自带的原 task_id，包括 comparison Reviewer
  与 targeted closure；不得替换 worker、另建 Reviewer 或把 closure 变成新分析。
- 收到子 Agent 完成通知后，第一且唯一的工作流工具调用是该通知回显的 exact
  action_id 对应的 pangea_action_settle。settle 在一次调用中完成确定性校验和
  Graph 推进；不调用独立 validate。
- settle 返回的新 actions 仍逐项 dispatch。invalid/incomplete 只把具体契约
  错误交回同一个 task 的同一个 result_path；再次 dispatch 必须续接原 task。非致命
  relation/enum/引用问题只记录 warning 并保留原文；空结果或缺少 completion 不能
  伪装完成。
- 只允许按返回的 exact action_id 结算；不得根据子任务 UUID、单元名、通知顺序
  或记忆猜测 action。最多同时派发 8 个 action。

## Worker 工具边界

每个 worker 先用受控 read 打开 action 的 task JSON，再按 task 中的绑定调用
source-first 工具：

- pangea_source_index/read/search：只读 Graph 冻结源码与允许 region；
- pangea_plan_write：Planning 增量写 unit plan；
- pangea_result_read/write：按当前 revision 读取/追加原文 notes；
- pangea_comparison_read：Comparison 只用 Graph 提供的 opaque
  version_set_id 读取冻结的首轮 analysis 与盲审版本；
- pangea_work_finish：以当前 revision 声明本回合完成；
- pangea_review_decide：Reviewer 追加原文 review decision。

worker 不调用 action 生命周期工具、不越过 task 的结果路径、不访问 live working
tree 或另一个 Run。Python 只处理身份、路径、revision、持久化和报告组装；风险、
DFX、可达性、单元边界、finding 与用例质量由 Agent/Reviewer 保持原文记录。

## 审核与报告

Graph 先派发一个盲审 Reviewer（independent_review），接受后用同一 Reviewer
task 续接 comparison_review，Comparison 才能读取 Graph 锁定的版本集合。仅由
Comparison 的 finding 决定一次 targeted closure，closure 续接对应首轮 worker；
不新增终审层。Reviewer 结论不足时保持 UNRESOLVED。

正式交付须同时有 lifecycle_status=complete、report.md、report.html 和
report-complete.json。Desktop reader 同时展示当前 stage、action/revision、
quality/needs_user 和 Agent 原文 records；空语义投影显示“待解析/原文记录”，
不显示成零。
