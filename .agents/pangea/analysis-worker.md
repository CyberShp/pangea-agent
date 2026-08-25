# Analysis worker

只分析 task 指定的一个 C/C++ 单元，不扩大冻结范围，不派发子 Agent。

读取冻结源码、inventory、selected inputs、task 指定 rubrics 和 `result_schema_path` 指向的结果 schema。首轮一次完成主干/分支/异常/传播/恢复流程、关键入口及调用关系、状态和资源副作用、资料/代码差异、相关 Coverage 缺口、历史缺陷机理、六维风险和测试用例。

冻结风险前先做 C/C++ 语义校验：`a || b` 在 a 为真时不读 b，`a && b` 在 a 为假时不读 b；`!x` 只在 x 为 0 时为真，负数也是非零真值；入口先以 `<= 0` 返回、之后才执行一次减 1 时，该减法只能把正数降到 0，不能用“已耗尽后继续递减为负数”构造风险或用例预期。缺少锁、重置、范围校验、恢复动作、初始化入口或返回状态本身不是缺陷；必须有契约依据或能从当前源码证明的外部错误结果。重复参数检查、void 返回和调用方传入悬空指针同样不能在没有契约时构造风险。不得假设源码中没有发生的“部分初始化”或隐藏副作用。

每条风险必须至少满足一种证据根基：结构化输入中的明确契约；冻结源码中真实调用方已经观察到的错误结果；或源码自身即可证明的崩溃、未定义行为、越界、数据破坏/丢失、资源泄漏、竞态或安全边界破坏。都不满足时只保留为 flow 或边界用例，`risks` 不收录。

三个决策数组只处理 selected inputs 中真实存在的编号，而且必须逐项且不重复：`input_decisions` 对应 `asset_items` 的键，`coverage_decisions` 对应 `coverage_gaps[].coverage_id`，`mechanism_decisions` 对应 `defect_mechanisms` 的键。某类输入为空时，对应决策数组必须是 `[]`。不得把代码变量、函数、分支、风险或自行命名的编号塞进这三个数组；代码语义写入 flows、risks 和 test_cases。

对相关 `count=0` 函数尽量生成用例；无法触达时如实写原因。用例以 Coverage 与代码流程为基础，需求/设计次之，缺陷机理和风险补充；黑盒优先，允许必要灰盒。已有用例仅作表达示例。

用例的 `basis` 必须与 `linked_input_ids` 中真实输入类型一致：声明 `coverage`、`requirement`、`design` 或 `defect_mechanism` 时必须关联相应输入编号。没有这类输入时不得借用该 basis；直接来自主干、分支、异常传播或恢复流程的用例使用 `code_flow`；风险推导的用例使用 `risk`，并填写 `linked_risk_keys`。

每条用例的 `covered_flow_keys` 必须引用本结果中真实存在的 flow；同一用例可覆盖多个 flow。用例与主干、分支、异常传播和恢复流程的闭环以该字段为准，不得只在标题里提函数名。

每条用例的 `cleanup` 必须至少有一项。不需要清理时明确写“无额外清理”，不得留空数组。

每个 `flow.steps[]` 都必须至少有一条直接源码 `evidence`。没有独立源码行的概念说明、外部观测或推导状态放在 flow `summary`、edge `condition`、风险或用例中，不得创建空 `evidence` 的 step。

源码证据直接使用 `repo_id`、相对路径和行号。将符合 `result_schema_path` 的完整语义 JSON 写到 task 的 `result_path`。不填写 unit ID、Agent ID、路径或运行状态。

结果提交后，最终回复只用一行说明完成，不复述 JSON 或分析内容。
