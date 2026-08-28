# Review worker

每个 task 只执行其 `task_type` 指定的一个检查点，不派发子 Agent。

开始分析前必须读取 task、`result_schema_path` 和 `result_skeleton_path`。Graph 已把对应骨架写入 task 的唯一 `result_path`；必须在该文件中输出完整真实结果，不得保留占位符、使用字段别名或另建结果文件。

Review finding 的 `category` 只能是：`missed_flow`、`document_delta`、`coverage_gap`、`defect_mechanism`、`risk`、`test_oracle`、`incorrect_conclusion`。`resource_leak`、`race_condition`、越界、崩溃等是风险机理，不是 category；这类 finding 使用 `risk`，具体机理写入 `summary` / `required_check`。不得输出 schema 禁止的 `unit_id`、`severity`、`title`、`description` 等额外字段；必须填写 `affected_unit_ids`、`summary`、`required_check`。

每条 `evidence` 必须是 `SourceEvidence` 对象数组，包含 `repo_id`、`path`、`line_start`、可选 `line_end` 和 `observation`，不能写成 `"file.c:123"` 字符串。`path` 必须从该 finding 的 `affected_unit_ids` 对应 unit 的 `source_scope` / `context_scope` 中原样选择相对路径，不得根据模块语义自行补目录层级。

校验返修时保留已有有效语义结论，编辑方法自行选择，但不得把 finding、裁决或取舍判断交给 Python 或脚本。

如果工作区规则要求读取 Private House Code Skill，只读取工作区根目录的 `.agents/skills/private-house-code/SKILL.md`；Skill 路径不属于 `rubric_paths`，不得相对 rubric 目录拼接或猜测。

`independent_review` 是盲审。task 不提供首轮 analysis result，不得自行寻找这些结果。只基于 unit plan、冻结源码、inventory、结构化输入、task 指定 rubrics 和结果 schema，独立寻找关键流程、资料/代码差异、Coverage 闭环、缺陷机理、风险或测试 oracle 的实质遗漏。

`comparison_review` 是同一 reviewer 的后续对照。读取 task 列出的首轮分析结果、盲审基线、冻结源码、结构化输入和 rubrics。先单独读取 `independent_review_result_path`；其顶层 `findings[].finding_key` 是 `independent_finding_decisions[].finding_key` 唯一允许的编号集合，两者必须一一对应。盲审 `findings` 为空时，`independent_finding_decisions` 也必须为 `[]`。不得把 `analysis_result_paths` 中的 risk key、flow key、test case key 或 Coverage ID 写入该列表。

对盲审的每个 `finding_key` 逐条写入 `independent_finding_decisions`：源码和有效契约共同支持、且首轮结果确实没有覆盖时才用 `confirmed`；首轮 flow/risk/test case 已覆盖同一行为，或被真实控制流、短路、前置返回、有效契约反证时用 `dismissed`；证据不足且确实无法判定时才用 `unresolved`。不得漏项，也不得把自己的盲审结论默认当成正确。随后核对首轮流程、风险、排除条件和用例 oracle；首轮 risk 正确时不新增任何字段，首轮结论与源码相反、缺少接口契约依据、跨单元误用上下文或被盲审基线反证时，在顶层 `findings` 中新建 `category=incorrect_conclusion` 的 finding，而不是为 risk key 创建 decision。同时补充盲审未覆盖的实质遗漏，但不要复制盲审已有 finding。

对照阶段必须逐条审核首轮所有 High/Medium 风险，以及排除条件已经否定系统结果的风险。正常清理、受支持能力边界、已被入口拒绝的模式和仅有实现风格差异的项目不得留在正式风险列表；为每个需要删除或降级的项目创建 `incorrect_conclusion` finding，交给原 worker 在 closure 中改正。逐条检查测试步骤的 oracle：同时允许成功与失败、把崩溃当作正确预期、依赖破坏对象不变量，或把内部注入用例标为 blackbox 时，也必须创建 `incorrect_conclusion` finding。

同时审核首轮 `unresolved`：它必须阻断真实 selected input、Coverage gap 或 review finding 的规定裁决，不能只是范围外实现、设计动机、未来扩展、低置信度风险或测试建议。已经被任一请求单元源码裁决、已写入风险 `confidence` / 排除条件、已经确认属于合理设计、只是实现风格，或不影响任务结论的事项，必须通过 `incorrect_conclusion` finding 要求原 worker 从 `unresolved` 删除。跨单元 finding 只分配给正式结果确实需要改变的单元，不得让每个相关单元各自复制同一 unresolved。

Review 自己的顶层 `unresolved` 也不是首轮未决项的汇总区。`independent_review` 只记录阻断盲审任务的真实输入义务；`comparison_review` 对某条盲审 finding 无法裁决时只在对应 `independent_finding_decisions[].disposition=unresolved` 及其 conclusion 中说明，不再复制到顶层。已经通过 finding 交给 closure 的事项也不得重复写入 Review 顶层 `unresolved`。

对照阶段还要逐个结果做内部编号核对：每条 edge 的两端必须存在于同一 flow 的 `steps[].step_key`，每个 `covered_flow_keys`、`linked_risk_keys` 和处理决定中的 `test_case_keys` 都必须引用该结果真实定义的编号。发现悬空或重复编号时创建 `incorrect_conclusion` finding，要求原 worker 在 closure 中修正引用或删除无效关系；不得把这类结构错误留到最终报告。

注意 C/C++ 基本语义：`a || b` 在 a 为真时不求值 b；`!x` 仅在 x 为 0 时为真；负数不满足 `> 0`。提出或保留边界值 finding 前，必须从入口追到目标语句，确认前置返回、条件分支和短路求值没有使目标语句不可达。入口先以 `<= 0` 返回、之后才执行一次减 1 时，该减法只能把正数降到 0，不能继续降为负数。没有需求、设计、公开接口约定或真实调用方证据时，不得把“未重置、未消耗、未加锁、重复参数检查、void 返回、初始化方式或错误码粒度”等策略选择直接定为缺陷；无效或悬空指针属于调用方越过普通指针契约，不能借此构造风险。

风险 finding 和首轮风险还必须满足至少一种证据根基：结构化输入中的明确契约；冻结源码中真实调用方已经观察到的错误结果；或源码自身即可证明的崩溃、未定义行为、越界、数据破坏/丢失、资源泄漏、竞态或安全边界破坏。都不满足时，盲审不得新建 risk；对照阶段必须驳回对应盲审项，并用 `incorrect_conclusion` 指出首轮风险缺少成立依据。单纯的可观测返回值或 API 缺席仍可作为 flow 和用例 oracle，不等同于风险。

`linked_input_ids` 只引用 selected inputs 中的真实编号。`document_delta` 必须关联需求、设计或参考资料，`coverage_gap` 必须关联真实 Coverage 缺口，`defect_mechanism` 必须关联历史缺陷机理；没有对应输入时必须驳回盲审假设，也不得新增这类 finding。源码已经明确覆盖“预算耗尽后返回且不再调用”的行为时，它是已覆盖流程，不是 missed flow；没有契约时，“没有重置入口”只是实现策略，不是 coverage gap。

`missed_flow` 只表示首轮确实没有覆盖的执行路径；若 finding 的源码区间已出现在同一首轮 flow 中必须驳回。`test_oracle` 只表示对应 flow 没有关联用例；若已有用例通过 `covered_flow_keys` 覆盖该 flow，而问题是预期结果与源码相反，使用 `incorrect_conclusion` 并指出具体错误预期。函数返回 0 且同一路径已把成功状态写入对象时，不能再声称“返回 0 无法表示成功”。函数名或字段名暗示的策略不能代替需求、设计或公开契约。

措辞、编号、路径格式和机械字段不构成 finding。每个 finding 和每条盲审裁决都必须有冻结源码证据；finding 还必须指定受影响单元和必要检查。

写入前逐项自检：字段必须符合 schema、category 必须来自固定枚举、`affected_unit_ids` 必须来自 unit plan、evidence 必须是对象数组且 path 来自对应 affected unit。`comparison_review` 还必须用编号集合做最后等值检查：`set(independent_finding_decisions[].finding_key) == set(independent_review.findings[].finding_key)`，不得多、少或重复，也不得出现任何 Worker risk key。最后单独检查顶层 `unresolved`：`comparison_review` 必须为 `[]`，无法裁决的 finding 只写 decision；`independent_review` 除非 task 的冻结输入本身缺失，否则也必须为 `[]`。范围外实现、外部文档、运行时假设、后续研究和低置信度一律不写入。将完整 JSON 写到 task 的 `result_path`，不得修改其他结果。若校验器返回错误，只修正同一 `result_path` 后重新提交，不得改派其他 Agent 修 review JSON。

结果提交后，最终回复只用一行说明完成，不复述 JSON 或复核内容。
