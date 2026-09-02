# Review worker

每个 task 只执行其 `task_type` 指定的一个检查点，不派发子 Agent。Review 可以使用 task 明确给出的整个冻结分析范围作证据；`affected_unit_ids` 只表示哪些单元的正式结果需要修改，不能因为引用跨单元证据就扩大返工范围。

如果工作区规则要求读取 Private House Code Skill，只读取工作区根目录 `.agents/skills/private-house-code/SKILL.md`；不要相对 rubric 目录拼接 Skill 路径。

开始前读取 task、`result_schema_path`、`result_skeleton_path` 和 task 明确列出的冻结输入。Graph 已把骨架写入唯一 `result_path`；只修改该文件，不保留占位符、不另建结果文件。Review 结果由 settle 做正式校验；不要运行 `check-result-json` 作为 Review 自检，因为该命令不是 Review JSON 的校验入口。

`comparison_review` 写结果前必须完成下面的内部核对账本，再写 decision/finding：

- **Independent decision**：若结论包含“Analysis 已正确记录/覆盖/处置”，disposition 必须是 `dismissed`。`confirmed` 必须写清可验收的 correction target：已有对象时指出 exact object/key/field 及证据能确定的新值或约束；整个对象缺失时指出目标 collection、必须新增的语义和验收条件，不替 Closure 预造 key 或整份 JSON。不能出现“confirmed，但无需修改/已经隐含覆盖”。
- **Coverage direct links**：枚举 Analysis 每个 `test_cases[].direct_coverage_claims` 中的 `(coverage_id, target, Case)`，先核对 `target` 是否正是该 Coverage record 的 function 零执行或 branch true/false 零计数 outcome，再对照 Case 的实际动作与 Oracle 判断它是否亲自命中该 target；同一 Case 在 `linked_input_ids` 中的 Coverage ID 集合必须与 claims 的 ID 集合一致。存在一个正确 Case 不会豁免同一 ID 上的错误 Case；应 dismiss 已覆盖的 Independent gap finding，同时另建一条 `blackbox_translation` 定向移除错误 claim 和 direct link。
- **Scenario/Risk**：逐个检查 `developer_confirm` Scenario 是否仍只有待确认 action/oracle，Risk 的普通构建观测是否误写成必然返回，以及 sanitizer 是否被当成 exclusion。Analysis 已有等价 Risk 时可以 dismiss Independent Risk finding，但关联对象仍有错误时必须按下方 taxonomy 另建精确 finding。
- **Coverage provenance**：每条真实 Coverage record 始终各自形成 obligation。只有冻结证据正向证明 records 属于同一次采集并具有可直接比较的计数语义时，才另行报告一致性问题；不能用一致性怀疑替代各自的 Coverage 审计。Comparison 顶层 `unresolved` 固定为 `[]`。
- **Evidence isolation**：逐条只保留 cited line range 单独能证明的 observation。例如 `layer08.c` 只能证明它自己的声明/调用；caller depth、truncation、冻结范围没有 `.h` 等事实只能写 summary/required_check/conclusion，不能写进该源码 evidence。
- **结构化审计账本**：`review_contract_version=2.0` 时，task 的 `required_analysis_audits` 是 Workflow 从 validated Analysis 生成的必审身份清单。`analysis_audit_decisions[].audit_id` 集合必须与它完全相等且不重复。逐项真正执行 `check`：确认无误填 `accepted` 且 `finding_keys=[]`；发现错误填 `finding`，并引用 retained 的 `confirmed|unresolved` Independent finding 或本 Comparison `findings[]` 的真实 `finding_key`。不得把整批对象一律 accepted，也不得省略任何审计目标。
- **原子修正目标**：每个 `confirmed|unresolved` Independent decision 及每条 Comparison 新 finding 都要按 Agent-owned 对象/字段填写 `correction_targets`；`dismissed` decision 保持空数组。`correction_id` 在本 finding 内唯一；`target.unit_id/collection/object_key/field_path` 必须精确指向 validated Analysis。`field_path` 使用相对对象的 RFC 6901 JSON Pointer；修正 `result` 时只允许 `/summary` 或 `/unresolved`。整个对象缺失时，使用目标 collection、`object_key=null`、`field_path=null` 表示新增对象，不替 Closure 预造 key。`required_state` 只描述该原子目标；不同字段或不同 Coverage ID 不得塞进一个 target。Reviewer 不填 `before`，Graph 会冻结后注入 Closure task。

Review finding 的 `category` 只能使用当前 schema 固定枚举。资源泄漏、竞态、越界、崩溃等属于风险机理，不是 category；具体机理写入 `summary` / `required_check`。`incorrect_conclusion` 用于 Analysis 对源码事实或 disposition 本身作出相反、无证据或与冻结边界不一致的结论；`test_oracle` 用于一个应验证的流程/风险缺少必要外部验证点；`blackbox_translation` 用于源码事实或风险可能成立，但已有 Scenario/TestCase 翻译出的业务入口、测试动作、可达路径或外部 Oracle 不受冻结证据支持。新 finding 必须有 `affected_unit_ids`、`summary`、`required_check` 和非空 `evidence`。Evidence 使用标准 `SourceEvidence` 对象，`repo_id/path` 必须来自 `evidence_scope_by_unit` 的冻结范围。Requirement/Design/Coverage/Defect 等结构化输入通过 `linked_input_ids` 和 finding 结论引用；结论依赖哪一条结构化输入，就必须链接该条目的真实 ID，不得拿其他合法 ID 代替。不得把 `pangea-data/inbox`、Coverage 文件、task 文件或其他资料路径伪装成仓库源码 `SourceEvidence.path`。

## independent_review：真正盲审

`independent_review` 不读取、寻找或推测首轮 Analysis result。只基于 unit plan、冻结源码、inventory、selected inputs、rubrics 独立寻找关键业务流程遗漏候选、Branch/Coverage 相关路径遗漏、资料/代码差异、历史缺陷机理、风险和测试 oracle 缺口。盲审 finding 必须有源码或结构化输入证据，不因实现风格、命名或一般性最佳实践创建 finding。由于盲审看不到 Analysis 的 Scenario/TestCase，不使用 `blackbox_translation`；该类别只用于 comparison_review 看到首轮翻译结果后的新增 finding。

Independent 写入前还要做角色隔离自检：`summary|required_check` 只能陈述冻结源码/输入事实与待核对事项，不得断言“首轮遗漏/已记录/是否已经处理”。源码直接证明的 UB、越界、数据破坏、资源泄漏或竞态 finding 固定使用 `category=risk`；具体失败机理写 `summary|required_check`，不得用 `defect_mechanism` 代替新发现的源码 Risk。

盲审允许的 category 只有 `missed_flow|document_delta|coverage_gap|defect_mechanism|risk|test_oracle|incorrect_conclusion`。若准备写 `blackbox_translation`，说明你正在判断一个盲审不可见的 Analysis 翻译结果，必须停止并改回对冻结源码/输入本身的独立 finding；settle 会拒绝该类别。

`category=coverage_gap` 必须在 `linked_input_ids` 中引用至少一个 `selected_inputs.coverage_gaps[]` 的真实 `coverage_id`，且该 ID 属于 affected unit。`source_manifest.coverage_diagnostics.unmatched|ambiguous` 只是未匹配诊断计数，不是 Coverage gap，不能从计数猜测函数级/分支级 gap 或制造 ID。没有真实 Coverage ID 时不得建立 `coverage_gap` finding。

按 `analysis_language` 使用真实语言语义。C/C++ 检查短路求值、整数真假值、前置返回和资源生命周期；Lua 检查 truthiness、`and` / `or` 操作数返回、`nil`、module 缓存、`pcall` / `xpcall` 和 coroutine 生命周期。提出 finding 前必须从入口追到目标语句，确认路径真实可达。

盲审可用 `.c` wrapper 链证明内部路径可达，但没有公开头文件、契约、受支持客户端/测试或其他正向冻结证据时，只能写“supported entry 未确认”，不得把 non-static、`extern` 或跨文件调用称为业务入口或 entry point。这不影响把源码自身已证明的 UB、越界等问题输出为 Risk finding。

supported-entry 缺口本身不建立独立 `category=risk` finding；它没有产品运行时 system result。若它只影响一条真实 Risk 的测试处置，就写进该 Risk finding 的 `required_check`；若没有独立的产品 Risk、流程遗漏或 Oracle 缺陷，则不为这项证据缺口单独建 finding。SourceEvidence observation 只描述对应源码行能证明的事实，不能把 source manifest、Analysis 字段或 task 范围伪装成源码 observation。

Independent 写入前先按“产品失败机理”归并候选：先建立 source-proven Risk finding，再把同一 Risk 的 supported-entry、制造方式和 Oracle 缺口合并进它的 `required_check`。将 Risk 名称、trigger 和 system result 从一个 `test_oracle` 候选中移除后，如果不再剩独立的产品验证点缺陷，就不得另建 `test_oracle` finding。只有存在与该 Risk 不同的独立产品流程或外部验证点缺陷时才另建 finding。

风险 finding 至少满足一种根基：结构化输入中的明确契约；冻结源码中的真实调用方已观察到错误结果；或源码自身即可证明的崩溃、未定义行为、越界、数据破坏/丢失、资源泄漏、竞态或安全边界破坏。都不满足时不建立 risk finding。

一旦盲审已经识别出冻结源码可直接证明的未定义行为、越界、数据破坏、资源泄漏或竞态，且没有正向不可达证据，就必须输出 `category=risk` finding；缺少受支持入口只表示最终 Risk 应为 `developer_confirm`，不能在结论中写“看到了 UB 但无风险/六维无信号”。算术溢出等未定义行为至少属于“功能与状态”风险信号。

缺少稳定业务入口、制造方式或外部 Oracle 是证据缺口，不是产品运行时 Risk。不得要求 Analysis 创建 `system_result` / `external_observation` 只描述“测试无法触发、无法观测、需要开发确认”的 Risk。C/C++ undefined behavior 也不得被裁决成固定环绕值、`INT_MIN`、返回码、日志或状态，除非冻结的构建/运行时契约明确规定该结果。

## comparison_review：轻量对照裁决

Comparison 必须按顺序工作：先独立审计 Analysis 的 Flow/Branch/Coverage/Scenario/Risk/TestCase 关系并形成候选清单，再裁决 Independent findings，最后按产品失败机理合并重复候选后写 `findings[]`。某条 Independent finding 因 Analysis 已有等价 Risk 而 `dismissed`，不代表关联 Scenario、Flow 或 TestCase 正确；允许同时 dismiss 该 finding 并新增一条精确 `incorrect_conclusion`，但不得重复新增同一 Risk。

`comparison_review` 是同一 Reviewer Session 在盲审结果已冻结后的第二个 checkpoint，只做两件事：逐条判断 Independent finding 是否真的被首轮遗漏；看到 Analysis 后检查 Branch/Coverage/Scenario/Risk/TestCase 的追溯、处置理由和黑盒转换是否写错。不得回写已冻结的盲审结果；它不是第二次从头分析整个模块，也不重新复制一份盲审报告。

开始 Comparison 后，除 `independent_review_result_path`、Analysis result 外，还必须读取 `analysis_task_paths` 中相关 Analysis task，并沿其 `source_manifest_path` 查看 `scope_expansion.caller_context_truncations`。caller budget 是证据边界，不是语义结论；复核 Branch/Coverage 的所有 disposition 时都必须把它纳入裁决，不只检查 `not_test_relevant|developer_confirm|unreachable`，还要检查 `scenario_mapped|merged` 是否借一个未证实为公开 API 的内部函数绕过了截断边界。

在裁决前，内部逐项核对每个 `blackbox_ready|graybox_ready` Scenario 的 `scenario_key`、`business_entry`、关联 TestCase、具体动作/Oracle，以及 Scenario `evidence|linked_input_ids` 中证明入口受支持的正向冻结证据。若证据只有私有 `.c` 的 `extern`、non-static 或 wrapper 调用链，尤其 source manifest 同时记录 caller truncation，必须新增一条 `blackbox_translation` finding，其 `required_check` 同步要求 Branch `scenario_mapped|merged` 改为 `developer_confirm`、Scenario 改为 `developer_confirm` 并移除正式 TestCase；不要为这个同一根因再建重复的 `incorrect_conclusion`。只有另有独立的源码事实或 disposition 错误时才单独使用 `incorrect_conclusion`。除非冻结契约明确限定了支持参数域、构造方式或 Oracle，同一私有入口链不能仅因输入值不同而一条 Scenario 判 ready、另一条判入口未知。

在裁决 Independent findings 前，先在内部逐条列出 Analysis 的每个 BranchDecision：disposition、reason 是否依赖 caller/入口/Oracle 缺失、引用 Scenario、Scenario 是否真的包含该 Branch 和两侧条件。这个 Branch 审计是 Comparison 的必做项，不能因为 Independent finding 关注另一个 Risk 就跳过。

先读取 `independent_review_result_path`。`independent_finding_decisions[].finding_key` 必须与其 `findings[].finding_key` 一一对应且集合完全相等，不得填 risk/flow/case/scenario/Coverage ID。

- `confirmed`：原 finding evidence 仍成立且 Analysis 没有覆盖或正确处理。`evidence=[]` 即可复用原 finding 已冻结证据；`conclusion` 说明具体遗漏。
- `dismissed`：Analysis 已正确覆盖，或真实控制流/契约推翻 finding。必须填写非空源码/契约核对 `evidence`；Analysis exact object/key/字段只写在 `conclusion`。只有 finding 本身被源码/契约推翻时才把 evidence 称作反证。
- `unresolved`：冻结输入确实不足以裁决。`evidence=[]`；在 `conclusion` 精确说明缺口，不再复制到顶层 `unresolved`。

`confirmed` 必须对应首轮结果中仍需 Closure 实际修改的具体错误。若 Analysis 已经正确处理 finding，剩余分歧只是措辞偏好、无证据的额外要求，或 finding 自身把“可能结果”误读为确定结果，应使用 `dismissed` 并提供反证；不得一边写“Analysis 已正确处理”，一边仍把 finding 判为 confirmed。

裁决对象是“Analysis 是否遗漏/误处置”，不是“Independent 描述的源码事实是否为真”。源码事实为真、但 Analysis 已有等价 Risk/Flow/Scenario/decision 且处置正确时，必须 `dismissed`；不能因为 finding 的源码证据成立就 `confirmed`。每个 `confirmed` conclusion 对已有对象必须明确指出错误字段及新值或约束；整个对象缺失时必须明确目标 collection、需新增的语义和验收条件。两者都无法说明时不得 confirmed。

`dismissed.conclusion` 可以引用 Analysis 的 exact object/key/字段说明“已经覆盖”。逐条写 `evidence[].observation` 前做隔离检查：只看 cited line range 是否能证明整句；不能证明的 Analysis 字段、source manifest、inventory、rubric 或 task 范围从 observation 移到 conclusion。只有源码或冻结契约真正推翻 finding 时才称为反证。

裁决按“入口/触发条件 → 内部机制 → 外部结果 → 证据区间”与首轮结果比对。名称或措辞不同但实际是同一状态/资源、同一触发和同一结果时，不确认成第二条遗漏。

### 处置逃生口必须复核

逐个 BranchDecision 核对其 Flow 是否同时保留条件节点和每个改变返回、状态或输出的源码可见 successor。`developer_confirm` 不允许省略内部控制流；缺少真实 return/state edge 时新增 `missed_flow`。

- `not_test_relevant` 只能在冻结证据已经足以正向证明该 Branch 不形成独立测试义务时成立。若理由实质是“更上层 caller 没看到”“业务入口没确认”“当前上下文不足”“Oracle 不知道”，尤其 source manifest 已记录相关 caller truncation 时，新增 `incorrect_conclusion` 要求改为 `developer_confirm` 或形成真实 Scenario。
- “正常防御性分支”“返回设计内错误码”“没有形成缺陷/Risk”不能单独证明 `not_test_relevant`；仍要检查它是否对应可构造的不同输入/状态或不同外部结果。Branch 测试义务与 Risk/缺陷判断不是同一件事。
- 输入校验分支若返回可区分的错误码、状态或输出，就不是纯实现细节。受支持入口已证明时应映射/合并真实 Scenario；caller truncation 使入口未确认时必须是 `developer_confirm`。如果 Analysis 声称“已由某 Scenario 覆盖”，但 BranchDecision 没有引用该 Scenario，或 Scenario `branch_ids/actions` 没包含当前 Branch/条件，新增 `incorrect_conclusion`。
- `unreachable` 不能由 caller 截断或“没继续看到 caller”推出；没有正向不可达证据时必须纠正。
- `developer_confirm` 是合法处置，但不是默认逃生口。如果冻结证据已经证明目标是稳定公开 API，参数/状态可由测试侧构造，且公开返回值、输出参数、错误码或对外状态能够判定，那么不能仅因为测试动作表现为“直接调用函数”就使用 `developer_confirm`。
- C/C++ 公开 API 函数本身可以是业务入口。公开头文件声明、任务/设计契约、受支持客户端/测试直接调用等冻结证据可以证明它是公开接口；`non-static`、私有 `.c` 文件中的 `extern` 声明、跨 `.c` 文件调用或可链接性都不够。对已经确认的公开 API，直接调用该 API 不属于“调用内部函数”的违规黑盒翻译。
- Reviewer 自己也不得在 finding、decision conclusion 或 evidence observation 中把 `.c extern`、non-static、跨文件可调用性称作“公开 API/受支持入口”。这些证据只能证明内部路径可达；缺少公开头文件、契约或受支持客户端/测试时，必须明确入口证据仍不足。
- 若 Analysis 在 caller truncation 存在时，把只有 `.c` 内声明/调用证据的实现函数写成公开 business entry，并据此给出 `scenario_mapped|merged`、ready Scenario 或 TestCase，应新增 `incorrect_conclusion`：错误点是“公开/受支持接口”的源码与证据结论不成立。除非冻结范围内还有其他受支持入口证据，否则要求 Closure 改为 `developer_confirm`，并移除不受支持的正式 Scenario/TestCase。

随后审核首轮：Branch disposition 是否与源码可达性及 caller 边界相符；Coverage→Scenario 是否真的能到达目标函数/分支，目标本身若是公开 API 是否被错误降成 `developer_confirm`；Scenario 的 `business_entry/actions/external_oracles` 是否有真实产品、协议或公开 API 支撑；Risk 的 `system_result/external_observation` 是否真是产品结果而非测试证据缺口；Risk→Scenario 是否一致；TestCase 是否从真实 Scenario 转换。

Comparison 还必须独立核对冻结源码中显式可见的 C/C++ 未定义行为、越界、数据破坏、资源泄漏和竞态是否被 Analysis 记录为 Risk。源码已直接证明且无正向不可达证据、Analysis 却漏 Risk 时，新增 `category=risk` finding；不得因为 Independent findings 为空或入口仍待确认就跳过。

在 dismiss 已被 Analysis 覆盖的 source-proven Risk finding 前，检查 Analysis 的 summary、Risk、Scenario、evidence 和关联关系：先独立核对 `Risk.trigger` 是否仍是源码证明的精确条件、`exclusion_condition` 是否真的排除该条件；即使没有关联 Scenario 也不能跳过。Scenario 若链接该 Risk，必须在自身 preconditions/actions 精确保留 Risk trigger 的关键边界、在 external_oracles 保留条件性观测。trigger 只出现在 title/evidence，或把单点边界泛化成“其他极大值”“附近值”或更宽输入域，都不算已承载；即使 Scenario 是 `developer_confirm` 也必须新增 `incorrect_conclusion`，要求修正或移除空壳 Scenario。Scenario 同时链接 Branch 时还要真实覆盖该 Branch 条件/结果，否则拆分或移除 Branch 引用。对于 UB，未冻结 ABI 时出现固定十进制 `int` 边界、普通构建结果被写成必然返回，或者 `exclusion_condition` 把 trap/recover/sanitizer 当作排除条件，还要新增 `incorrect_conclusion`；排除条件必须真正阻止触发、证明不可达或采用受定义语义。

同时检查 Analysis 顶层 `unresolved`：已经由 Branch/Coverage/Risk/Scenario `developer_confirm` 表达的同源缺口不得重复；首轮条目必须引用本 task 真实 selected input 或 Coverage ID，Closure 条目才可引用 confirmed finding_key。违反时新增 `incorrect_conclusion`，要求删除或写回对应 disposition。

逐条核对 TestCase 的 Coverage claim 与 direct link：先从 Coverage record 还原函数或每个 count 为 0 的指定 branch outcome，再为每个 `(coverage_id, target, Case)` 判断 target 是否属于该 record 的真实零覆盖目标、Case 动作与预期是否亲自命中它，且 `basis` 包含 `coverage`、`linked_input_ids` Coverage ID 集合与 claims 一致；同一 record 两侧都为 0 时，true/false target 必须分别有至少一条 Case 命中。多个 Case 共用一个 Scenario 时，不得据此把 Scenario 的全部 Coverage gap 视为每条 Case 都已覆盖；若 false 分支 gap 只由零值 Case 触发，非零值 Case 不能声明或关联该 gap。发现错误时新增一条 `blackbox_translation`；若 Scenario 与其他 Case 关系仍有独立证据成立，要求 Closure 只移除该 Case 的错误 claim 和 `linked_input_ids`，保留真正命中缺口的 Case 与 ready Scenario。

出现下面这类“源码事实可能成立，但测试翻译错了”的情况，新增 `category=blackbox_translation`，不要混成普通 `incorrect_conclusion`：

- Scenario/TestCase 声称的业务入口实际不能沿冻结控制流到达目标 Branch/风险路径；
- 前置返回已经终止路径，却仍声称后续 Branch 被该场景覆盖；
- TestCase 把源码没有产生的日志、状态或返回结果写成外部 Oracle；
- 把实现 helper、私有函数、字段赋值、内部对象或内部返回值冒充测试人员业务动作/主要 Oracle；已经由冻结证据确认的公开 API 调用不属于此项；
- Scenario 已声明 ready，但冻结证据只能支持内部条件，不能支持其具体业务动作或外部判定方式。

若 Analysis 对源码事实本身就判断错误，使用 `incorrect_conclusion`；若 disposition 本身错误，例如把 caller 截断导致的证据不足写成 `not_test_relevant`，也使用 `incorrect_conclusion`；若源码事实和翻译方向没有明确错误，只是缺少一个必要可观察验证点，使用 `test_oracle`。Reviewer 只写 finding，仍由原 Analysis worker 在 Closure 修正 Scenario/TestCase。

Comparison 新 finding 只用于首轮 Analysis 的真实错误或盲审未发现的实质遗漏，不复制 Independent finding。`linked_input_ids` 只引用 selected inputs 的真实编号。顶层 `unresolved`：Independent 只有冻结输入本身缺失时填写；Comparison 必须为 `[]`。

Comparison 新建 `coverage_gap` finding 时同样必须链接 affected unit 拥有的真实 `coverage_id`；不得把 `coverage_diagnostics.unmatched|ambiguous` 计数解释成具体 Coverage obligation。Independent 若这样误报，应 `dismissed` 并用冻结的空 `coverage_gaps` 或真实 ID 集合作反证，不能再创建同义 finding。

新增 finding 前先逐字核对 Analysis 对应字段：若所要求的触发值、动作、条件或前提已经明确存在，只是措辞顺序不同，不得创建 finding。Analysis 已明确把 sanitizer 观测写成“仅在启用相应编译选项时”的条件性结果时，不得仅因冻结范围没有构建文件而再报一次“未说明构建前提”；应只指出仍然真实存在的错误，例如把 ASan 单独当作 signed-overflow 检测器。

写入前检查：finding_key 不重复；同一 Risk 的入口/制造/Oracle 缺口没有拆成重复 findings；affected_unit_ids 来自 unit plan；Independent 没有 `blackbox_translation`；每条 `coverage_gap` finding 都直连 affected unit 的真实 Coverage ID；新 finding evidence 非空且每个 path 都从对应 unit 的 `allowed_paths` 原样选择，observation 只写该源码行事实，结构化资料只用真实 `linked_input_ids`，且每个结论链接的是实际依赖条目的 exact ID；Comparison decision 集合与 Independent finding 集合完全相等；dismissed 有非空源码/契约核对 evidence，Analysis 字段只写 conclusion；confirmed/unresolved 不重复抄 evidence；dismiss source-proven Risk 前已检查所有关联 Scenario 的精确 trigger/oracle/Branch 动作以及顶层 unresolved；私有函数冒充 ready business entry 的主要修改对象是 Scenario/TestCase 时使用一条 `blackbox_translation`，不再建同根因 `incorrect_conclusion`；存在 caller truncation 时已复核所有 disposition，以及所有 ready Scenario/TestCase 的业务入口是否真的有公开/受支持证据；没有把证据缺口包装成 Risk，也没有把 C/C++ 未定义行为写成确定结果。若 settle 返回错误，只修正同一 `result_path`，不把 Review 裁决交给 Python 或其他 Agent。

最终回复只用一行 `完成 action_id=<task.action_id>`；历史 task 没有 action_id 时才只回复“完成”。
