# 模块结构化流程先行
risk 场景（method_version=risk-method-v1）按 scene_risk 的工作顺序，可以在完成镜头检查后再保存流程，流程仍是必交付内容。

适用于 behavior-test-v2 全部四场景。先理解所选对象入口、主干步骤、关键判断、状态变化、异常退出与恢复及必要上下游关系，再开展当前专项。
只深入对象及必要依赖；不为先出流程而完整扫描所有风险、分支或无关模块，不增加全局串行阶段或子Agent。

在当前 analysis action 中先保存 kind=flow 的结构化业务流程（risk 场景按 scene_risk 的工作顺序）；专项分析得到新证据后更新同一逻辑 flow_id 并补充用例关联。跨单元的主干由purpose指定的现有worker负责，连接必须有源码依据，不猜补其他单元结论。

恢复 behavior-flow-v1 正文合同：format_version、flow_id、title、description、role、attach_to、nodes、edges、paths、source_evidence。
- nodes：每个节点使用 id、kind、label、description；入口为 kind=entry，其他节点按真实业务动作、判断和结果表达。
- edges：使用 source_step_key、target_step_key、condition，端点引用已有 nodes.id，表达实际走向、回接或结束。
- paths：使用 path_id、title、condition、node_ids、explanation、case_ids；node_ids 按实际执行顺序引用节点。专项用例产出后补充实际 case_ids，不能虚构关联。
- description 为流程说明，text 只能作为补充，不能替代 nodes、edges、paths。不要将逐函数/变量列表当作模块流程。

关键判断用独立节点表达；不同条件走向实际处理或结果节点，汇合时再引用同一后继。若两条路径条件不同但node_ids完全相同，核对是否漏掉判断后的处理差异，不靠改标题表达真假分支。普通顺序连线的condition留空，只有实际守卫或选择条件才填写，不能把“下一步”当作条件分支。调用者不在冻结范围时，区分已证实的函数内行为与待确认的业务调度顺序，不把使用建议写成已验证调用链。

实际证据引用冻结源码，保存在 source_evidence 中供追溯，业务流程页面不展示该字段。文档预期与实现不同时分开说明。
保存前由当前 worker 核对节点、连线和路径引用；Reviewer 负责核实流程语义。flow 记录的结构校验（字段、id 引用）属于格式契约，不属于语义门禁：不合格时写入被拒绝并逐条返回错误，由同一 worker 在本轮修正；Python 不拆分自由文本、不自动修复、不补造节点、不新增语义门禁。
已有 module-flow-text-v1 记录保留可读；需要补齐时由原 worker 结合冻结源码修正原流程，不能由客户端猜测。流程使用稳定flow_id；修正以准确record_id supersedes，保留旧记录及正确的用例关联，不重做其他单元。
worker 不绘制任何图，不生成图任务、不调用绘图脚本：流程图由宿主根据 flow 记录确定性渲染；函数变量图（call-graph-v1）仅由用户单独触发。

<!-- FLOW-CONTRACT:BEGIN -->
### flow 记录标准样例（behavior-flow-v1，照此结构填写）

```json
{
  "format_version": "behavior-flow-v1",
  "flow_id": "F-001",
  "title": "用户登录",
  "description": "用户提交凭据，经鉴权后建立会话；失败时按原因返回。",
  "role": "main",
  "attach_to": null,
  "nodes": [
    {"id": "submit", "kind": "entry", "label": "提交用户名和密码", "description": "login_handler 接收请求", "source_evidence": ["web:src/login.c:20"]},
    {"id": "verify", "kind": "main", "label": "校验密码", "description": "check_password 比对哈希"},
    {"id": "locked", "kind": "error", "label": "账户锁定", "description": "连续失败 5 次后拒绝"},
    {"id": "session", "kind": "exit", "label": "建立会话", "description": "写入 session 表并返回 token"}
  ],
  "edges": [
    {"source_step_key": "submit", "target_step_key": "verify", "condition": ""},
    {"source_step_key": "verify", "target_step_key": "session", "condition": "密码匹配"},
    {"source_step_key": "verify", "target_step_key": "locked", "condition": "失败次数 ≥ 5"}
  ],
  "paths": [
    {"path_id": "P-01", "title": "正常登录", "condition": "密码正确", "node_ids": ["submit", "verify", "session"], "explanation": "…", "case_ids": []}
  ],
  "source_evidence": ["web:src/login.c:20-88"]
}
```

硬性规则：
- 字段名必须与样例完全一致（英文），字段值使用中文。
- 一条 flow 记录只放一个流程；有多个流程就写多条 flow 记录。
- edges 的 source_step_key、target_step_key 必须写节点 id，不能写 label；paths.node_ids 同样只引用节点 id。
- body 直接是 JSON 对象：不要写成字符串，不要包 ```json 代码块，不要外套 flow 或 flows；不要用 steps、from/to、node_id、name、节点、连线、路径 等替代字段。
- 节点 kind 只能取 entry | main | branch | error | propagation | recovery | exit。
- role 取 main 或 sub：只有 purpose 指定的 owner 单元的主流程写 main，其他流程一律写 sub。
- attach_to 写子流程挂接到主流程的位置，如 {"flow_id": "F-001", "node_id": "verify"}；不确定时写 null。role/attach_to 只用于排版，缺失时按 sub、未挂接处理。
- 写入时会做结构检查（字段名、id 唯一与引用）；不合格时 pangea_result_write 返回逐条错误，请在本轮按错误修正后重写同一结果。
<!-- FLOW-CONTRACT:END -->
