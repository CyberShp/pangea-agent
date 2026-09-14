# C/C++ 分析单元规划

根据 task 提供的源码元数据和资产候选，把请求范围划分为功能完整、工作量受控的分析单元。

优先按以下关系归并：

- 同一对外功能、生命周期或状态机；
- 接口声明与直接实现；
- 调用方与关键被调函数；
- 回调注册方与回调实现；
- 共同读写同一状态或资源；
- 设计资料明确归属于同一模块。

不要只根据文件名前缀拆分，也不要为了增加并发而把一条调用链切成多个很小的单元。每个源码文件只能归属于一个单元；其他单元需要读取时放入 `context_scope`。

若不同提议单元之间存在请求范围内的直接函数调用，只有在合并后的源码行数和函数数同时不超过 task 的 `max_unit_*` 工作量预算与 `merge_direct_call_chain_*` 界限时才能合并。单元边界由 Planning Agent 按这些真实元数据决定；Python 只验证和记录结果，不替 Agent 做语义归并。

读取 `compact_metadata_path` 后，先在 `owned_source_paths` 和 `files[].path` 中逐个核对 `requested_scope`，再做单元分组。不得仅从分组摘要推断某个请求文件缺少元数据。

`asset_item_ids`、`coverage_ids` 和 `mechanism_ids` 只选择与该单元功能相关的候选。无法确定归属时保留在 `unresolved`，不要任意分配。

## target-first-v1 范围规则

task.scope_policy=target-first-v1 时，target 是分析对象，owned_scope_paths 是可选主责候选，
不是整目录交付义务。先按业务目标选 owned_files/owned_regions，再分单元；必要调用方、
CHAP 等耦合依赖放 context，其他功能不生成独立用例。同文件包含其他功能时允许按 region
划分，不受“整文件一个 unit”限制。以源码关系判断，不能只匹配 TLS 等名字。
用一条范围 note 写清主责、必要依赖和未纳入范围的功能及理由；未分配候选不是自动遗漏，
但不能漏掉 target 的真实业务路径。旧 task 未声明此 policy 时仍按其冻结范围执行。
