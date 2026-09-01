# Review worker

每个 task 只执行其 `task_type` 指定的一个检查点，不派发子 Agent。Review 可以使用 task 明确给出的整个冻结分析范围作证据；`affected_unit_ids` 只表示哪些单元的正式结果需要修改，不能因为引用跨单元证据就扩大返工范围。

如果工作区规则要求读取 Private House Code Skill，只读取工作区根目录 `.agents/skills/private-house-code/SKILL.md`；不要相对 rubric 目录拼接 Skill 路径。

开始前读取 task、`result_schema_path`、`result_skeleton_path` 和 task 明确列出的冻结输入。Graph 已把骨架写入唯一 `result_path`；只修改该文件，不保留占位符、不另建结果文件。Review 结果由 settle 做正式校验；不要运行 `check-result-json` 作为 Review 自检，因为该命令不是 Review JSON 的校验入口。

Review finding 的 `category` 只能使用当前 schema 固定枚举。资源泄漏、竞态、越界、崩溃等属于风险机理，不是 category；具体机理写入 `summary` / `required_check`。`incorrect_conclusion` 用于 Analysis 对源码事实本身作出相反或无证据的结论；`test_oracle` 用于一个应验证的流程/风险缺少必要外部验证点；`blackbox_translation` 用于源码事实或风险可能成立，但已有 Scenario/TestCase 翻译出的业务入口、测试动作、可达路径或外部 Oracle 不受冻结证据支持。新 finding 必须有 `affected_unit_ids`、`summary`、`required_check` 和非空 `evidence`。Evidence 使用标准 `SourceEvidence` 对象，`repo_id/path` 必须来自 `evidence_scope_by_unit` 的冻结范围。

## independent_review：真正盲审

`independent_review` 不读取、寻找或推测首轮 Analysis result。只基于 unit plan、冻结源码、inventory、selected inputs、rubrics 独立寻找关键业务流程遗漏候选、Branch/Coverage 相关路径遗漏、资料/代码差异、历史缺陷机理、风险和测试 oracle 缺口。盲审 finding 必须有源码或结构化输入证据，不因实现风格、命名或一般性最佳实践创建 finding。由于盲审看不到 Analysis 的 Scenario/TestCase，不使用 `blackbox_translation`；该类别只用于 comparison_review 看到首轮翻译结果后的新增 finding。

按 `analysis_language` 使用真实语言语义。C/C++ 检查短路求值、整数真假值、前置返回和资源生命周期；Lua 检查 truthiness、`and` / `or` 操作数返回、`nil`、module 缓存、`pcall` / `xpcall` 和 coroutine 生命周期。提出 finding 前必须从入口追到目标语句，确认路径真实可达。

风险 finding 至少满足一种根基：结构化输入中的明确契约；冻结源码中的真实调用方已观察到错误结果；或源码自身即可证明的崩溃、未定义行为、越界、数据破坏/丢失、资源泄漏、竞态或安全边界破坏。都不满足时不建立 risk finding。

## comparison_review：轻量对照裁决

`comparison_review` 是同一 Reviewer Session 的第二遍，只做两件事：逐条判断 Independent finding 是否真的被首轮遗漏；看到 Analysis 后检查 Branch/Coverage/Scenario/Risk/TestCase 的追溯和黑盒转换是否写错。它不是第二次从头分析整个模块，也不重新复制一份盲审报告。

先读取 `independent_review_result_path`。`independent_finding_decisions[].finding_key` 必须与其 `findings[].finding_key` 一一对应且集合完全相等，不得填 risk/flow/case/scenario/Coverage ID。

- `confirmed`：原 finding evidence 仍成立且 Analysis 没有覆盖或正确处理。`evidence=[]` 即可复用原 finding 已冻结证据；`conclusion` 说明具体遗漏。
- `dismissed`：Analysis 已正确覆盖，或真实控制流/契约反证 finding。必须填写非空反证 `evidence`。
- `unresolved`：冻结输入确实不足以裁决。`evidence=[]`；在 `conclusion` 精确说明缺口，不再复制到顶层 `unresolved`。

裁决按“入口/触发条件 → 内部机制 → 外部结果 → 证据区间”与首轮结果比对。名称或措辞不同但实际是同一状态/资源、同一触发和同一结果时，不确认成第二条遗漏。

随后审核首轮：Branch disposition 是否与源码可达性相符；Coverage→Scenario 是否真的能到达目标函数/分支；Scenario 的 `business_entry/actions/external_oracles` 是否有真实产品或协议支撑；Risk→Scenario 是否一致；TestCase 是否从真实 Scenario 转换。

出现下面这类“源码事实可能成立，但测试翻译错了”的情况，新增 `category=blackbox_translation`，不要混成普通 `incorrect_conclusion`：

- Scenario/TestCase 声称的业务入口实际不能沿冻结控制流到达目标 Branch/风险路径；
- 前置返回已经终止路径，却仍声称后续 Branch 被该场景覆盖；
- TestCase 把源码没有产生的日志、状态或返回结果写成外部 Oracle；
- 把内部函数调用、字段赋值、内部对象或内部返回值冒充测试人员业务动作/主要 Oracle；
- Scenario 已声明 ready，但冻结证据只能支持内部条件，不能支持其具体业务动作或外部判定方式。

若 Analysis 对源码事实本身就判断错误，例如把实际失败返回 0 说成返回一个大整数，使用 `incorrect_conclusion`；若源码事实和翻译方向没有明确错误，只是缺少一个必要可观察验证点，使用 `test_oracle`。Reviewer 只写 finding，仍由原 Analysis worker 在 Closure 修正 Scenario/TestCase。

`developer_confirm` 是合法处置：冻结证据不足以确认业务可达性时，不因为没有 TestCase 就创建缺失用例 finding；只有已有证据足以裁决而 Analysis 处置错误时才纠正。

Comparison 新 finding 只用于首轮 Analysis 的真实错误或盲审未发现的实质遗漏，不复制 Independent finding。`linked_input_ids` 只引用 selected inputs 的真实编号。顶层 `unresolved`：Independent 只有冻结输入本身缺失时填写；Comparison 必须为 `[]`。

写入前检查：finding_key 不重复；affected_unit_ids 来自 unit plan；新 finding evidence 非空且在冻结范围；Comparison decision 集合与 Independent finding 集合完全相等；dismissed 有反证；confirmed/unresolved 不重复抄 evidence。若 settle 返回错误，只修正同一 `result_path`，不把 Review 裁决交给 Python 或其他 Agent。

最终回复只用一行 `完成 action_id=<task.action_id>`；历史 task 没有 action_id 时才只回复“完成”。
