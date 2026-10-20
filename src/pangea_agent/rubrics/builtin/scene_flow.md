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

## 分支深度与处理闭环

以下方法参考固定版本的 softaworks/agent-toolkit `mermaid-diagrams`（3027f20f）与 WH-2099/mermaid-skill（982d9035），已适配本产品 JSON 合同；只使用本 Run 冻结的规则，不安装 Mermaid、不联网、不把 Mermaid 代码写入 flow。通用绘图规范负责表达方式，源码和本场景职责决定实际内容。

- 先逐个核对当前入口中会改变业务走向的判断、返回/提前退出、事件分发、错误传播、重试与资源交接，再形成 nodes/edges/paths。不要只沿成功路径阅读，然后把其他事件统一塞进一条边。函数名不是节点粒度标准，也不设固定节点数量。
- 一条已证实的分支须能回答：什么条件触发，随后做了什么，改变了什么状态/资源，对调用方或外部呈现什么结果，在哪里回接、等待、重试或结束。实际存在且决定结果的步骤分别成节点；没有动作的直接返回可以直接连到出口，不能为了形式完整捏造清理或恢复。
- 连线 condition 只表达触发事件或守卫，不能承载整个处理过程。`recv_fin`、`recv_rst`、超时、取消、资源不足等若在源码中有独立处理，分别连到对应处理节点；即使最终共用释放/退出节点，也要保留释放前的差异。正常关闭、预期拒绝、可重试中间态与失效分开说明，error 节点不等于产品缺陷。
- 读到分发器或被调子过程时，在当前对象及必要依赖内追到业务结果。对同一资源说明所有权移交/保留/释放发生在哪里；对异步工作说明受理、等待回调和完成边界。看不到回调或调用者时，在 description 明确已知边界和待确认，不猜跨入口调用顺序。
- 复杂过程拆成具有真实业务边界的 sub，并用 attach_to 指向准确节点；父图仍保留判断、分支去向与出口，子图展开实际处理。不能用“处理异常”一个总括节点、一个异常箭头或省略号隐藏多个不同结果，也不能用虚线替代缺失节点。

示意（假设源码确有以下行为；不代表任何协议实现必然如此）：一次接收先判断结果；收到数据后再检查报文完整性，完整则提交处理，不完整则保留接收状态并等待后续；对端正常关闭先停止接收并完成待处理请求，再释放连接；连接复位先向请求方报告失败，再释放连接；暂时不可读保留连接并退出本次回调等待下一次事件。这里“数据/FIN/RST/暂不可读”是条件，“保留状态/完成请求/报告失败/释放连接”是节点。共享释放节点不抹掉此前不同处理；不同回调也不凭空连成一次连续执行。

### 写入前与写入响应自检

按真实执行顺序先走一遍各 paths，再填写两份引用；同时修改路径时一并更新 node_ids 和 edge_ids，不只在图上加节点。

1. `edge_ids.length == max(node_ids.length - 1, 0)`。第 i 条 edge 的起终点必须分别为 `node_ids[i]` 和 `node_ids[i+1]`，不能混用全图边序号与路径内步号。
2. 循环重复经过同一节点/连线时保留重复项，说明继续/退出条件；不同互斥分支分别建 path，不能首尾拼接为一次执行。
3. 同起终点有多条条件边时明确本路径选择的 edge_id。引用缺失时核实原源码和记录，不用最短路、数组位置或自动连边填空。
4. 检查结果写入响应的 warnings。若提示 paths/edge_ids 的具体错位，在本轮按源码核实并通过原结果写入机制 supersedes 对应 record_id；只修受影响 flow，保留其他有效结果。不能仅因写入成功就忽略 warning，也不能仅为消除 warning 删除分支。证据不足时保留原结论和待确认事项，不启动额外完整分析/自动续接。

这些是 Agent 的分析和自检职责，不新增 Python 语义门禁。写入警告不阻断 action/Run，也不授权宿主替换原路径。

## 业务粒度与跨次可比较性

适用于冻结了本版规则的新 Run。相同模块名不代表输入相同；先遵守当前冻结目标、源码、资料、场景及方法论范围。若 task 提供 flow_stability 输入，按其中的输入指纹理解可比较范围，不能自行扫描历史 Run 寻找旧图。输入相同也不能保证 LLM 自然生成完全相同的结论。

- 节点以一项业务动作、改变后续行为的决策、状态转换、外部终态或恢复动作为单位。普通赋值、helper 调用和局部变量不因实现拆分独立成节点；只有解释业务选择或结果所必需时才展开。可独立说明且有明确业务边界的子过程单列 sub，挂接点使用准确 flow_id/node_id；不能本次按函数、下次任意按业务章节改变粒度。
- 入口、正常完成、正常拒绝、超时/取消及恢复的区别以证据决定。主干可以有多个，不把第一个数组元素或最长路径默认当主干；确无适用主干时明确说明，不猜造。合法参数变化若操作、判据与恢复一致，可共用流程并在用例保留全部变体，不为每个数据值复制同一张图。
- flow_id、节点 id、path_id 采用与业务入口/角色绑定的稳定名称，不使用随机数、时间、Run 编号、当前遍历序号或标题翻译作为身份。flow_id 可按 `repo_id:relative_path:entry_symbol:operation` 命名；同一证据锚点下的多个操作须有明确区分。已有身份只在同一业务对象与依据仍成立时复用，不能强迫新流程与旧错图一致。

新记录提供以下可选稳定元数据（原 behavior-flow-v1 字段保持不变）：

- `layout_version: "stable-v1"`：声明采用稳定身份与显式路径；缺字段或身份尚未核实时仍保留原流程和缺口，不伪称稳定，不授权宿主补造身份。
- `main_path_ids: []`：Agent 明确选择主干 path_id，可有多条；无适用主干填空数组并在 description 解释。每条正常分支是否属于主干由 Agent 依据业务职责决定。
- `nodes[].semantic_key`：由 Agent 根据已读源码填写“repo/path/入口或符号/可定位的源码锚点 + 业务角色”的身份，例如 `web:src/login.c:login_handler:submit|entry`。source_evidence 保留精确行段；标题、行号单独、相似函数名或数组位置不证明语义身份。锚点改变时核实是真实行为变化还是位置移动并说明，宿主不模糊匹配、不自动归并。
- `edges[].edge_id`：为真实连线显式给出稳定 ID；同起点终点的不同条件必须有不同 edge_id，不能按节点对合并。条件含义由 Agent 核实，ID 不以展示顺序命名。
- `paths[].edge_ids`：按实际执行顺序列出准确 edge_id，与 node_ids 的相邻步对应；循环可重复引用节点和边，保留次数/退出条件说明。平行条件边必须明确选中哪一条，不能由客户端猜测。未知时保留缺口，不从最短路、上下游可达集合生成业务路径。

这些字段帮助机械对应与确定性排序/渲染，不证明业务正确或覆盖充分。缺元数据的旧流程继续可读，不追加语义门禁，也不靠宿主重写成稳定版本。

只有当前 task 的 comparison 阶段明确提供 `flow_baseline` 和 `flow_comparison` 时才对照这份流程稳定性基线。普通新建 Analysis 不接收这两个输入；显式增量 Analysis 仍按其冻结职责读取 `incremental_parent_records` 等父记录，并重新核验，不能将此规则误解为禁止增量参考旧流程。采用本版稳定规则的新 Run，其 independent_review 不读取首轮结果、流程基线或增量父记录；旧 Run 保留原冻结输入合同。所有角色均不得自行检索其他 Run。机械 diff 只表示显式身份、字段或拓扑变化；Reviewer 核实新增、删除、拆分、合并、条件与连线变化的依据，并在现有 note/finding 中区分输入变化、纠正旧遗漏、展示变化或待确认。源码未变仍允许有证据的纠错；不为稳定外观隐藏实质变化，不以相似标题宣称等价，不自动把本轮候选替换为已验收基线。

<!-- FLOW-CONTRACT:BEGIN -->
### flow 记录标准样例（behavior-flow-v1，照此结构填写）

以下为虚构的简单登录行为，只演示字段结构，不能作为真实模块的分析深度模板；实际节点、身份、条件及证据须来自当前任务，并按上文展开分支处理闭环。

```json
{
  "format_version": "behavior-flow-v1",
  "flow_id": "web:src/login.c:login_handler:login",
  "layout_version": "stable-v1",
  "main_path_ids": ["login-success"],
  "title": "用户登录",
  "description": "用户提交凭据，经鉴权后建立会话；失败时按原因返回。",
  "role": "main",
  "attach_to": null,
  "nodes": [
    {"id": "submit", "semantic_key": "web:src/login.c:login_handler:submit|entry", "kind": "entry", "label": "提交用户名和密码", "description": "login_handler 接收请求", "source_evidence": ["web:src/login.c:20"]},
    {"id": "verify", "semantic_key": "web:src/login.c:check_password:compare|decision", "kind": "branch", "label": "校验密码", "description": "check_password 比对哈希"},
    {"id": "rejected", "semantic_key": "web:src/login.c:login_handler:reject_credentials|rejection", "kind": "error", "label": "登录被拒绝", "description": "凭据不匹配，不建立本次会话"},
    {"id": "session", "semantic_key": "web:src/login.c:login_handler:create_session|success", "kind": "exit", "label": "建立会话", "description": "写入 session 表并返回 token"}
  ],
  "edges": [
    {"edge_id": "submit-verify", "source_step_key": "submit", "target_step_key": "verify", "condition": ""},
    {"edge_id": "verify-success", "source_step_key": "verify", "target_step_key": "session", "condition": "密码匹配"},
    {"edge_id": "verify-rejected", "source_step_key": "verify", "target_step_key": "rejected", "condition": "密码不匹配"}
  ],
  "paths": [
    {"path_id": "login-success", "title": "正常登录", "condition": "密码正确", "node_ids": ["submit", "verify", "session"], "edge_ids": ["submit-verify", "verify-success"], "explanation": "凭据匹配后建立本次会话", "case_ids": []},
    {"path_id": "login-rejected", "title": "拒绝错误凭据", "condition": "密码不匹配", "node_ids": ["submit", "verify", "rejected"], "edge_ids": ["submit-verify", "verify-rejected"], "explanation": "拒绝本次登录，不创建会话", "case_ids": []}
  ],
  "source_evidence": ["web:src/login.c:20-88"]
}
```

硬性规则：
- 标准字段名与样例一致（英文），用户可见说明使用中文；稳定 ID、semantic_key 与源码锚点保留实际标识。示例 ID 只说明结构，实际生成按上述业务身份命名。
- 一条 flow 记录只放一个流程；有多个流程就写多条 flow 记录。
- edges 的 source_step_key、target_step_key 必须写节点 id，不能写 label；paths.node_ids 同样只引用节点 id。
- body 直接是 JSON 对象：不要写成字符串，不要包 ```json 代码块，不要外套 flow 或 flows；不要用 steps、from/to、node_id、name、节点、连线、路径 等替代字段。
- 节点 kind 只能取 entry | main | branch | error | propagation | recovery | exit。
- role 取 main 或 sub：只有 purpose 指定的 owner 单元的主流程写 main，其他流程一律写 sub。
- attach_to 写子流程挂接到主流程的位置，如 {"flow_id": "F-001", "node_id": "verify"}；不确定时写 null。role/attach_to 只用于排版，缺失时按 sub、未挂接处理。
- 写入时会做结构检查（字段名、id 唯一与引用）；不合格时 pangea_result_write 返回逐条错误，请在本轮按错误修正后重写同一结果。
<!-- FLOW-CONTRACT:END -->
