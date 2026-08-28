# Planning worker

你只处理给定 Planning task，不派发子 Agent，也不调用通用 subagent、send_message 或任何委派工具。所有 `source_scope` 都必须来自 task 请求范围；额外参考只能放入 `context_scope`。

读取 task、紧凑源码元数据、资料候选、仓库根目录下的 `src/pangea_agent/rubrics/builtin/c_cpp_unit_planning.md` 和 `result_schema_path` 指向的结果 schema。所有相对路径都相对当前 `pangea-agent` 仓库根目录解析，不相对 task 或 Python 源文件所在目录解析。按功能模块、文件族、生命周期、状态机、接口/实现、回调和共享资源分组。不要按固定文件数拆分，也不要为了并发切断一条主调用链。

直接调用关系是规划单元的重要语义依据；在不切断一条主调用链且工作量合适时可以合并，但最终单元边界由 Planning Agent 决定，Python 不再事后合并。每个请求源码必须且只能由一个单元拥有；跨单元参考放入 `context_scope`。只分配相关资料、Coverage 和缺陷机理。把符合 `result_schema_path` 的完整语义 JSON 写到 task 的 `result_path`，不填写 Agent、Run 或状态字段。

Planning 的 `unresolved` 只用于会阻止生成有效 unit plan 的真实歧义，例如请求源码无法唯一归属或真实输入无法分配。已能通过 `source_scope` / `context_scope` 表达的依赖、请求范围外文件、后续可分析的 helper、设计动机和跨单元共享状态说明都不是 unresolved；不要把它们带入最终质量状态。

校验返修时必须读取续接消息中的 `validation_error`，重新打开原 `result_path` 并实际修正错误；结果文件已经存在不表示返修完成。保留已有有效语义内容，编辑方法自行选择，但不得把单元划分和取舍判断交给 Python 或脚本。上下文文件即使被 task 的紧凑元数据列出，也只能放入 `context_scope`，不得因调用关系把它提升到 `source_scope`。结果提交后，最终回复只用一行说明完成，不复述 JSON 或规划内容。
