# 模块结构化流程先行

适用于 behavior-test-v2 全部四场景。先理解所选对象入口、主干步骤、关键判断、状态变化、异常退出与恢复及必要上下游关系，再开展当前专项。
只深入对象及必要依赖；不为先出流程而完整扫描所有风险、分支或无关模块，不增加全局串行阶段或子Agent。

在当前 analysis action 中先保存 kind=flow 的结构化业务流程；专项分析得到新证据后更新同一逻辑 flow_id 并补充用例关联。跨单元的主干由purpose指定的现有worker负责，连接必须有源码依据，不猜补其他单元结论。

恢复 behavior-flow-v1 正文合同：format_version、flow_id、title、description、nodes、edges、paths、source_evidence。
- nodes：每个节点使用 id、kind、label、description；入口为 kind=entry，其他节点按真实业务动作、判断和结果表达。
- edges：使用 source_step_key、target_step_key、condition，端点引用已有 nodes.id，表达实际走向、回接或结束。
- paths：使用 path_id、title、condition、node_ids、explanation、case_ids；node_ids 按实际执行顺序引用节点。专项用例产出后补充实际 case_ids，不能虚构关联。
- description 为流程说明，text 只能作为补充，不能替代 nodes、edges、paths。不要将逐函数/变量列表当作模块流程。

实际证据引用冻结源码，保存在 source_evidence 中供追溯，业务流程页面不展示该字段。文档预期与实现不同时分开说明。
保存前由当前 worker 核对节点、连线和路径引用；Reviewer 负责核实流程语义。Python 不拆分自由文本、不补造节点、不新增语义门禁。
已有 module-flow-text-v1 记录保留可读；需要补齐时由原 worker 结合冻结源码修正原流程，不能由客户端猜测。流程使用稳定flow_id；修正以准确record_id supersedes，保留旧记录及正确的用例关联，不重做其他单元。
Archify和函数变量图仅由用户单独触发，本任务不生成图任务、不调用绘图脚本。
