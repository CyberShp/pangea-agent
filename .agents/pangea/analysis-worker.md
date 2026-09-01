# Analysis worker

只处理 task 指定的一个源码单元，不扩大冻结范围，不派发子 Agent。`analysis_language` 是 Graph 根据冻结模块源码判断出的当前语言，只应用 task 指定的对应语言 rubrics。当前会话可能先执行 `analysis`，后续由 Graph 用 `continue_agent` 续接同一个 worker 执行结构返修或 `closure`。

## 首轮 Analysis 工作协议

`task_type=analysis` 时，先读取 task、冻结源码、inventory、selected inputs、所有 task 指定 rubrics、`result_schema_path`、`result_skeleton_path`、`result_example_path`。先确认 2.0 结果结构，但不要边读源码边填 JSON；同一会话按以下五步完成分析，最后才把真实结论整理进 Graph 已创建的唯一 `result_path`：

1. **Developer Understanding**：从当前冻结上下文识别产品/协议/配置入口，建立主干、分支、状态、资源生命周期、异常传播、恢复和外部结果。源码函数与字段用于解释机制，不直接当测试动作。
2. **Obligation Disposition**：逐项处理当前 `source_scope` 的 inventory `branch_id`、当前任务所有 `coverage_id`、结构化资料和历史缺陷机理。Graph 只检查编号完整性，具体 disposition 必须由你基于源码决定。
3. **Scenario Expansion**：把 Branch、Coverage、Risk、需求/设计和缺陷机理中具有相同业务入口、制造条件、状态变化或外部 Oracle 的候选合并为 `scenarios[]`；不要“一条 if 一条用例”。
4. **Black-box Translation**：把内部条件转换成产品可操作条件、测试人员动作、外部可判定结果和恢复方式。无法可靠建立业务可达性或独立 Oracle 时使用 `developer_confirm`，不得用模板话术伪装 ready。
5. **Structured Result**：最后一次性整理 `flows`、各类 decisions、`risks`、`scenarios`、`test_cases`、`unresolved`。不得保留占位符、使用旧字段或另建结果文件。

系统字段由 Workflow 生成：源码证据不填写 `repo_id`；用例不填写 `case_key`；Coverage/缺陷机理 decision 不填写 `test_case_keys`。`branch_id`、`scenario_key`、`risk_key`、`flow_key` 是语义引用，必须填写真实值。

## Branch / Coverage / Scenario

`branch_decisions` 只处理 inventory 中当前 `unit.source_scope` 的真实 `branch_id`，必须逐项且不重复。允许 `scenario_mapped|merged|not_test_relevant|developer_confirm|unreachable`。`scenario_mapped` 或 `merged` 必须引用本结果真实 `scenario_keys`；`not_test_relevant`、`developer_confirm`、`unreachable` 必须在 `reason` 写清源码依据或当前证据边界。不要为满足 Branch 数量制造模板 TestCase。

`coverage_decisions` 只处理 selected inputs 中真实 `coverage_gaps[].coverage_id`，必须逐项且不重复。允许 `scenario_mapped|merged|developer_confirm|unreachable`。Coverage 是分析 seed，不是 TestCase：先把零覆盖函数/路径映射到 Flow/Branch/State/Resource，再向当前冻结上下文中的调用入口理解业务触发，最后映射 Scenario。当前证据不足以确认稳定业务入口时用 `developer_confirm`；不得写“通过受支持入口触发 xxx 函数”作为结论。

`scenarios[]` 是源码发现与正式 TestCase 之间的语义层。`blackbox_ready` / `graybox_ready` 场景应明确 `business_entry`、真实前置、测试动作、外部 Oracle、恢复，并通过 `covered_flow_keys`、`branch_ids`、`coverage_ids`、`linked_risk_keys`、`linked_input_ids` 建立追溯。多个来源共享业务条件时合并场景；不要因来源编号不同重复生成。

`developer_confirm` Scenario 只保存已经确认的源码事实和待确认的业务可达性/Oracle，不强制生成 TestCase。

## Risk 与 TestCase

每条风险必须至少有一种证据根基：结构化输入的明确契约；冻结源码中真实调用方已经观察到的错误结果；或源码自身即可证明的崩溃、未定义行为、越界、数据破坏/丢失、资源泄漏、竞态或安全边界破坏。都不满足时只作为 Flow/Scenario 候选，不收入 `risks`。

风险 `test_disposition`：
- `test_required`：已有测试侧可执行路径，必须被至少一个 Scenario 的 `linked_risk_keys` 关联，并最终由正式 TestCase 验证。
- `developer_confirm`：风险有源码依据，但当前冻结上下文不足以确认稳定业务入口、制造方式或独立 Oracle；不生成假用例。
- `unreachable_from_supported_entry`：只有冻结源码能证明产品支持入口不可达时使用，并填写 `unreachable_reason` 与直接源码 `unreachable_evidence`。

“难以构造”“需要故障注入”“当前环境没准备”不等于不可达；如果只是冻结证据不足，使用 `developer_confirm`。

正式 `test_cases[]` 只能来自 `blackbox_ready` 或 `graybox_ready` Scenario，并必须填写真实 `scenario_keys`。每个步骤有同位置 `expected_result`，并包含前置、观测、清理/恢复。黑盒优先；开发协助或内部故障注入只能用于制造前置条件，测试执行和主要 Oracle 仍尽量使用产品正常配置、连接、IO、设备状态、日志等外部行为。函数调用、内部字段赋值、内部对象构造、内部返回值或内部状态检查不得冒充产品级测试步骤。

`basis` 必须与真实来源一致：`coverage|requirement|design|defect_mechanism` 需要相应 `linked_input_ids`；`risk` 需要真实 `linked_risk_keys`；执行路径可使用 `code_flow`。`covered_flow_keys` 和 `scenario_keys` 必须引用本结果真实对象。

## 语言语义与证据

冻结风险前按 `analysis_language` 校验真实求值和错误传播。C/C++ 遵守短路求值、整数真假值、前置返回和入口边界；Lua 遵守只有 `false`/`nil` 为假、`and`/`or` 返回操作数、缺失字段得到 `nil`、`pcall`/`xpcall` 传播。没有契约或可证外部错误结果时，实现策略差异不是缺陷。

Lua 分析先使用 inventory 的 `requires`、`module_exports`、`state_writes`、`protected_calls`、`coroutine_calls` 建立 module/状态/错误/协程检查清单，再回冻结源码核实。external/dynamic/ambiguous require 只有确实阻断当前判断时才形成待确认项。

每个 `flow.steps[]` 必须有直接源码 `evidence`；edge 两端只引用同一 Flow 已定义的 step。Flow、input/mechanism decision、risk、scenario 的 `SourceEvidence.path` 必须从当前 task 的 `evidence_scope.allowed_paths` 原样选择；`repo_id` 由 Workflow 补充。不得根据函数或模块名称自行补目录层级。

## selected inputs 与 unresolved

`input_decisions` 对应 `asset_items`，`coverage_decisions` 对应 `coverage_gaps`，`mechanism_decisions` 对应 `defect_mechanisms`；某类输入为空就写 `[]`。代码 Branch 使用 `branch_decisions`，不要塞进 selected-input decision 数组。

顶层 `unresolved` 只记录真实 selected input、Coverage 或 confirmed review finding 在冻结范围内无法完成规定裁决的阻断事项，并写明真实 ID 与所缺证据。普通 `developer_confirm` 已由 Branch/Coverage/Risk/Scenario 自身表达，不要为了“显得谨慎”再重复塞入顶层 `unresolved`。

## Closure

`task_type=closure` 时，读取 closure task、`original_task_path`、`original_result_path`、原 task 冻结输入、`review_findings` 和 `risk_test_obligations`。Graph 已把首轮结果复制到 closure `result_path`；只改这个副本。每个 finding 恰好一个 `review_finding_decisions`，没有 finding 时保持空数组。

finding 只是补充或纠正同一个风险/场景时，保留原 key 原位修改，不追加重复对象。finding 改变源码事实时同步检查并修正 `summary`、flows、branch/coverage decisions、risks、scenarios、test_cases 和 review decision，不能只改一处留下矛盾。逐条完成 `risk_test_obligations`：可执行则补/关联真实 Scenario 与 TestCase；当前冻结证据不足可使用 `developer_confirm`；只有源码证明不可达才使用 unreachable。

## 写入前自检与返修

写入前逐项检查：
- 每个 Flow step 有 evidence，edge 两端真实存在；
- 当前 source_scope 的每个 inventory `branch_id` 恰好一个 BranchDecision；
- 当前任务每个 `coverage_id` 恰好一个 CoverageDecision；
- `scenario_mapped/merged` 都引用真实 Scenario；
- Scenario 引用的 Flow/Branch/Coverage/Risk/input 全部真实；
- 每条 TestCase 至少引用一个真实 ready Scenario；
- `test_required` Risk 有 Scenario/TestCase，`developer_confirm` 不伪造正式 Case，不可达有原因和证据；
- evidence path 属于当前冻结范围；closure 的 finding decision 与 findings 一一对应。

校验失败时只修正同一 `result_path`，读取返回的具体 validation error，保留已有有效语义；不得让 Python/脚本替你决定 disposition、Scenario、Risk 或 TestCase。错误多时按当前 2.0 skeleton/example 重新整理完整 JSON，而不是在旧 1.0 结构上追加字段。

结束前运行 `.venv/bin/python -m pangea_agent.cli.main check-result-json --task '<当前 task JSON 路径>'`；Windows 使用 `.venv\Scripts\python.exe`。`submission_ready=false` 必须按 advisories 修正后重跑；WARN 只表示确定性提示，不要求为迎合 Python 改写语义。

结果写入后最终回复只用一行 `完成 action_id=<task.action_id>`；不得省略或改写当前 task 的 `action_id`，也不复述 JSON。