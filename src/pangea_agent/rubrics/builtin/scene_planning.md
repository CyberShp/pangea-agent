# 四场景规划职责

仅适用于behavior-test-v2。先读analysis_scene，再按目标、入口和生命周期划分主责；冻结范围是可读边界，不是全目录交付义务。
沿用当前语言规划规则，不在Planning穷举状态机、风险、分支或用例。
用既有purpose简述每个单元职责，并指定一个已有单元负责对象主干结构化业务流程及跨单元关系；不新增全局流程Agent或等待屏障。
module保持综合范围；risk按下方“风险场景规划”；branch围绕业务路径；coverage按真实缺口选择重点及必要调用链，先理解对象主干，不做全范围风险扫描。
按需读取coverage_match_summary、资料和methodology_catalog。选择真实方法论ID写入methodology_ids，说明选择理由；没有相关方法论不编造ID。不能仅按名称、行数、文件数划分，也不能全量展开清单。
methodology_ids是本单元选择的补充方法论；Analysis读取该单元所选的内置/用户方法论，Reviewer读取各单元选择的合集。选择应服务当前场景，不因选择通用方法论扩大branch/coverage的正式风险责任；空选择应说明无相关方法论的依据，不自动补入全套DFX。
覆盖数据没有新缺口时仍安排对象流程理解与数据范围说明，不制造覆盖用例；无数据、版本未知和不匹配不得解释为100%覆盖。

## 风险场景规划（risk-method-v1）

仅当 analysis_scene.method_version=risk-method-v1 时适用。
- 先读 planning_metadata 的 risk_signal_overview 和 risk_signal_summary 输入。信号只是线索，用来定位资源、锁和取消状态、引用计数、外部输入解析、定长缓冲区集中的文件。
- 按“契约闭包”划分单元：同一资源的申请与全部释放点、同一配置或数据的生产与消费、同一共享对象的全部访问者、同一解析器与其输入来源，尽量放在同一单元的 owned 范围。必须分开时，把另一侧写进 context_files 或 context_regions，并在 purpose 中写明契约关系。
- 每个单元填写 risk_lenses，取值 error_path、resource_lifecycle、boundary、concurrency、untrusted_input、contract；按信号和职责选择，不确定时全部填写。
- 外部输入解析、跨单元资源契约和共享状态集中的区域，单元宜小，保证 worker 能在执行窗口内逐个对象检查；纯查表、常量类文件可以并入相邻单元。
- target 范围内的每个主责文件，都要能在某个单元的 purpose 中找到职责；不分析的文件在范围 note 中说明原因。
- 有引用计数、对象池、队列额度等信号时，选择 storage_resource_recovery 方法论（名称是存储专项，资源账本程序通用）。
- 规划阶段仍不展开风险结论。
