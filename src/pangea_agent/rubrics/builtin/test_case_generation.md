# 测试用例生成规则

源码分析不是直接填 TestCase。一个单元必须先建立实现语义，再处置 Branch/Coverage，形成 Scenario，最后才生成正式用例。结果 Schema 只是 Graph 通信协议，不是分析步骤。

## Analysis 五 Pass

首轮 Analysis 在同一个 Agent 会话内按下面顺序完成；可以反复回看源码，但不要边读一个 `if` 边立即生成一条用例。

```text
Pass 1  Developer Understanding
        External Entry → Flow → Branch / State / Resource → Error Propagation → External Consequence

Pass 2  Obligation Disposition
        Branch / Coverage / Requirement / Design / Defect Mechanism 逐项裁决

Pass 3  Scenario Expansion
        把具有相同业务入口、触发条件、状态变化或外部结果的来源合并成测试场景

Pass 4  Black-box Translation
        内部机制 → 产品条件 → 测试人员动作 → 外部 Oracle → 恢复

Pass 5  Structured Result
        最后一次性整理 flows / decisions / risks / scenarios / test_cases
```

若当前冻结源码只能证明内部条件，却不能可靠证明如何从产品支持入口制造该条件，不得用模板补齐。Branch/Coverage 使用 `developer_confirm`；Risk 使用 `test_disposition=developer_confirm`；Scenario 可以使用 `readiness=developer_confirm`，且此时不强制生成正式 TestCase。

## 从源码发现转换为测试语义

源码实现细节只用于发现风险和解释原因。生成测试场景或用例前，必须先完成以下转换，不得直接把源码条件改写成测试步骤：

```text
源码条件
→ 对应业务含义
→ 可到达该条件的业务入口
→ 测试人员可构造的业务条件
→ 测试人员可观察的系统结果
→ Scenario
→ TestCase
```

优先使用风险中的 `trigger`、`system_result`、`external_observation`、`exclusion_condition` 和 `test_disposition` 完成转换。

- `blackbox_ready`：从业务入口直接形成场景和用例，测试步骤和预期结果只使用业务操作与外部现象描述，不依赖源码实现细节。
- `graybox_ready`：允许在场景/用例前置条件中说明需要开发协助制造内部条件，但实际测试步骤和主要观测仍从业务入口执行。
- `developer_confirm`：业务可达性或独立 Oracle 尚未确认，不强行生成可执行用例；保留已经确认的源码事实、缺口和所需确认点。

### 公开 API 也是业务入口

“不要直接调用源码函数”只针对实现 helper、私有函数、内部状态机和其他非支持接口，不针对已经由冻结证据确认的公开 API。若公开头文件、需求/设计/任务契约、受支持客户端/测试或其他冻结证据表明某个 C/C++ 函数本身就是稳定公开接口，那么直接调用该 API 就是合法业务入口和测试动作；其公开返回值、输出参数、错误码或对外状态可以作为 Oracle。不要强迫公开 API 必须再向上追到 CLI/RPC/GUI 才算业务入口。

`non-static` 本身不自动证明公开性；私有 `.c` 文件中的 `extern` 声明、跨 `.c` 文件直接调用或可被链接，也只证明 C 链接/调用关系。仍需公开头文件、契约、真实受支持调用方/测试等证据。caller context 已截断且当前入口只有上述链接性证据时，不得把该实现函数直接包装成 ready Scenario；接口支持性、测试侧构造方式或独立 Oracle 缺证据时，应使用 `developer_confirm`。

ready 是逐 Scenario 的正向证明，不是默认值。每个 `blackbox_ready|graybox_ready` Scenario 都要在 `evidence` 中保留证明其 `business_entry` 受支持的源码证据，或在 `linked_input_ids` 中精确关联提供该证明的 Requirement/Design/task contract 等结构化输入，并且至少被一条正式 TestCase 的 `scenario_keys` 直接引用；正式 TestCase 通过该引用继承入口证据。如果只有私有 `.c` wrapper 链或链接性证据，就必须保持 `developer_confirm` 并且不生成正式 TestCase。除非冻结契约明确限定了支持参数域、构造方式或 Oracle，同一私有入口链不能对一个 Branch/输入声称 ready，同时对另一个 Branch/输入又承认入口未知。

## Branch 处置

Inventory 中属于当前 `source_scope` 的每个 `branch_id` 都必须有且只有一个 `branch_decisions[]`。Graph 只检查编号完整性，具体 disposition 由你判断。

允许的处置：

- `scenario_mapped`：该分支形成或加强一个明确 Scenario，填写真实 `scenario_keys`。
- `merged`：该分支已经被其他高信息量 Scenario 一并覆盖，填写被合并到的 `scenario_keys`，不要再造重复用例。
- `not_test_relevant`：当前冻结证据已经足以正向证明该分支不形成独立测试义务；在 `reason` 中说明为什么。它不是“暂时不知道怎么测”的出口。
- `developer_confirm`：源码分支真实存在，但当前冻结上下文不足以建立稳定业务制造方式或独立 Oracle。
- `unreachable`：已根据冻结源码确认当前产品支持入口无法到达；在 `reason` 中记录直接依据。

`scenario_mapped|merged` 只可引用 `blackbox_ready|graybox_ready` Scenario。若唯一可建立的 Scenario 仍是 `developer_confirm`，对应 Branch 也必须是 `developer_confirm`；两者可以双向引用以保留已确认的源码关系。`not_test_relevant|unreachable` 不得残留 Scenario 引用。

只要 `not_test_relevant` 的理由依赖“没看到更上层 caller”“业务入口没确认”“当前上下文不足”“Oracle 不知道”，就应使用 `developer_confirm`。若 source manifest 已记录与当前分析相关的 `caller_context_truncations`，且缺失的上层 caller 会影响该判断，更不能用 `not_test_relevant` 或 `unreachable` 代替证据不足。

“正常防御性分支”“返回设计内错误码”“没有形成缺陷/Risk”都不自动等于 `not_test_relevant`。Branch 是否形成测试义务，要看它是否带来可构造的不同输入/状态或不同外部结果；这与是否建立 Risk 是两项独立判断。

输入校验 Branch 若返回不同错误码、状态或输出，就具有可区分结果，不是纯实现细节。受支持入口已证明时映射/合并真实 Scenario；caller truncation 使入口未确认时使用 `developer_confirm`。不能用只覆盖相反条件的 Scenario 声称当前 Branch 已覆盖；`merged/scenario_mapped` 必须有 Scenario `branch_ids` 反向引用，并由动作实际覆盖对应条件。

BranchDecision 引用的 Flow 必须包含条件节点和每个改变返回、状态或输出的源码可见 successor；`developer_confirm` 不允许省略内部控制流。缺少真实 return/state edge 属于 Flow 遗漏。

caller truncation 不只约束 `not_test_relevant|developer_confirm|unreachable`，也约束乐观的 `scenario_mapped|merged` 和 ready Scenario/TestCase。若所谓业务入口只由私有 `.c` 的声明、跨文件调用或可链接性支撑，而缺失 caller 可能包含真正产品入口，就不能直接声明 ready；冻结证据不足时使用 `developer_confirm`。

私有 `.c` 的 `extern`、non-static 或跨文件调用只可作为内部可达性证据，不得在 Risk trigger/evidence、Scenario business entry 或 Review finding 中改称公开 API、受支持入口或测试人员可直接调用。

一个 Branch 不等于一个 TestCase。多个 Branch 可以汇聚到同一 Scenario；禁止为满足数量而“一条 if 一条模板用例”。

## Coverage 处置

Coverage 只指出需要补覆盖的函数或路径，不等于测试步骤。对于低覆盖或未覆盖函数，必须寻找真实测试入口和业务触发条件，再决定如何处置。**如果目标只是内部实现函数，不得为了补覆盖直接调用它；如果目标本身已经由冻结证据确认是稳定公开 API，则直接调用该公开 API 是合法测试入口，不需要为了形式再向上追一层。**

每个当前任务的 `coverage_id` 必须有且只有一个 `coverage_decisions[]`：

- `scenario_mapped`：已经映射到真实 Scenario。
- `merged`：由已有 Scenario 一并覆盖，不新增重复场景。
- `developer_confirm`：当前冻结上下文不足以确认业务入口、制造条件或独立 Oracle。
- `unreachable`：冻结源码证明从受支持入口不可达。

`source_manifest.coverage_diagnostics.unmatched|ambiguous` 只是外部 Coverage 记录未匹配成功的计数，不是 Coverage gap，也不能派生 coverage_id。只有 `selected_inputs.coverage_gaps[]` 中的真实 `coverage_id` 才能进入 CoverageDecision、Scenario 或 TestCase 链接。

如果 Coverage 目标已经确认是公开 API，且参数/状态可从测试侧构造、公开返回值或外部状态可判定，应优先形成 `scenario_mapped/merged → ready Scenario → TestCase`；不能仅因为测试动作是“调用函数”就使用 `developer_confirm`。

`scenario_mapped` / `merged` 必须填写真实 `scenario_keys`。不得把 Coverage 直接映射成“通过受支持入口触发 xxx 函数”之类的占位用例。

Coverage 的正确链路是：

```text
Coverage Gap
→ Flow / Branch / State / Resource
→ Scenario Candidate
→ 与 Branch / Risk / Requirement / Defect 候选去重或合并
→ Black-box Translation
→ TestCase
```

## Scenario Expansion

`scenarios[]` 是源码发现与正式用例之间的语义层。场景候选至少可以来自：Branch、状态迁移、资源生命周期、边界/容量、并发交错、错误传播、Requirement/Design、Coverage Gap、历史缺陷机理和已确认 Risk。

不要机械地为每个来源创建 Scenario。若多个来源满足相同业务入口和制造条件，并通过同一组外部 Oracle 验证，应合并为一个高信息量 Scenario，并通过 `branch_ids`、`coverage_ids`、`linked_risk_keys`、`linked_input_ids` 建立追溯。

`blackbox_ready` / `graybox_ready` Scenario 应尽量明确：

- `business_entry`：产品公开接口、协议入口、配置入口、已确认的公开 API 或稳定业务动作。
- `preconditions`：测试前可真实准备的业务/环境状态。
- `actions`：测试人员实际执行的业务动作。
- `external_oracles`：不读内部对象也能判定的结果；公开 API 的返回值/输出参数属于接口可观察结果。
- `recovery`：恢复到可继续测试状态的方法。

若这些信息无法可靠建立，使用 `developer_confirm`，不要用“已准备能够到达目标函数的环境”“通过受支持入口触发目标项”等话术伪装成 ready。

## TestCase 生成

正式 `test_cases[]` 只能来自 `blackbox_ready` 或 `graybox_ready` Scenario，并必须填写对应 `scenario_keys`。一个 Scenario 可以生成一个或多个用例；一个用例也可以关联多个共享业务条件的 Scenario，但不得脱离 Scenario 直接由 Branch/Coverage/Risk 生成模板用例。

`test_cases[].linked_input_ids` 只记录该 TestCase 自身通过实际步骤和断言直接覆盖的输入。`scenario_mapped|merged` Coverage 必须至少有一条 TestCase 直接填写真实 `coverage_id`、引用该 decision 的 ready Scenario，并在 `basis` 中包含 `coverage`。共享 Scenario 只表示业务条件相同，不会让其中每条 Case 自动继承 Scenario 的全部 `coverage_ids`；只执行 true 分支的 Case 不得关联 false 分支 Coverage gap，反之亦然。

正式写入前先从每条 Coverage record 还原精确目标，再逐 Case 判断：函数 `count=0` 需要该 Case 实际执行目标函数；分支的每个 `true_count=0|false_count=0` 都需要至少一条 Case 实际执行并判定该指定 outcome。只有亲自命中目标的 Case 才直接链接该 `coverage_id`；同一 record 两侧都为 0 时，两侧 Case 可以直连同一个 ID，但不得把共享 Scenario 的 Coverage ID 当作整组 Case 标签批量复制。

用例必须包含：用例描述、用例类型、前置条件、测试步骤、预期结果、观测方式、清理/恢复。用例不分优先级。

生成用例时先列必要测试变体，再写正文。每个变体固定考虑关联风险、构建类型、运行模式、唯一终态。一条风险同时包含 Debug 崩溃与 Release 状态破坏时，先拆成两个变体，再分别生成 TestCase，不要写完一条混合用例后再修改。若唯一终态是进程或服务崩溃、退出、停止，且该变体还要验证恢复，后续动作的第一步固定为“重启并等待服务恢复”。

## 表达方式

- 生成场景和用例时先把源码或协议触发条件提升为产品正常入口和外部结果；客户端可以调用自己的黑盒用例辅助能力，但它不参与完成状态或流程判断。
- 操作步骤使用测试人员能理解的业务动作描述目标，不要求写具体命令。
- 函数、字段、调用点和行号只作为证据，不替代业务步骤；但已确认的公开 API 本身可以作为业务动作。
- 业务级用例不得把**内部实现函数**调用、实现字段赋值、内部对象构造、内部返回值或内部状态检查作为测试步骤和主要预期。公开 API 调用及其公开返回/输出不属于这里禁止的内部实现。
- 某一步预期进程或服务崩溃、退出或停止后，后续配置、连接或 IO 前必须显式重启并等待服务恢复；故障窗口恢复不能代替进程恢复。
- 需要开发协助的用例，协助只用于制造前置条件；业务执行和结果判断仍尽量使用产品正常提供的配置、连接、IO、设备状态、日志或其他外部可观测行为。手工发送认证报文属于协议级操作，不作为产品级黑盒步骤。
- 畸形 PDU、握手字段、数据段、digest、内部队列或状态机等只能说明开发需要制造什么故障条件，不得成为测试人员的步骤或主要预期。测试人员仍通过正常建链、断链和 IO 操作验证外部结果。
- 每个 `steps[]` 对象必须同时包含一个 `action` 和一个同位置的 `expected_result`。不得把最终结论提前挂到准备步骤，也不得用“见后续合并预期”掩盖缺失。
- 每个用例必须逐项反查 `linked_risk_keys`：业务触发条件、预期异常和观测现象必须能验证所关联风险。正常基线或普通错误密钥场景若不能触发该风险，只能作为同一用例中的排除对照，不能单独冒充风险验证用例。
- 故障注入只负责制造触发条件，不能同时预设或制造要验证的外部后果。例如可以让一次底层操作在产生部分副作用后返回失败，但不能把“返回失败并泄漏资源”整体写成前置条件。
- 验证部分副作用风险时，注入点必须真实产生该部分副作用；只返回错误而不改变状态，不能验证该风险。
- 删除、注销或关闭类系统调用失败时，必须选定一个真实可制造的失败原因，并说明该原因对应的对象或内核状态；泛称“底层操作返回失败”或只让桩返回错误不能作为可执行用例前置条件。
- 错误码隐含对象已不存在、句柄已失效或注册已解除时，故障环境必须真实制造该前置状态。只让桩函数返回 `ENOENT`/`EBADF`，却保留对象或注册，不能据此期待“不再收到事件”等后果。
- 真实制造对象未注册或句柄失效后，不能预期依靠原注册继续收到事件或完成新 IO；后续操作必须先显式重新注册或重建资源。
- 产品已经报告对象、连接或资源释放后，不得继续使用旧指针、旧句柄或已失效连接观测结果。改用新的业务操作、资源计数、日志、管理接口或新连接判断系统状态。
- 每个预期结果必须唯一、可判定，不得写成“A 或 B”。没有安全、稳定的外部观测方式时，Scenario/Risk 使用 `developer_confirm`，不要伪造可执行用例。
- 前置条件限定 Debug 或 Release 构建后，预期只描述该构建下的唯一结果，不得混入另一构建或使用“可能”。
- 同一条 TestCase 只能使用一种构建类型和一种运行模式。Debug 与 Release、epoll 与 kevent 等对照场景必须拆成不同用例，不能在同一用例的后续步骤中切换。
- 部分副作用已经释放引用或资源后，清理步骤不得再次执行同一释放操作；应重建或重启拥有该状态的进程。

## 风险测试处置

- 缺少稳定业务入口、制造方法或独立 Oracle 是证据缺口，不是产品运行时 Risk。不得创建 `system_result` / `external_observation` 只描述“测试无法触发、无法观测、需要开发确认”的 Risk；把对应 Branch/Coverage/Scenario 保持为 `developer_confirm`。
- C/C++ undefined behavior（未定义行为）只说明结果不可依赖，不能在没有冻结构建/运行时契约时写成固定环绕值、`INT_MIN`、返回码、日志或状态。需要受控 sanitizer/构建方式但当前证据不足时保持 `developer_confirm`。
- 未冻结目标 ABI/编译契约时只写类型边界符号（例如 `INT_MAX`），不得擅自把 `int` 固定成 32 位十进制值；ASan 主要检查内存错误，不能单独作为 signed-integer-overflow 的检测 Oracle。使用 UBSan/对应 signed-overflow sanitizer 时必须明确依赖构建选项；未冻结 recover/trap 配置时只能说“可报告”，不能断言必然中止。sanitizer 不是 Risk 排除条件。
- 源码已直接证明且未被正向证明不可达的 C/C++ 未定义行为必须保留 Risk；入口/制造/Oracle 不足时用 `developer_confirm`，不得把它改写成“六维无信号”或从 Risk 集合删除。
- `test_required`：风险已经具备测试侧可执行路径，必须由至少一个 ready Scenario 关联，并最终由正式 TestCase 的 `linked_risk_keys` 覆盖。
- `developer_confirm`：风险本身有源码依据，但当前冻结上下文不足以确认稳定业务入口、制造方法或独立 Oracle；不得强行生成正式 TestCase。
- `developer_confirm` Risk 的 `trigger` 只能写冻结源码已证明的内部条件，并明确尚缺的入口/构造证据；缺少公开头文件、产品契约或受支持客户端/测试时，不得声称“通过受支持入口”或“从公开 API”触发。
- Scenario 只有在自身 actions 含该 Risk trigger、external_oracles 对应该 Risk 的观测方式时才填写 `linked_risk_keys`。developer-confirm Risk 不强制生成 Scenario；无法形成真实动作或稳定 Oracle 时保留 Risk 本身即可，不建立空壳 Scenario。若保留风险 Scenario，它必须独立承载已确认触发和条件性观测，不能挂到只验证其他输入或 Branch 的泛化 Scenario。
- developer-confirm Scenario 必须保存至少一个冻结证据已证明的具体 predicate/trigger，以及对应的源码结果或条件性观测；入口或产品外部 Oracle可以明确待确认，但 actions 与 external_oracles 不能全部只剩占位话术。没有已确认内容就删除 Scenario 并重算 Branch/Risk 引用。若保留关联该 Risk 的 Scenario，关键边界必须出现在 actions 中，不能只藏在 title/preconditions/evidence；按 usual arithmetic conversions 后的真实有符号运算类型使用对应 `TYPE_MAX`，单点边界不能扩写成“其他极大值”“附近值”或更宽输入域；只有运算类型确为 `int` 的 `value + 1` 才使用 `value == INT_MAX`。Scenario 同时填写 `branch_ids` 时，还必须由自身动作与 Oracle 覆盖自己声明的分支 outcome/结果；单个 Scenario 不强制同时覆盖 true/false，否则拆分或移除该引用。
- `unreachable_from_supported_entry`：只有确认无法从当前产品支持的业务入口到达时使用，同时填写 `unreachable_reason` 和直接源码 `unreachable_evidence`。
- “难以构造”“需要故障注入”“暂时缺少环境”本身不等于不可达；如果只是无法在当前冻结证据中确认业务制造方式，使用 `developer_confirm`。
- 分支、边界、正常流程和 Coverage 场景可以不关联风险；不得为了满足风险映射而给它们强加无关风险。
- 不得只生成正常流程。结合源码覆盖异常、分支、初始化、运行、停止、恢复和卸载。
- 参数维度、值域、默认值和代表性组合必须来自源码或当前实现规格；已有测试用例只作为表达和环境参考，不能证明风险已覆盖。
- 低置信度风险仍可形成验证候选，但没有可靠可达性或 Oracle 时保持 `developer_confirm`。
- 顶层 `unresolved` 不重复 Branch/Coverage/Risk/Scenario 已用 `developer_confirm` 表达的同源证据缺口。

## 禁止内容

- 不生成安全专项、SFMEA、代码改进建议或实现质量评价。
- 不因为已有用例名称声称覆盖某风险，不根据测试人员主观分类推断代码分支已覆盖。
- 不把“Schema 字段齐全”视为分析完成；Branch/Coverage 必须逐项有真实处置，Scenario/TestCase 引用必须来自实际分析结论。
