# pangea-agent Agent Rules

本项目使用中文进行需求澄清、方案说明和开发沟通。代码符号、配置键、协议字段、文件路径和错误信息保持原文。

## 项目目标

`pangea-agent` 是面向测试分析的项目级 Agent。核心工作是把源码、结构化资料和覆盖率转化为：

- 代码与流程理解。
- 六维 DFX 风险账本。
- 可执行测试用例。
- 测试报告。

`analysis_profile=behavior-test-v1` 的一期 source-first 任务以可执行业务行为用例为交付主体：
正常主干、业务分支、异常处理与传播、清理恢复和真实 Coverage 补测。用例无需先建立 Risk；
随业务阅读完成轻量风险识别并保存有依据的风险，优先关联已有用例；不另做完整六维扫描。旧 Run 仍按其冻结
合同和 rubric 解释。

新建四场景使用 `analysis_profile=behavior-test-v2`，复用 source-first-v1 Graph；职责读取当前 Run
冻结的 `inputs/analysis-scene.json` 与 scene_* rubric。模块分析保留综合行为；风险分析保留风险板块；
分支与覆盖率分析不承担正式风险账本责任、不要求 risk_refs。四场景均先交付结构化模块流程
（behavior-flow-v1），保留 nodes/edges/paths，text 不能替代流程结构。采用新冻结规则的 analysis 在同一 flow
记录提供 sequence（sequence-v1）和 call_graph（call-graph-v1）；worker 只提供结构化事实，不提供坐标、SVG
或绘图命令，三种图由宿主确定性渲染，不另开生图 Agent。缺少或未知关系保留原始状态和说明，不猜造。
Coverage 通过资产进入，冻结已选输入内容指纹、来源及匹配诊断；不得在恢复时重新查询或替换版本。
不将上述规则补写入旧合同或旧 rubric，不增加语义门禁、完整复核轮次或替代 worker。

已有用例不作为长期管理资产。用户可以在单次 Run 中提供少量用例示例，它们只用于表达和环境参考。

## 独立判断

- 先检查用户说法是否包含错误前提、逻辑跳跃或缺失信息，不以附和代替判断。
- 区分已验证事实、基于证据的推测和主观取舍。
- 不同意时直接说明依据、风险和替代解释，并主动指出容易忽略的变量、成本和偏差。

## 语义权限与机械门禁

- Agent 和 Reviewer 对源码理解、单元划分、流程、风险、finding、证据含义和测试用例拥有语义决定权。Python 只负责输入准备、索引、状态、文件写入、确定性校验和报告组装，不得代写、改写、补全或推翻语义结果。
- Python 不得扫描历史 Run、目录、Agent 回复或结果内容来猜测当前 Run、action、task、worker 或下一阶段。所有生命周期操作必须使用当前 Graph 明确返回并已绑定的 `data_root`、`run_id`、`action_id` 和 `task_id`；缺少任何一项时如实停止，不得推断或兜底。
- 不得以模型稳定性、效率或“更可靠”为由，把语义判断迁移到 Python、schema、Adapter、提交工具或其他机械层。无法证明为确定性事实的判断留给原 Agent/Reviewer；证据不足时使用 `UNRESOLVED`。
- Python 发现结果不一致时默认只记录 `warning`、`degraded` 或 `evidence pending confirmation`，并保留 Agent 原始结论供 Reviewer 和报告呈现。可解析结果中的缺字段、枚举、ID、引用、路径归属、Coverage、basis、finding 或测试用例问题不得直接把 action/Run 判为失败。
- 硬阻断只允许用于当前 action 身份或绑定不成立、路径越出受控数据边界、冻结输入/结果文件损坏，或产物不可读取到下游完全无法继续的情况。即使产物不可读取，也只能保持当前 Run 等待同一 Agent 修复，不能由 Python 否决语义、改派 Agent 或把未审结论写成失败结论。
- 重试次数、运行时长和 token 消耗由 Agent 宿主负责停止失控执行；Python 可以记录次数和提示升级处理，但不得仅因达到固定次数而获得判定语义 action/Run 失败的权限。
- 机械检查不得根据关键词、行号重叠、数量、措辞、finding category 或测试用例内容判断分析质量，也不得增加测试用例语义门禁。
- `kind=flow` 记录的结构校验（`flow_schema.check_flow_structure`：body 为对象、format_version、字段名、节点 id 唯一、edges/paths 的 id 引用）属于格式契约，不属于语义门禁：写入时不合格即拒绝本次写入并逐条返回中文错误，由同一 worker 在本轮修正后重写；Python 不自动修复、不补造节点，也不判断节点 kind/label 的业务含义或流程是否完整（交给 Reviewer）。
- 新任务声明 `record_contracts.risk=behavior-risk-v1` 时，同样只校验 risk 的格式、字段与引用形态；旧任务沿用冻结规则。不合格由同一 worker 修正同一结果，Python 不判风险真实性、不补造正文、不据此判 action/Run 失败。
- 新增或扩大 schema、门禁、自动修复、结果提交工具或 action 自动化之前，方案必须先向用户说明：当前已验证的失败、准备新增的确定性行为、可能误拦有效结果的场景、Python 获得与明确不获得的权限，以及为何不能通过简化合同、Agent 规则或客户端限权解决。用户明确确认前不得实施。
- 门禁失败不得丢弃已经完成的有效语义内容、改派 Agent、重做整个分析或由主 Agent/Python 代修；只把精确错误交回同一 Agent 修正同一 `result_path`。若不能给出具体可修正字段和确定性依据，该检查不得作为阻断门禁。
- 新增检查的验收必须同时包含：合法语义结果能够通过、非致命不一致只降级且流程能够继续、不可消费或安全边界错误才进入等待修复、同一 Agent 修正后能够继续，以及未完成不会被报告为成功。只证明“能拦住错误”不算完成。

## 开发边界

- `src/pangea_agent/graph/` 是流程入口。
- `schemas/` 是数据契约入口。
- `src/pangea_agent/rubrics/` 是分析方法论入口。
- `pangea-data/` 是本地用户数据目录，不作为项目源码提交内容。
- `tests/` 是本地开发目录，当前不提交到 Git。

## Windows / PowerShell 规则

- 默认按 Windows PowerShell 可执行方式组织命令，不使用 POSIX-only 写法。
- 不使用 `cd /d ... && ...`、`source .venv/bin/activate`、`export VAR=...`、`rm -rf`、`touch` 等 bash 风格命令。
- 不把多个正式步骤串成一条命令；每个命令单独执行，上一条成功后再执行下一条。
- 优先使用 Python 模块入口：`python -m pangea_agent.cli.main ...` 或安装后的 `pangea ...`。
- 文件和目录路径按字面值处理。路径中的 `\n`、`\t`、`\r` 等字符组合不得解释为换行、Tab 或其他转义字符。
- filesystem、glob、read、git 等工具返回的路径必须原样复用，不得手工重写、拆分、转义或规范化。
- PowerShell 操作已有路径时优先使用单引号和 `-LiteralPath`，例如 `Get-ChildItem -LiteralPath 'C:\code\nof\tcp'`。
- 某个文件工具因特殊路径访问失败时，可直接换用 read、Python `pathlib` 或 PowerShell `-LiteralPath`；不要仅根据该工具的路径错误判断目录不存在。
- 读写项目文件优先使用 Agent 客户端的 read/write/edit 能力；需要执行命令时只执行单一、明确、可复现的命令。
- 不自动对 `pangea-data/repositories/` 下的用户源码执行 `git pull`、`reset`、`stash`、`checkout` 或格式化。

## 源码分析原则

- 用户源码从 `pangea-data/repositories/<repo_id>/` 引用。
- 分析结果引用源码时使用 `repo_id:path:line` 形式。
- Git 信息只作为版本描述；普通源码目录同样可以分析。
- 测试用例优先用黑盒语言表达，函数、变量和行号只作为证据。

## 运行规则入口

Desktop 外部 source-first worker 读取 `docs/source-first-cli-worker.md`；内部 DSH 使用 `.agents/pangea/`，OpenCode 使用 `.opencode/agents/`，独立 Claude 使用 `CLAUDE.md` 指定的本客户端协议。实际加载与发布复制关系见 `docs/worker-instruction-loading.md`。分析输出内容以当前 task 冻结的 rubric 为准，开发规则不替代运行合同；旧 Run 不追读工作树的新场景规则。

## 实现约定

- 新节点放在 `src/pangea_agent/graph/nodes/`。
- 新数据模型放在 `src/pangea_agent/models/`。
- 新的确定性输入处理放在 `src/pangea_agent/documents/` 或 `inventory/`。
- 新分析方法论放在 `src/pangea_agent/rubrics/builtin/`。
- CLI 能力放在 `src/pangea_agent/cli/`。

## Agent 客户端

- 各客户端只读取自己的入口和 Agent 规则。客户端目录中的内容不得引用或调用另一个客户端的规则文件。
- 共享范围只包括 graph、schema、rubric 和 CLI 契约；客户端专有的命令、会话轨迹和 Agent 调用方式不得写入共享方法论。
- Python 不调用模型 API，也不做语义拆分。一个 Planning Agent 按功能模块或文件族规划单元；risk-method-v1 按资源申请/释放、数据生产/消费的契约闭包规划，无法同单元时显式放入 context。
- 首轮 analysis 最多同时派发 3 个互不重叠单元，总单元数不受 3 限制；worker 不得再派发子 Agent。
- 标准型 analysis 结果齐备后先启动 1 个盲审 Reviewer。`independent_review` task 不包含 analysis result；Graph 接受盲审后，以 `continue_agent` 续接同一 Reviewer 的真实 `task_id` 执行 `comparison_review`，再向该会话开放盲审与首轮结果做对照裁决。Comparison 不是第二次完整分析，也不再启动新的 Reviewer 或第三个复核 Agent。
- comparison review 保留的 finding 只为受影响单元生成一次 `targeted_closure`；该 action 必须续接对应单元首轮 analysis worker 的真实 `task_id`，在 Workflow 预先复制的 closure 结果中定向补齐，不能创建替代 worker，也不能修改原始 analysis 结果。
- 生命周期调用只使用 Graph 返回并绑定的 exact data_root/run_id/action_id/task_id；实现需保持原 worker 续接、唯一 result_path、有效内容保留和同一结果修复。具体客户端命令只放对应运行入口，禁止混用客户端。
- Python 只校验 schema、真实 ID、路径归属、引用关系和流程结构等确定性契约，不根据行号重叠、finding category 或测试用例内容推翻 Agent/Reviewer 的语义结论。Coverage 只提供当前 `source_scope` 中唯一匹配的零覆盖提示，不强制生成测试用例。

- 速度型 analysis_settings.mode=speed 跳过盲审，派发一个直接审核首轮结果的 Reviewer，沿用 comparison/closure 合同；旧 Run 遵循冻结设置，缺省为标准型。外部 ACP 单元分析最多三个并发，状态变更顺序执行。

## 定向修正执行预算与部分交付

- analysis 和 targeted_closure 在内部 DSH 与外部 ACP 均最多三个 worker 并发；Graph/result 写入串行化。复核依赖关系保持不变。
- 同一单元集中处理关联 finding，只进行一次语义修正，不因措辞变化发起新一轮完整复核。宿主在定向修正原回合后最多自动续接一次机械修复；仍未完成则暂停，覆盖上述通用修复提示次数。速度型每次继续的修正执行窗口 15 分钟，标准型 30 分钟；不是整个 Run 的耗时承诺。
- 暂停不判语义失败，不替换 worker，不丢弃其他单元成功结果。用户继续时续接原 task；结束修正时先冻结写入和停止执行，再保留已有产物生成 UNRESOLVED 部分报告。
- 用例统一沿用 execution_readiness=ready/needs_instrumentation/unknown 与 readiness_reason。内部函数和变量只能作为证据，除非有明确的测试桩/注入设施；不由 Python 关键词过滤代替语义审核。新规则只冻结到新 Run。

## risk-method-v1 的确定性提示

behavior-risk-v2、behavior-check-v1、behavior-review-check-v1 只检查结构、引文是否逐字出自冻结位置、引用 ID 是否存在。用户 2026-10-15 明确选择 G1–G4 全部提示模式；不得拒写或因缺复核记录阻断，不获得风险真实性、相关性或充分性的判断权。旧任务继续依冻结合同执行。
