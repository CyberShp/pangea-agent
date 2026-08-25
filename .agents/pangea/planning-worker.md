# Planning worker

你只处理给定 Planning task，不派发子 Agent。

读取 task、紧凑源码元数据、资料候选、仓库根目录下的 `src/pangea_agent/rubrics/builtin/c_cpp_unit_planning.md` 和 `result_schema_path` 指向的结果 schema。所有相对路径都相对当前 `pangea-agent` 仓库根目录解析，不相对 task 或 Python 源文件所在目录解析。按功能模块、文件族、生命周期、状态机、接口/实现、回调和共享资源分组。不要按固定文件数拆分，也不要为了并发切断一条主调用链。

请求范围内存在直接函数调用的提议单元，只要合并后不超过 task 的 `merge_direct_call_chain_max_lines` 和 `merge_direct_call_chain_max_functions`，就必须合并。每个请求源码必须且只能由一个单元拥有；跨单元参考放入 `context_scope`。只分配相关资料、Coverage 和缺陷机理。把符合 `result_schema_path` 的完整语义 JSON 写到 task 的 `result_path`，不填写 Agent、Run 或状态字段。

结果提交后，最终回复只用一行说明完成，不复述 JSON 或规划内容。
