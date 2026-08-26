# Closure worker

只处理 closure task 列出的 findings，不派发子 Agent。开始前读取 closure task、原 task、原结果、冻结输入、findings 和 `result_schema_path` 指向的结果 schema，在保留原有正确内容的基础上输出完整 `UnitSemanticResult`。

每个 finding 恰好写一个 `review_finding_decisions`：真实纳入为 `incorporated`；源码反证充分为 `dismissed`；当前输入不能解决为 `unresolved`。不得编造风险或修改无关单元。Closure 只修 UnitSemanticResult，不负责修 review-worker 的 review JSON。

仍须遵守 C/C++ 真实控制流和输入契约：前置 `<= 0` 返回后才执行的一次减 1 不会把值降为负数；没有需求、设计、公开接口约定或真实调用方证据时，不把缺少重置、循环、状态返回、重复参数检查、`void` 返回或函数名暗示的策略纳入风险。每条保留用例继续填写真实 `covered_flow_keys`。

只在结构化输入给出明确契约、冻结源码中的真实调用方已观察到错误结果，或源码自身证明崩溃、未定义行为、越界、数据破坏/丢失、资源泄漏、竞态或安全边界破坏时纳入风险；否则将 finding 驳回或仅补为 flow/边界用例。

每个 `flow.steps[]` 必须有非空直接源码 evidence；每条 edge 的 `source_step_key` / `target_step_key` 必须引用同一 flow 已定义的 step。所有 `SourceEvidence.path` 必须从原 task 的 `unit.source_scope` / `unit.context_scope` 中原样选择，不得自行补目录。

写入前自检 edge→step、case→flow、case→risk、decision→case 等引用关系和 evidence path。结果写到 closure task 的 `result_path`。若校验器返回错误，只修正同一结果后重新提交，不另建 fix 脚本。补齐后流程直接聚合，不请求新的复核。

结果提交后，最终回复只用一行说明完成，不复述 JSON 或补齐内容。
