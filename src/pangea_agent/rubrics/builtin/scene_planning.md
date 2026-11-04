# 四场景规划职责

仅适用于behavior-test-v2。先读analysis_scene，再按目标、入口和生命周期划分主责；冻结范围是可读边界，不是全目录交付义务。
沿用当前语言规划规则，不在Planning穷举状态机、风险、分支或用例。
用既有purpose简述每个单元职责，并指定一个已有单元负责对象主干结构化业务流程及跨单元关系；不新增全局流程Agent或等待屏障。
先按 task.target、task.focus 和 analysis_scene.objective 理解用户要验证的对象及目标。product-behavior 以产品行为为入口；interface-contract 以公开接口的输入、调用顺序、状态、返回与资源责任为入口。目标只由用户显式选择冻结，不因库、驱动或文件名自行切换。
只读足以确认入口、主干方向及必要另一侧的源码，再按跨文件业务、共享状态、资源创建/释放和异步受理/完成组织职责；不把文件列表机械转换为单元。主干 owner 的 purpose 简述可见入口、主干责任和必要挂接点，不展开调用链，其他单元写明与主干的关系和负责的边界。接口拥有者、调用方或产品入口在冻结可读范围外时，在范围 note 与 unresolved 记录已知边界及缺少的证据，不猜造入口、不请求越界读取。
module保持综合范围；risk按下方“风险场景规划”；branch围绕业务路径；coverage按真实缺口选择重点及必要调用链，先理解对象主干，不做全范围风险扫描。
按需读取coverage_match_summary、资料和methodology_catalog；客户端提供冻结Coverage聚合查询时先按目录/文件定位，再读取所需真实gap ID和原记录，不全量展开匹配明细。选择真实方法论ID写入methodology_ids，仅在需要澄清适用边界时简述理由；没有相关方法论不编造ID。不能仅按名称、行数、文件数划分，也不能全量展开清单。
methodology_ids是本单元选择的补充方法论；Analysis读取该单元所选的内置/用户方法论，Reviewer读取各单元选择的合集。选择应服务当前场景，不因选择通用方法论扩大branch/coverage的正式风险责任；空选择合法，不逐项写排除论证，不自动补入全套DFX。
coverage的主干理解和流程交付不等于普通基础功能用例义务。按适用的真实gap及必要调用链分配职责，在现有purpose和coverage_ids中明确负责的缺口；跨单元缺口指定一个已有owner汇总处置，其他单元提供必要context_files/context_regions，不重复补主成功场景。正常路径只有自身有真实未覆盖目标才独立补测，否则只是缺口触发的前置或恢复。规划不穷举case，由Analysis逐gap核实入口、必要条件与普通操作不足之处，并在每个关联case的body.summary中对应真实coverage_refs说明，不仅写全局summary。函数count>0不能排除内部零计数分支侧，不按函数整体计数删去此类职责；复用已有用例保留原purpose，允许零新增。
覆盖数据没有新缺口时仍安排对象流程理解与数据范围说明，零新增用例合法；无数据、版本未知和不匹配不得解释为100%覆盖。

## 分支场景规划（behavior-branch-v1）

仅当 analysis_scene.method_version=behavior-branch-v1 时适用。分支职责是业务条件、配置、状态、顺序、结果和恢复的测试设计，不承担产品风险判断。
- 用现有 purpose 明确单元负责的业务操作、入口、生命周期和输出；以业务关系划责，不仅按文件或函数列表切块。
- 参数/配置的生产与消费、前驱状态与后续操作、请求受理与完成、资源创建与清理若跨单元，尽量由同一 owner 分析完整业务关系；需要分开时在 purpose 写明关系 owner，把必要另一侧放 context_files/context_regions。不能两个单元都假设关系由对方覆盖。
- 规划只确定职责和必要依赖，不在这里枚举全部取值、组合、状态机或用例。Analysis 在当前单元内依 scene_branch 从证据提炼业务覆盖项并维护普通 note，Reviewer 依冻结 scene_branch_review 双向核对；两份场景规则不依赖可选 methodology_ids。
- 不新增全局覆盖 Agent、等待屏障、风险扫描或完整复核轮次。边界无法确定时明确依赖缺口，不扩展为全仓分析。

## 风险场景规划（risk-method-v1）

仅当 analysis_scene.method_version=risk-method-v1 时适用。
- 先读 planning_metadata 的 risk_signal_overview 和 risk_signal_summary 输入。信号只是线索，用来定位资源、锁和取消状态、引用计数、外部输入解析、定长缓冲区集中的文件。
- 按“契约闭包”划分单元：同一资源的申请与释放责任、同一配置或数据的生产与消费责任、同一共享对象的访问责任、同一解析器与其输入来源，尽量放在同一单元的 owned 范围；Planning 不枚举全部释放点或访问者，完整对象核查由 Analysis 执行。必须分开时，把另一侧写进 context_files 或 context_regions，并在 purpose 中写明契约关系。
- 每个单元填写 risk_lenses，取值 error_path、resource_lifecycle、boundary、concurrency、untrusted_input、contract；按信号和职责选择，不确定时全部填写。
- 外部输入解析、跨单元资源契约和共享状态集中的区域，单元宜小，保证 worker 能在执行窗口内逐个对象检查；纯查表、常量类文件可以并入相邻单元。
- target 内已确定的主责文件必须有 owner；purpose 描述业务职责，不逐文件重复说明。不确定的边界以相关 note/unresolved 交接，不为无关文件逐个写排除证明。
- 有引用计数、对象池、队列额度等信号时，选择 storage_resource_recovery 方法论（名称是存储专项，资源账本程序通用）。
- 规划阶段仍不展开风险结论。

## 测试能力参考

若 task 提供 test_tool_reference_paths，这些冻结工具文件只供原 Analysis/Reviewer 按用例的入口、参数、观测或恢复缺口按需检索，不属于主责功能源码；不为工具另建分析单元、覆盖义务或工具分析 Agent。Planning 不全量展开工具，必要时在现有 purpose 中提示相关能力参考，具体操作说明由原 worker 核对后保存为普通 note。
