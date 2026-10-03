# 已有 Run 的定向补充与文件变更分析

仅当 task 包含 `incremental_request` 时应用本规则。先读取 `incremental_request`、
`incremental_changes` 与 `incremental_parent_plan`，按需从 `incremental_baseline`
查找父记录，再对 `incremental_parent_records` 使用索引内 cursor 分页读取原文；
不从头遍历所有历史记录。父记录只提供历史参考，
不是本次 Run 已接受结果；记录地址必须包含 parent_run_id、action_id、record_id 和 revision。

## Planning

- 用户说明、selected_unit_ids、selected_records 与 changed_paths 是本次目标。
  目录、父计划和父记录索引只表示可检索范围，不要求逐项阅读、复查或重做全部单元。
- supplement 使用父 Run 的冻结源码和资料，不表示工作仓库当前版本。
  changed-files 使用本次冻结的当前源码；`baseline_source_*` 和 `source_diff_*`
  只表示用户指定文件的历史内容和字节差异。baseline-missing 表示父范围未冻结该文件，
  不能断言它是新文件；deleted 表示本次文件不存在，不能将旧源码当当前实现。
- 父合同、资产元数据和记录中出现的历史绝对路径仅保留来源线索，不可直接读取；
  本次可读输入均通过当前 task 的 input_id 与冻结 source 工具访问。
- 结合源码明确变化影响哪些业务路径、调用方、状态和资源生命周期，由你决定必要依赖。
  只为本次补充目标或变化影响规划单元；在 purpose 中说明与用户目标、父记录的关系及补读原因。
  文件变化不能自动等同于相关用例失效，文件没变化也不能自动证明旧结论继续成立。
- 依赖超出冻结可读范围时明确记录未确认部分，不扩大为全仓分析、不自行假造依赖。

## Analysis 与 Review

- 本次仍按冻结场景交付目标范围内的流程、用例及相应风险职责。优先复用已经核实的历史
  背景，定向产生新增或变化内容；不把父 Run 全部流程、用例和风险复制为本次交付。
- 在正常 note/summary 中说明哪些父记录可继续参考、哪些需要更新、哪些证据不足，
  并写出完整父地址；无需新增结果字段、语义门禁或质量评分。
- 即使源码 hash 相同，规则或任务目标变化也需要重新核验结论的适用性。历史 coverage
  只表达父版本当时数据，不是当前版本覆盖率，也不证明新增用例执行后已消除缺口。
- Reviewer 检查新增和变化路径、引用的适用性及必要关联影响。标准模式的独立盲审
  不得读取本次首轮 Analysis 结果；之后沿用同一 Reviewer 对照裁决。速度模式沿用直接审核。
  不因这是增量任务新增完整审查轮次，不因历史 Run 的 PASS 跳过本次 Review。
- 旧 Run 不被修改，本次结果仅对本次冻结目标负责；未核验内容明确标记待确认。
