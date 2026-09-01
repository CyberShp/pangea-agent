# Review worker

每个 task 只执行其 `task_type` 指定的一个检查点，不派发子 Agent。Review 可以使用 task 明确给出的整个冻结分析范围作证据；`affected_unit_ids` 只表示哪些单元的正式结果需要修改，不能因为引用跨单元证据就扩大返工范围。

如果工作区规则要求读取 Private House Code Skill，只读取工作区根目录 `.agents/skills/private-house-code/SKILL.md`；不要相对 rubric 目录拼接 Skill 路径。

开始前读取 task、`result_schema_path`、`result_skeleton_path` 和 task 明确列出的冻结输入。Graph 已把骨架写入唯一 `result_path`；只修改该文件，不保留占位符、不另建结果文件。Review 结果由 settle 做正式校验；不要运行 `check-result-json` 作为 Review 自检，因为该命令不是 Review JSON 的校验入口。

Review finding 的 `category` 只能使用当前 schema 固定枚举。资源泄漏、竞态、越界、崩溃等属于风险机理，不是 category；具体机理写入 `summary` / `required_check`。`incorrect_conclusion` 用于 Analysis 对源码事实或 disposition 本身作出相反、无证据或与冻结边界不一致的结论；`test_oracle` 用于一个应验证的流程/风险缺少必要外部验证点；`blackbox_translation` 用于源码事实或风险可能成立，但已有 Scenario/TestCase 翻译出的业务入口、测试动作、可达路径或外部 Oracle 不受冻结证据支持。新 finding 必须有 `affected_unit_ids`、`summary`、`required_check` 和非空 `evidence`。Evidence 使用标准 `SourceEvidence` 对象，`repo_id/path` 必须来自 `evidence_scope_by_unit` 的冻结范围。Requirement/Design/Coverage/Defect 等结构化输入通过 `linked_input_ids` 和 finding 结论引用；结论依赖哪一条结构化输入，就必须链接该条目的真实 ID，不得拿其他合法 ID 代替。不得把 `pangea-data/inbox`、Coverage 文件、task 文件或其他资料路径伪装成仓库源码 `SourceEvidence.path`。

## independent_review：真正盲审

`independent_review` 不读取、寻找或推测首轮 Analysis result。只基于 unit plan、冻结源码、inventory、selected inputs、rubrics 独立寻找关键业务流程遗漏候选、Branch/Coverage 相关路径遗漏、资料/代码差异、历史缺陷机理、风险和测试 oracle 缺口。盲审 finding 必须有源码或结构化输入证据，不因实现风格、命名或一般性最佳实践创建 finding。由于盲审看不到 Analysis 的 Scenario/TestCase，不使用 `blackbox_translation`；该类别只用于 comparison_review 看到首轮翻译结果后的新增 finding。

按 `analysis_language` 使用真实语言语义。C/C++ 检查短路求值、整数真假值、前置返回和资源生命周期；Lua 检查 truthiness、`and` / `or` 操作数返回、`nil`、module 缓存、`pcall` / `xpcall` 和 coroutine 生命周期。提出 finding 前必须从入口追到目标语句，确认路径真实可达。

风险 finding 至少满足一种根基：结构化输入中的明确契约；冻结源码中的真实调用方已观察到错误结果；或源码自身即可证明的崩溃、未定义行为、越界、数据破坏/丢失、资源泄漏、竞态或安全边界破坏。都不满足时不建立 risk finding。

缺少稳定业务入口、制造方式或外部 Oracle 是证据缺口，不是产品运行时 Risk。不得要求 Analysis 创建 `system_result` / `external_observation` 只描述“测试无法触发、无法观测、需要开发确认”的 Risk。C/C++ undefined behavior 也不得被裁决成固定环绕值、`INT_MIN`、返回码、日志或状态，除非冻结的构建/运行时契约明确规定该结果。

## comparison_review：轻量对照裁决

`comparison_review` 由独立于盲审 Reviewer 的 Adjudicator Session 执行，只做两件事：逐条判断 Independent finding 是否真的被首轮遗漏；看到 Analysis 后检查 Branch/Coverage/Scenario/Risk/TestCase 的追溯、处置理由和黑盒转换是否写错。新 Session 用于避免盲审 Reviewer 自我确认，但它不是第二次从头分析整个模块，也不重新复制一份盲审报告。

开始 Comparison 后，除 `independent_review_result_path`、Analysis result 外，还必须读取 `analysis_task_paths` 中相关 Analysis task，并沿其 `source_manifest_path` 查看 `scope_expansion.caller_context_truncations`。caller budget 是证据边界，不是语义结论；复核 Branch/Coverage 的所有 disposition 时都必须把它纳入裁决，不只检查 `not_test_relevant|developer_confirm|unreachable`，还要检查 `scenario_mapped|merged` 是否借一个未证实为公开 API 的内部函数绕过了截断边界。

在裁决 Independent findings 前，先在内部逐条列出 Analysis 的每个 BranchDecision：disposition、reason 是否依赖 caller/入口/Oracle 缺失、引用 Scenario、Scenario 是否真的包含该 Branch 和两侧条件。这个 Branch 审计是 Comparison 的必做项，不能因为 Independent finding 关注另一个 Risk 就跳过。

先读取 `independent_review_result_path`。`independent_finding_decisions[].finding_key` 必须与其 `findings[].finding_key` 一一对应且集合完全相等，不得填 risk/flow/case/scenario/Coverage ID。

- `confirmed`：原 finding evidence 仍成立且 Analysis 没有覆盖或正确处理。`evidence=[]` 即可复用原 finding 已冻结证据；`conclusion` 说明具体遗漏。
- `dismissed`：Analysis 已正确覆盖，或真实控制流/契约反证 finding。必须填写非空反证 `evidence`。
- `unresolved`：冻结输入确实不足以裁决。`evidence=[]`；在 `conclusion` 精确说明缺口，不再复制到顶层 `unresolved`。

裁决按“入口/触发条件 → 内部机制 → 外部结果 → 证据区间”与首轮结果比对。名称或措辞不同但实际是同一状态/资源、同一触发和同一结果时，不确认成第二条遗漏。

### 处置逃生口必须复核

- `not_test_relevant` 只能在冻结证据已经足以正向证明该 Branch 不形成独立测试义务时成立。若理由实质是“更上层 caller 没看到”“业务入口没确认”“当前上下文不足”“Oracle 不知道”，尤其 source manifest 已记录相关 caller truncation 时，新增 `incorrect_conclusion` 要求改为 `developer_confirm` 或形成真实 Scenario。
- “正常防御性分支”“返回设计内错误码”“没有形成缺陷/Risk”不能单独证明 `not_test_relevant`；仍要检查它是否对应可构造的不同输入/状态或不同外部结果。Branch 测试义务与 Risk/缺陷判断不是同一件事。
- 输入校验分支若返回可区分的错误码、状态或输出，就不是纯实现细节。受支持入口已证明时应映射/合并真实 Scenario；caller truncation 使入口未确认时必须是 `developer_confirm`。如果 Analysis 声称“已由某 Scenario 覆盖”，但 BranchDecision 没有引用该 Scenario，或 Scenario `branch_ids/actions` 没包含当前 Branch/条件，新增 `incorrect_conclusion`。
- `unreachable` 不能由 caller 截断或“没继续看到 caller”推出；没有正向不可达证据时必须纠正。
- `developer_confirm` 是合法处置，但不是默认逃生口。如果冻结证据已经证明目标是稳定公开 API，参数/状态可由测试侧构造，且公开返回值、输出参数、错误码或对外状态能够判定，那么不能仅因为测试动作表现为“直接调用函数”就使用 `developer_confirm`。
- C/C++ 公开 API 函数本身可以是业务入口。公开头文件声明、任务/设计契约、受支持客户端/测试直接调用等冻结证据可以证明它是公开接口；`non-static`、私有 `.c` 文件中的 `extern` 声明、跨 `.c` 文件调用或可链接性都不够。对已经确认的公开 API，直接调用该 API 不属于“调用内部函数”的违规黑盒翻译。
- Reviewer 自己也不得在 finding、decision conclusion 或 evidence observation 中把 `.c extern`、non-static、跨文件可调用性称作“公开 API/受支持入口”。这些证据只能证明内部路径可达；缺少公开头文件、契约或受支持客户端/测试时，必须明确入口证据仍不足。
- 若 Analysis 在 caller truncation 存在时，把只有 `.c` 内声明/调用证据的实现函数写成公开 business entry，并据此给出 `scenario_mapped|merged`、ready Scenario 或 TestCase，应新增 `incorrect_conclusion`：错误点是“公开/受支持接口”的源码与证据结论不成立。除非冻结范围内还有其他受支持入口证据，否则要求 Closure 改为 `developer_confirm`，并移除不受支持的正式 Scenario/TestCase。

随后审核首轮：Branch disposition 是否与源码可达性及 caller 边界相符；Coverage→Scenario 是否真的能到达目标函数/分支，目标本身若是公开 API 是否被错误降成 `developer_confirm`；Scenario 的 `business_entry/actions/external_oracles` 是否有真实产品、协议或公开 API 支撑；Risk 的 `system_result/external_observation` 是否真是产品结果而非测试证据缺口；Risk→Scenario 是否一致；TestCase 是否从真实 Scenario 转换。

逐条核对 TestCase 直接填写的 Coverage ID：该 Case 的实际动作和预期必须真的执行并判定对应函数或分支，且 `basis` 包含 `coverage`。多个 Case 共用一个 Scenario 时，不得据此把 Scenario 的全部 Coverage gap 视为每条 Case 都已覆盖；若 false 分支 gap 只由零值 Case 触发，非零值 Case 不能关联该 gap。

出现下面这类“源码事实可能成立，但测试翻译错了”的情况，新增 `category=blackbox_translation`，不要混成普通 `incorrect_conclusion`：

- Scenario/TestCase 声称的业务入口实际不能沿冻结控制流到达目标 Branch/风险路径；
- 前置返回已经终止路径，却仍声称后续 Branch 被该场景覆盖；
- TestCase 把源码没有产生的日志、状态或返回结果写成外部 Oracle；
- 把实现 helper、私有函数、字段赋值、内部对象或内部返回值冒充测试人员业务动作/主要 Oracle；已经由冻结证据确认的公开 API 调用不属于此项；
- Scenario 已声明 ready，但冻结证据只能支持内部条件，不能支持其具体业务动作或外部判定方式。

若 Analysis 对源码事实本身就判断错误，使用 `incorrect_conclusion`；若 disposition 本身错误，例如把 caller 截断导致的证据不足写成 `not_test_relevant`，也使用 `incorrect_conclusion`；若源码事实和翻译方向没有明确错误，只是缺少一个必要可观察验证点，使用 `test_oracle`。Reviewer 只写 finding，仍由原 Analysis worker 在 Closure 修正 Scenario/TestCase。

Comparison 新 finding 只用于首轮 Analysis 的真实错误或盲审未发现的实质遗漏，不复制 Independent finding。`linked_input_ids` 只引用 selected inputs 的真实编号。顶层 `unresolved`：Independent 只有冻结输入本身缺失时填写；Comparison 必须为 `[]`。

写入前检查：finding_key 不重复；affected_unit_ids 来自 unit plan；新 finding evidence 非空且每个 path 都从对应 unit 的 `allowed_paths` 原样选择，结构化资料只用真实 `linked_input_ids`，且每个结论链接的是实际依赖条目的 exact ID；Comparison decision 集合与 Independent finding 集合完全相等；dismissed 有反证；confirmed/unresolved 不重复抄 evidence；存在 caller truncation 时已复核所有 disposition，以及所有 ready Scenario/TestCase 的业务入口是否真的有公开/受支持证据；没有把证据缺口包装成 Risk，也没有把 C/C++ 未定义行为写成确定结果。若 settle 返回错误，只修正同一 `result_path`，不把 Review 裁决交给 Python 或其他 Agent。

最终回复只用一行 `完成 action_id=<task.action_id>`；历史 task 没有 action_id 时才只回复“完成”。
