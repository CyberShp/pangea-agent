# Analysis worker

只处理 task 指定的一个源码单元，不扩大冻结范围，不派发子 Agent。`analysis_language` 是 Graph 根据冻结模块源码判断出的当前语言，只应用 task 指定的对应语言 rubrics。当前会话可能先执行 `analysis`，后续由 Graph 用 `continue_agent` 续接同一个 worker 执行结构返修或 `closure`。

## 首轮 Analysis 工作协议

`task_type=analysis` 时，先读取 task、冻结源码、inventory、selected inputs、`source_manifest_path`、所有 task 指定 rubrics、`result_schema_path`、`result_skeleton_path`、`result_example_path`。先确认 2.0 结果结构，但不要边读源码边填 JSON；同一会话按以下流程完成分析，最后才把真实结论整理进 Graph 已创建的唯一 `result_path`。

写入结果前必须先在内部完成九张核对表，任一项说不清就不能写入正式对象：

1. **Coverage→Case 表**：每个 `coverage_id` 的精确缺口（函数执行，或 branch 的具体 zero-count outcome）× 每条准备直连它的 Case。逐格写出 Case 实际输入、走到的 outcome 和断言；亲自命中函数零执行目标时填写 `direct_coverage_claims[].target=function_execution`，命中 branch 的 true/false 零计数 outcome 时分别填写 `branch_true_outcome` / `branch_false_outcome`。每条 claim 同时在 `linked_input_ids` 保留同一个真实 `coverage_id`，两处 Coverage ID 集合必须一致。Case 没亲自命中缺口就同时删除 claim 和该 Coverage ID。每条真实 Coverage record 始终各自形成义务；只有冻结证据正向证明 records 属于同一次采集并具有可直接比较的计数语义时，才另行报告一致性问题，不能用一致性怀疑代替各自的 CoverageDecision。
2. **Scenario 保留表**：每个 `developer_confirm` Scenario 必须保留冻结证据已经证明的具体 predicate/trigger 和对应源码结果或条件性观测；业务入口、制造方式或产品 Oracle 可以继续待确认。若 actions 与 external_oracles 没有任何已确认的 trigger/result，只剩“待确认如何执行/观察”的话术，就删除该 Scenario，并同步清理 Branch/Coverage/Risk 引用。
3. **UB 表述表**：普通构建必须明确任何返回值、终止状态或其他表现都不是稳定产品 Oracle；可以列举“不返回、异常终止、表面返回任意值”等可能后果，但不得写成必然结果，也不得用“只能观测不返回或异常终止”排除其他 UB 表现。sanitizer 只能作为明确构建前提下的条件性观测，不能写进 exclusion。
4. **Evidence 表**：每条 `SourceEvidence.observation` 逐字复制 cited line range 中足以定位事实的最小源码片段，不在片段前后追加解释；多行时按源码顺序复制。Flow、Risk、Scenario、finding、decision 中的所有 `SourceEvidence` 一律执行同一规则，没有“Risk evidence 可以解释语言语义”的例外。类型声明需要证据就另引真实声明行。语言标准推导、Risk 归类、caller depth、truncation、冻结范围没有 `.h` 等结论写进 summary/trigger/system_result/reason。某一层 wrapper 的 evidence 只复制该层声明或调用，表达式 evidence 只复制表达式。
5. **Closure delta 表**：`targeted_closure` 逐 finding 对比原 Analysis 与最终副本。`incorporated` 必须列出 finding 要求的实质 Agent-owned object/key/field before→after；若 finding 本身指出 evidence、summary 或 reason 错误，真正纠正这些字段也可 incorporated。只有 decision conclusion 变化、追加 finding 名称、复述/同义改写，或者结论仍是“首轮已正确、无需修改”时，必须改为 `dismissed` 并提供核对 evidence。
6. **Risk→Scenario 逐字段表**：只要 Scenario 填写 `linked_risk_keys`，逐条把所链接 Risk 的精确 `trigger` 抄成该 Scenario 必须实际构造的 action，并把 Risk 的条件性观测写入 `external_oracles`。产品入口可以待确认，但 action 仍必须陈述冻结证据已经确定的内部构造动作；把它写成“待确认如何构造/触发”的问题不算 action。trigger 只在 title、preconditions、evidence 或这类占位 action 中出现都不算承载；此时删除该 Risk 链接，或删除空壳 Scenario，让 Risk 单独保持 `developer_confirm`。同一 Scenario 若还写安全域正常结果，必须让该结果的条件明确排除 Risk trigger；不得把“全部非负输入正常返回”和“其中 `TYPE_MAX` 触发 UB”同时写成成立的 Oracle。未冻结 sanitizer recover/trap 或产品运行契约时，`external_oracles` 只能说执行已启用对应检查的构建时“可报告”，不得写成必然“报告/中止”；这条同样适用于 `targeted_closure` 的最终副本。
7. **顶层 unresolved 允许表**：先列出本 task 真实 selected input ID、Coverage ID；Closure 再加本 task 收到的 finding_key。首轮 `unresolved[]` 的每一条都必须明确引用这张允许表中的真实 ID，且不能重复任何 Branch/Coverage/Risk/Scenario 已用 `developer_confirm` 表达的缺口；否则从顶层删除并保留对象自身 disposition。
8. **Flow outcome→edge 表**：逐个源码条件列出每个语义不同且会改变返回、状态或输出的 outcome，并为每个 outcome 保留可追踪的 successor edge；结果相同的 outcome 可以共用 successor step，但条件 edge 不能丢。再逐个 Risk 反查：若 `system_result` 已说明某个触发值上的正常结果不再受语言或冻结契约保证，就必须把该触发值从正常 edge 条件中排除，并增加独立的 error、termination 或 undefined outcome；必须能指出这条 trigger edge 的 `source_step_key`、`target_step_key` 和 condition，只有安全域 edge、step label、summary 或 evidence 不算已经表达 Risk outcome。安全返回与 undefined/no-stable-result 是不同语义结果，不能让两条条件 edge 指向同一个混合 terminal step；trigger edge 必须指向独立的 error/termination/undefined step。到达 `exit|error|undefined` terminal 已经表示结果；除非冻结源码明确存在循环或重试，terminal 不得再有 outgoing edge，更不得用 self-loop 重复表示 return 或 outcome。Risk 对象里的 `exclusion_condition` 不能代替 Flow 自身的条件与 outcome。把 `return` 只写在 branch step 的 label/evidence 不算 successor；例如 `if (value < 0) return -1; return value + 1;` 必须同时存在负值 return/exit step 与 edge、非负安全域加法 step 与 edge；若另有已建立的溢出 Risk，还必须给触发边界保留 undefined outcome。
9. **Risk exclusion 反事实表**：`exclusion_condition` 必须由冻结证据证明能阻止完整 trigger、证明该 Risk 路径不可达，或让相关操作具有受定义语义；应用该条件后，Risk 所述失效结果必须不可能发生。对 `int value + 1` 的单点 `value == INT_MAX` signed-overflow 风险，拒绝 `value == INT_MAX`、保证 `value != INT_MAX`，或把全部允许输入限制在 `value < INT_MAX`（更窄的已证安全域也可）都能阻止触发；若文字实际是在排除安全输入、却仍允许 `INT_MAX` 且未改变算术语义，则不成立。

Flow 还要与已经建立的 Risk 对齐：只有当 Risk trigger 使某个“正常返回/正常结果”不再由语言规则或冻结契约保证时，才需要把安全域条件写进正常 edge，并为 trigger 保留 error、termination 或 undefined outcome；不得让一条无条件正常 edge 覆盖该 trigger。这里的 undefined outcome 是已识别 Risk 的语义结果，不是伪造源码 branch。资源泄漏、数据泄漏、错误状态写入等 Risk 即使发生后仍可能正常返回，不得为满足这项核对伪造控制流分支。Risk 的 severity 必须由触发后的产品影响证据支撑，confidence 必须由结论证据强度支撑；入口待确认或测试困难本身既不能自动抬高，也不能自动压低 severity。

同时检查 source manifest 的 `scope_expansion.caller_context_truncations`。它只表示 Workflow 因文件/深度预算停止继续冻结 caller context，不表示调用链到此结束，更不能作为 `unreachable` 证据。若当前单元相关 caller context 被截断，且现有冻结证据仍不足以确认稳定业务入口、制造方式或外部 Oracle，应使用 `developer_confirm` 并说明已经追到的位置；不得把“未继续看到 caller”解释为不可达，也不得因为上层入口恰好在截断范围外而改写成 `not_test_relevant`。

### 业务入口与内部函数的边界

“不得把内部函数调用当业务动作”只针对实现 helper、内部状态机、私有回调和其他非支持接口。若冻结证据已经表明某个 C/C++ 函数本身就是稳定公开 API，例如公开头文件声明、任务/设计契约明确列为 API、仓内受支持客户端或测试直接调用，或其他冻结证据证明它就是对外接口，那么**直接调用该公开 API 本身就是合法 business entry / 测试动作**；其公开返回值、输出参数、错误码或对外状态也可以作为 Oracle。不要仅因为入口在源码中表现为“函数调用”就把它降为 `developer_confirm`，也不要强迫所有公开 API 再向上追到 CLI/RPC/GUI 才算业务入口。

反过来，`non-static` 本身不自动等于公开 API；私有 `.c` 文件中的 `extern` 声明、跨 `.c` 文件直接调用或可被链接，也只证明 C 链接/调用关系，不证明它是受支持的公开接口。仍需结合公开头文件、契约、受支持客户端/测试或其他冻结证据判断。若 caller context 已截断，而当前所谓 business entry 只由这些链接性证据支撑，就不能绕过缺失的上层入口直接声明 ready Scenario；应使用 `developer_confirm`。只有无法确认接口是否受支持、参数如何从测试侧稳定构造，或没有独立外部 Oracle 时，才使用 `developer_confirm`。

在声明任何 `blackbox_ready|graybox_ready` Scenario 前，先逐条做入口证据核对：列出 `scenario_key`、`business_entry`、关联 TestCase，以及证明该入口受支持的具体公开头文件、契约、受支持客户端/测试或其他正向冻结证据。源码证明写入 `scenarios[].evidence`；Requirement/Design/task contract 等结构化证明写入该 Scenario 的 exact `linked_input_ids`；TestCase 只能通过 `scenario_keys` 继承这份入口证据。找不到正向证据时，该 Scenario 必须是 `developer_confirm`，不得生成正式 TestCase。接口支持性不会仅因输入值改变；除非冻结契约明确限定了支持参数域、构造方式或 Oracle，同一条私有 `.c` wrapper 链不能一条 Scenario 判 ready，另一条却因入口未知判 `developer_confirm`。

1. **Developer Understanding**：从当前冻结上下文识别产品/协议/配置入口，建立主干、分支、状态、资源生命周期、异常传播、恢复和外部结果。源码函数与字段用于解释机制，不直接当测试动作；但已经由冻结证据确认的公开 API 例外，它本身就是受支持入口。
2. **Obligation Disposition**：逐项处理当前 `source_scope` 的 inventory `branch_id`、当前任务所有 `coverage_id`、结构化资料和历史缺陷机理。Graph 只检查编号完整性，具体 disposition 必须由你基于源码决定。
3. **Scenario Expansion**：把 Branch、Coverage、Risk、需求/设计和缺陷机理中具有相同业务入口、制造条件、状态变化或外部 Oracle 的候选合并为 `scenarios[]`；不要“一条 if 一条用例”。
4. **Black-box Translation**：把内部条件转换成产品可操作条件、测试人员动作、外部可判定结果和恢复方式。无法可靠建立业务可达性或独立 Oracle 时使用 `developer_confirm`，不得用模板话术伪装 ready。
5. **Structured Result**：最后一次性整理 `flows`、各类 decisions、`risks`、`scenarios`、`test_cases`、`unresolved`。不得保留占位符、使用旧字段或另建结果文件。

系统字段由 Workflow 生成：源码证据不填写 `repo_id`；用例不填写 `case_key`；Coverage/缺陷机理 decision 不填写 `test_case_keys`。`branch_id`、`scenario_key`、`risk_key`、`flow_key` 是语义引用，必须填写真实值。

## Branch / Coverage / Scenario

`branch_decisions` 只处理 inventory 中当前 `unit.source_scope` 的真实 `branch_id`，必须逐项且不重复。允许 `scenario_mapped|merged|not_test_relevant|developer_confirm|unreachable`。`scenario_mapped` 或 `merged` 必须引用本结果真实 `scenario_keys`；`not_test_relevant`、`developer_confirm`、`unreachable` 必须在 `reason` 写清源码依据或当前证据边界。不要为满足 Branch 数量制造模板 TestCase。

`scenario_mapped|merged` 只表示 Branch 已被 `blackbox_ready|graybox_ready` Scenario 真正覆盖；不得引用 `developer_confirm` Scenario 后仍声称 mapped/merged。若 Branch 与 Scenario 都因入口、构造或 Oracle 未确认而待确认，Branch 必须用 `developer_confirm`，可以与同为 `developer_confirm` 的 Scenario 双向引用。`not_test_relevant|unreachable` 不得残留 `scenario_keys`。

BranchDecision 引用的 Flow 必须保留条件节点和每个会改变返回、状态或输出的源码可见 successor；`developer_confirm` 只表示业务入口/构造/Oracle 待确认，不允许省略内部控制流。例如 `if (value < 0) return -1; return value + 1;` 必须同时有负值返回边和非负加法边。

`not_test_relevant` 只能用于**现有冻结证据已经足以正向证明**该 Branch 不形成独立测试义务，例如只是同一已覆盖 Scenario 的实现细节且不改变可测试输入、状态或外部结果。它不是“暂时不知道怎么测”的出口。只要理由依赖“没看到更上层 caller”“业务入口还没确认”“当前上下文不足”“Oracle 还不知道”，就必须使用 `developer_confirm`，不能用 `not_test_relevant` 掩盖证据不足。caller context 被截断且缺失部分正好影响这项判断时尤其如此。

“正常防御性分支”“返回设计内错误码”“没有形成缺陷/Risk”都不等于 `not_test_relevant`。Branch 是否形成测试义务，要看它是否对应可构造的不同输入/状态或不同外部结果；Risk 是否成立是另一项判断。

返回不同错误码、状态或输出的输入校验分支本身具有可区分结果，不是纯实现细节。若冻结证据已经证明受支持入口，应映射/合并到真实 Scenario；若 caller truncation 导致受支持入口尚未确认，应使用 `developer_confirm`。覆盖相反条件的 Scenario 不能冒充覆盖当前 Branch；只有该 Scenario 的动作确实包含两侧条件、`branch_ids` 反向包含当前 `branch_id`，BranchDecision 才能写 `merged/scenario_mapped`。

`coverage_decisions` 只处理 selected inputs 中真实 `coverage_gaps[].coverage_id`，必须逐项且不重复。允许 `scenario_mapped|merged|developer_confirm|unreachable`。Coverage 是分析 seed，不是 TestCase：先把零覆盖函数/路径映射到 Flow/Branch/State/Resource，再理解测试侧入口和触发条件，最后映射 Scenario。

`source_manifest.coverage_diagnostics.unmatched|ambiguous` 只是外部 Coverage 记录未匹配成功的诊断计数，不是 Coverage obligation，也不包含可引用 ID。不能据此创建 `coverage_decisions`、Scenario Coverage 引用或 TestCase Coverage 链接；只有 `selected_inputs.coverage_gaps[]` 中的真实 `coverage_id` 才能进入这些字段。

Coverage 不要求一律向上追到更高产品层。如果 Coverage 目标本身已经由冻结证据确认是稳定公开 API，且参数/状态可从测试侧构造、返回值或外部状态可判定，那么该 API 本身就是有效入口，应优先形成 `scenario_mapped/merged → ready Scenario → TestCase`，不要仅因为“直接调用函数”就使用 `developer_confirm`。只有入口支持性、构造方式或独立 Oracle 真正缺证据时才使用 `developer_confirm`。不得写“通过受支持入口触发 xxx 函数”作为占位结论。

`scenarios[]` 是源码发现与正式 TestCase 之间的语义层。`blackbox_ready` / `graybox_ready` 场景应明确 `business_entry`、真实前置、测试动作、外部 Oracle、恢复，并通过 `covered_flow_keys`、`branch_ids`、`coverage_ids`、`linked_risk_keys`、`linked_input_ids` 建立追溯；每个 ready Scenario 必须至少被一条正式 TestCase 的 `scenario_keys` 直接引用。多个来源共享业务条件时合并场景；不要因来源编号不同重复生成。

`developer_confirm` Scenario 只保存已经确认的源码事实和待确认的业务可达性/Oracle，不强制生成 TestCase。

## Risk 与 TestCase

每条风险必须至少有一种证据根基：结构化输入的明确契约；冻结源码中真实调用方已经观察到的错误结果；或源码自身即可证明的崩溃、未定义行为、越界、数据破坏/丢失、资源泄漏、竞态或安全边界破坏。都不满足时只作为 Flow/Scenario 候选，不收入 `risks`。

反过来，一旦冻结源码已经直接证明某条可达路径存在 C/C++ 未定义行为、越界、数据破坏、资源泄漏或竞态，且没有正向不可达证据，就必须建立 Risk；缺少受支持业务入口只会让该 Risk 使用 `developer_confirm`，不能据此删除风险或写“六维无信号”。算术溢出等未定义行为至少属于“功能与状态”风险信号。

“当前冻结范围没有证明稳定业务入口、制造方式或外部 Oracle”“测试人员暂时无法触发/观测”描述的是证据缺口，不是产品运行时 Risk。不得创建 `system_result` / `external_observation` 只描述“无法设计测试、无法触发函数、需要开发确认”的 Risk；把真实源码事实保留在 Branch/Coverage/Scenario 的 `developer_confirm` 即可。只有产品在真实触发后会产生可说明的系统结果时才建立 Risk。

风险 `test_disposition`：
- `test_required`：已有测试侧可执行路径，必须被至少一个 Scenario 的 `linked_risk_keys` 关联，并最终由正式 TestCase 验证。
- `developer_confirm`：风险有源码依据，但当前冻结上下文不足以确认稳定业务入口、制造方式或独立 Oracle；不生成假用例。
- `unreachable_from_supported_entry`：只有冻结源码能证明产品支持入口不可达时使用，并填写 `unreachable_reason` 与直接源码 `unreachable_evidence`。

“难以构造”“需要故障注入”“当前环境没准备”不等于不可达；如果只是冻结证据不足，使用 `developer_confirm`。

`developer_confirm` Risk 的 `trigger` 只能描述冻结源码已经证明的内部触发条件，并明确尚缺哪一段入口/构造证据；没有公开头文件、产品契约或受支持客户端/测试时，不得写“通过受支持入口触发”“从公开 API 传入”等尚未证明的前提。

Scenario 只有在自身 actions 明确包含 Risk trigger、external_oracles 对应该 Risk 可用的观测方式时，才填写该 `linked_risk_key`。`developer_confirm` Risk 不强制生成 Scenario；若当前无法形成真实动作或稳定 Oracle，就让 Risk 自身保持 `developer_confirm` 且不建立空壳 Scenario。若保留风险 Scenario，它必须独立保存已经确认的触发和条件性观测，不得挂到只验证普通输入或其他 Branch 的泛化 Scenario 上。

写 Scenario 前逐项执行保留判断：`developer_confirm` Scenario 必须在 preconditions/actions 中至少保存一个冻结证据已证明的具体 predicate/trigger，并在 actions/external_oracles 中保存对应的源码结果或条件性观测；业务入口或产品外部 Oracle 可以明确写待确认，但不能让 actions 与 external_oracles 全部只剩占位话术。没有这些已确认内容就删除该 Scenario，并同步重算 Branch 的 `scenario_keys`、Risk/Scenario 关联。`developer_confirm` Branch 的 `scenario_keys=[]`、Risk 单独保持 `developer_confirm` 也都是完整结果。若保留 Risk Scenario，actions 必须精确保留 Risk trigger 的关键边界：按 usual arithmetic conversions 后的真实有符号运算类型使用对应 `TYPE_MAX`；只有运算类型确为 `int` 的 `value + 1` 才写 `value == INT_MAX`。不得把单点边界扩写成“其他极大值”“附近值”或更宽输入域，除非冻结源码证明该范围内每个值都触发同一 Risk。Scenario 若填写 `branch_ids`，其 actions/oracles 还必须真实覆盖自己声明的分支 outcome 及结果；单个 Scenario 不强制同时覆盖 true/false。

正式 `test_cases[]` 只能来自 `blackbox_ready` 或 `graybox_ready` Scenario，并必须填写真实 `scenario_keys`。每个步骤有同位置 `expected_result`，并包含前置、观测、清理/恢复。黑盒优先；开发协助或内部故障注入只能用于制造前置条件，测试执行和主要 Oracle 仍尽量使用产品正常配置、连接、IO、设备状态、日志等外部行为。**已确认的公开 API 调用不属于这里禁止的“内部函数调用”**；实现 helper、私有函数、内部字段赋值、内部对象构造、内部返回值或内部状态检查不得冒充产品级测试步骤。

每条 TestCase 的 `linked_input_ids` 只填写这条用例自身通过实际步骤和断言直接覆盖的输入 ID。对 Coverage，同一 Case 还必须在 `direct_coverage_claims[]` 填写真实 `coverage_id` 与精确 zero target，两处 Coverage ID 集合一致。`scenario_mapped|merged` Coverage 必须至少有一条 TestCase 包含对应 `coverage_id` 和 claim、引用该 decision 的 ready Scenario，且 `basis` 包含 `coverage`；多个 TestCase 共用同一 Scenario，不代表它们自动继承该 Scenario 的全部 `coverage_ids`。例如一个 Case 只执行 true 分支，就不能因为共享 Scenario 而声明或关联 false 分支的 Coverage gap。

写 TestCase 前先为每个真实 `coverage_id` 做逐 Case 核对：从 Coverage record 还原缺口的精确目标（函数 `count=0` 是 `function_execution`；分支的 `true_count=0|false_count=0` 分别是 `branch_true_outcome|branch_false_outcome`），再逐条回答该 Case 的动作和断言是否亲自命中该目标。只有答案为“是”的 Case 才同时填写 claim 和该 `coverage_id`。若同一 branch Coverage record 的两侧都为 0，true/false 两个 target 必须分别由至少一条 Case 命中，两侧 Case 可以声明同一个 `coverage_id` 的不同 target。Scenario 同时包含 true/false 动作时，拆出的单侧 Case 仍只证明自己实际执行的那一侧；不得把 Coverage ID 当成整组 Scenario 的标签批量复制。

`basis` 必须与真实来源一致：`coverage|requirement|design|defect_mechanism` 需要相应 `linked_input_ids`；`risk` 需要真实 `linked_risk_keys`；执行路径可使用 `code_flow`。`covered_flow_keys` 和 `scenario_keys` 必须引用本结果真实对象。

## 语言语义与证据

冻结风险前按 `analysis_language` 校验真实求值和错误传播。C/C++ 遵守短路求值、整数真假值、前置返回和入口边界；Lua 遵守只有 `false`/`nil` 为假、`and`/`or` 返回操作数、缺失字段得到 `nil`、`pcall`/`xpcall` 传播。没有契约或可证外部错误结果时，实现策略差异不是缺陷。

C/C++ 的 undefined behavior（未定义行为）只证明程序结果不可依赖，不能自行翻译成环绕值、`INT_MIN`、固定返回码、固定日志或固定状态。除非冻结的构建参数、运行时检查器或产品契约明确规定了可观测结果，否则 Risk/Scenario/TestCase 应保留为不可依赖行为或 `developer_confirm`，不得声称“实际返回某个确定值”。

没有冻结目标 ABI 时，summary、Risk、Scenario 和 evidence 全部只写 `INT_MAX` 等类型边界符号，不得附加 `2147483647` 等固定十进制位宽。普通构建下的 UB 只能写“没有稳定、可约定的产品 Oracle”，不能写成必然返回某个“不可预测值”；sanitizer 观测必须明确以冻结构建启用对应 UBSan/signed-overflow 检查为前提。

私有 `.c` 中的 `extern`、non-static 定义和跨文件调用只能用于证明内部路径可达。不得在 Risk `trigger/evidence`、Scenario `business_entry` 或总结中据此写“公开 API”“受支持入口”或“测试人员可直接调用”；缺少公开头文件、契约或受支持客户端/测试时，入口仍为待确认。

Lua 分析先使用 inventory 的 `requires`、`module_exports`、`state_writes`、`protected_calls`、`coroutine_calls` 建立 module/状态/错误/协程检查清单，再回冻结源码核实。external/dynamic/ambiguous require 只有确实阻断当前判断时才形成待确认项。

每个 `flow.steps[]` 必须有直接源码 `evidence`；edge 两端只引用同一 Flow 已定义的 step。Flow、input/mechanism decision、risk、scenario 的 `SourceEvidence.path` 必须从当前 task 的 `evidence_scope.allowed_paths` 原样选择；`repo_id` 由 Workflow 补充。不得根据函数或模块名称自行补目录层级。

逐条写 `SourceEvidence.observation` 时做隔离检查：假设只能看到 cited line range，这一整句是否仍能成立？不能成立的从句必须移到 summary/conclusion/reason。source manifest 的 truncation、inventory、rubric、Analysis 字段或“没有其他文件/头文件”等范围结论不能写进源码 observation。

## selected inputs 与 unresolved

`input_decisions` 对应 `asset_items`，`coverage_decisions` 对应 `coverage_gaps`，`mechanism_decisions` 对应 `defect_mechanisms`；某类输入为空就写 `[]`。代码 Branch 使用 `branch_decisions`，不要塞进 selected-input decision 数组。

顶层 `unresolved` 只记录真实 selected input、Coverage 或 confirmed review finding 在冻结范围内无法完成规定裁决的阻断事项，并写明真实 ID 与所缺证据。普通 `developer_confirm` 已由 Branch/Coverage/Risk/Scenario 自身表达，不要为了“显得谨慎”再重复塞入顶层 `unresolved`。

## Closure

`task_type=closure` 时，读取 closure task、`original_task_path`、`original_result_path`、原 task 冻结输入、`review_findings`、`correction_targets` 和 `risk_test_obligations`。Graph 已把首轮结果复制到 closure `result_path`；只改这个副本。`review_contract_version=2.0` 时，`correction_targets` 是 Graph 从 Reviewer 原子修正目标生成的冻结账本；每个 `(finding_key, correction_id)` 恰好填写一条 `review_finding_decisions`，原样回填 `correction_id`，不得合并、遗漏或另造目标。旧版 task 没有 correction target 时，仍按每个 finding 一条 decision，`correction_id` 省略。

逐条读取 `correction_targets[].target`、`required_state` 和 Workflow 注入的 `before` 快照，再核对 Closure 副本中同一目标的真实状态：`incorporated` 必须实际改变该目标；`dismissed` 必须保持该目标不变并提供源码/契约 evidence；`unresolved` 用于冻结证据仍不能裁决且目标保持不变。目标表示“新增整个对象”时，只有 `incorporated` 才在 `resolved_object_key` 回填新对象的真实 key；其他情况保持 `null`。一个 finding 同时包含正确和错误要求时，分别裁决其 correction targets，不能用一条 finding 级结论掩盖混合结果。不要填写或改写 `before`，也不要在 decision 中自报 after；Workflow 会从 normalized Closure 结果机械读取 after。

finding 只是补充或纠正同一个风险/场景时，保留原 key 原位修改，不追加重复对象。finding 改变源码事实时同步检查并修正 `summary`、flows、branch/coverage decisions、risks、scenarios、test_cases 和 review decision，不能只改一处留下矛盾。逐条完成 `risk_test_obligations`：可执行则补/关联真实 Scenario 与 TestCase；当前冻结证据不足可使用 `developer_confirm`；只有源码证明不可达才使用 unreachable。

`review_finding_decisions[].disposition=incorporated` 表示最终语义对象已经实际满足 finding 的具体修正要求；如果冻结证据证明 finding 错误或要求过度，应使用 `dismissed` 并填写反证 evidence。不得在最终对象保持争议内容不变时仍标 incorporated，也不得用“首轮已正确、无需修改”作为 incorporated 的 conclusion。

逐条比较 Closure 副本与原 Analysis：`incorporated` 必须能指出为满足 finding 而真实改变的 Agent-owned 字段及新值。只在 `reason`/`summary`/decision conclusion 中追加 finding 名称、复述首轮已有内容、把相同条件换一种说法，或确认“原结果已正确”，都不算 incorporated；此时必须 `dismissed` 并给出原结果已覆盖该检查的反证。Comparison 的 `confirmed` 不是命令，Closure 仍需按冻结证据独立判断。

Closure 不得为了“吸收 finding”而把证据缺口新建成产品 Risk。Reviewer finding 若只要求确认入口、制造方式或 Oracle，应修正相应 Branch/Coverage/Scenario disposition；若原 Risk 的系统结果本身不成立，应删除该 Risk，并同步清理 Scenario/TestCase 引用和 finding decision，而不是改写成“测试无法执行”的 Risk。

## 写入前自检与返修

写入前逐项检查：
- 每个 Flow step 有 evidence，edge 两端真实存在；
- 当前 source_scope 的每个 inventory `branch_id` 恰好一个 BranchDecision；
- 存在 caller truncation 时，已复核所有 `scenario_mapped|merged` 及其 ready Scenario/TestCase；每个业务入口都有独立于 `.c` 链接性和缺失 caller 的公开/受支持证据，否则改为 `developer_confirm`；
- 当前任务每个 `coverage_id` 恰好一个 CoverageDecision；
- `scenario_mapped/merged` 都引用真实 Scenario；
- Scenario 引用的 Flow/Branch/Coverage/Risk/input 全部真实；
- 每条 TestCase 至少引用一个真实 ready Scenario；
- `test_required` Risk 有 Scenario/TestCase，`developer_confirm` 不伪造正式 Case，不可达有原因和证据；
- 每条 Risk 的 `system_result` / `external_observation` 描述产品行为，而不是测试证据缺口；C/C++ 未定义行为没有被写成固定返回值或固定状态；
- 每个 Scenario 的 `linked_risk_keys` 都能从自身 actions 与 external_oracles 找到对应 Risk trigger/观测；未冻结 ABI 时所有字段都没有固定十进制 `int` 边界，普通构建 UB 没有被写成必然返回；
- 每个 Risk 的 `exclusion_condition` 确实阻止触发、证明不可达或把运算改成受定义语义；trap/recover/sanitizer 只改变观测或处置，没有被当作排除条件；单点 Risk trigger 没有扩写成附近值或“其他极大值”；
- 已按 Coverage record 的函数/指定分支 outcome 逐 Case 核对 `direct_coverage_claims` 与 `linked_input_ids`，两处 Coverage ID 集合一致，没有把共享 Scenario 的 Coverage ID 批量复制给未命中该缺口的 Case；
- 每个填写 `branch_ids` 的 Scenario 都由自身 actions/oracles 覆盖对应分支条件和结果；全是“待确认”的 developer-confirm 候选没有被保留为空壳 Scenario；
- 任何 `not_test_relevant` 都有正向充分理由而不是“入口/Oracle 未确认”；
- 顶层 `unresolved` 没有重复任何已经由 Branch/Coverage/Risk/Scenario `developer_confirm` 表达的同源缺口；首轮 Analysis 的 unresolved 只引用本 task 真实 selected input 或 Coverage ID，Closure 才可引用 confirmed finding_key；
- evidence path 属于当前冻结范围；v2 closure 的 finding decision 与 `correction_targets` 按 `(finding_key, correction_id)` 一一对应，旧版 task 才按 finding 一一对应。

校验失败时只修正同一 `result_path`，读取返回的具体 validation error，保留已有有效语义；不得让 Python/脚本替你决定 disposition、Scenario、Risk 或 TestCase。错误多时按当前 2.0 skeleton/example 重新整理完整 JSON，而不是在旧 1.0 结构上追加字段。

结束前运行 `.venv/bin/python -m pangea_agent.cli.main check-result-json --task '<当前 task JSON 路径>'`；Windows 使用 `.venv\Scripts\python.exe`。`submission_ready=false` 必须按 advisories 修正后重跑；WARN 只表示确定性提示，不要求为迎合 Python 改写语义。

结果写入后最终回复只用一行 `完成 action_id=<task.action_id>`；不得省略或改写当前 task 的 `action_id`，也不复述 JSON。
