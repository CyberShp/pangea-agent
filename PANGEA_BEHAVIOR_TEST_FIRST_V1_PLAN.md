# PANGEA 业务行为用例优先：开发与验收方案

编写日期：2026-09-06
执行对象：Sol High
状态：已按方案完成开发和本地回归；真实客户端验收未通过。执行证据见
`PANGEA_BEHAVIOR_TEST_V1_ACCEPTANCE.md`。后续真实验收改用 DSH，当前受 Provider
未注册和产品部署依赖缺失阻塞；不再由 Codex 启动或监视 OpenCode 进程。

## 1. 先用测试人员的话说明目标

先把 PANGEA 做成一个能够稳定回答以下问题的工具：

1. 这个模块有哪些正常流程和业务分支需要测试？
2. 超时、中断、资源不足等异常发生后，上层收到什么结果？
3. 失败之后怎样清理、恢复，能否再次操作？
4. Coverage 报告中的未覆盖函数或分支，怎样从实际业务入口触发？
5. 测试人员如何准备环境、执行步骤、判断对错和恢复现场？

本期用例直接来自这些行为，不需要先建立一条风险记录。专项风险搜寻、完整六维 DFX 分类、历史缺陷机理和领域专项扩展，留待基础用例能力稳定后再开发。

这不是把上述能力排成每个 Run 都必须执行的新流水线。本期交付就是业务行为用例生成。源码分析中已经确认的问题仍保留，并说明与相关用例的关系；不能把源码当前的错误行为写成正确预期。

### 本期完成后用户应当看到

- 以用例为主体的报告，正常、业务选项、异常、异常传播、清理和恢复均有实际内容。
- 有真实 Coverage 时，能看到具体缺口对应的补测用例或尚缺的条件。
- 没有 Coverage 时正常生成用例，并明确没有实际覆盖率补测结论。
- 测试步骤通过受支持的业务入口或公开 API 执行；需要故障注入时明确开发协助条件。
- 输出不再依赖先填写 Risk → BranchDecision → Scenario → TestCase 多套重复结构。
- 完成后不被无条件拉回再做一次全量复读。

## 2. 执行基线与开发规则

### 2.1 必须使用的工作树

| 项目 | 已核实的基线 |
| --- | --- |
| 实现工作树 | `/Volumes/Media/pangea-agent-source-first-v1` |
| 分支 | `codex/source-first-v1-agent-rules` |
| HEAD | `5dae058`，实际基线还包含当前未提交修改 |
| 原工作树 | `/Volumes/Media/pangea-agent`，位于另一分支，不能误在这里覆盖实现 |
| 数据目录 | 实现工作树的 `pangea-data` 指向原工作树的数据目录 |
| 上轮证据 | [PANGEA_REPAIR_ACCEPTANCE_20260906.md](/Volumes/Media/pangea-agent-source-first-v1/PANGEA_REPAIR_ACCEPTANCE_20260906.md) |

Sol 开始时重新执行单独的 `git status --short --branch`、`git worktree list`，记录实际基线。若用户已经将修改合并到其他工作树，以用户明确指定的新位置为准；不能只根据 HEAD 相同就认定包含全部实现。

当前工作树有大量既有修改和未跟踪的 `.opencode/plugins/`。不得 reset、checkout 覆盖、自动 stash、重新拉取覆盖，或把它们当作本次可删除文件。开发期间不要修改用户源码及 Run17、Run18 的冻结输入或结果。

### 2.2 Sol High 必读

按顺序完整读取：

1. `/Users/shepard/.agents/skills/no-negative-echo/SKILL.md`，并按条件读取附加参考。
2. 执行工作树的 `AGENTS.md`。
3. 执行工作树的 `.agents/skills/private-house-code/SKILL.md`。
4. 本方案全文。

用户确认本方案后，Sol 才进入正式开发。本次生成方案本身不代表已经开始开发。若实现需要越过第 3 节范围，先报告具体差异，不自行扩大工程。

测试文件按项目规则保留在本地 `tests/`，不得通过强制添加绕过 Git 忽略规则。未获用户要求，不自动提交或推送。

### 2.3 当前问题的事实边界

Run18 使用 OpenCode + `minimax-cn-coding-plan/MiniMax-M3`：

- 首轮未完成，最大单次输入含缓存 299689 tokens；Reviewer 尚未启动。
- 5 次缺 `body`，均出现在非 risk 类型的提交中；另有 1 次输出截断后的 JSON EOF。
- 有效用例 9 条，其中 5 条来自普通流程、4 条关联风险；均未完成正式审核。
- 没有真实 Coverage 输入。
- 43 次结果回读，存在历史版本重复输入和宿主截断；索引、搜索回包也较大。
- 正文源码读取的行区间重叠不多，不能把失败归因为反复全文读源码。

因此本期同时处理业务目标和通用执行问题。风险分析后置不能代替提交接口及读取减负。

## 3. 本期修改范围

### 纳入

1. pangea-agent 内的一期目标、冻结任务说明和角色规则。
2. OpenCode 普通正文提交接口的平铺化。
3. 面向新 Agent 的有界结果、索引和搜索读取。
4. 删除 Analysis、Comparison 完成后固定追加的两轮复读。
5. 报告的用例优先呈现和本期质量范围说明。
6. 本地定向验证、真实 OpenCode 验收以及测试用例的实际可执行性抽查。
7. 本仓库 `.agents/pangea/` 的语义规则同步及现有 CLI 兼容性验证。

### 保持

- 现有 Graph 阶段及角色数量：Planning → Analysis → 独立盲审 → 同 Reviewer 对照 → 必要时原 worker 定向修正 → 报告。
- 冻结源码、真实 task/action 绑定、受控路径、原子写入、并发版本保护。
- 原始首轮结果及修正历史；`pangea-notes-v1` 存储格式。
- Reviewer 对语义质量的决定权，Python 的确定性处理边界。
- 旧 Run 的可读性，现有 DSH 使用的 CLI 参数和返回语义。

### 本期不承担的交付

- dsh-pangea、pangea-desktop 的外部仓库修改和 UI 改造。
- DSH 新版平铺工具的实际部署、内网 CodeAgent/DeepSeek 验收。
- 覆盖率采集平台、新代码解析器、自动执行任意用户测试的框架。
- MR 流程重构、数据库迁移、缓存系统、自动扩范围或新的语义校验器。

DSH 是目标客户端，但本仓库只有它的规则入口，不能通过改文字创造尚不存在的工具参数。本期完成后必须分别汇报“OpenCode 已验证”和“DSH 尚未真实验收”。

## 4. 一期产品合同

### 4.1 用例范围

对用户指定范围内以下有业务意义的行为形成用例或明确缺口：

| 行为 | 检查内容 |
| --- | --- |
| 正常主干 | 入口、必要操作顺序、成功结果、后续业务 |
| 业务分支 | 配置、模式、输入及已有状态引起的不同结果 |
| 异常处理 | 明确的失败条件、错误反馈、清理和退出 |
| 异常传播 | 底层失败怎样到达上层返回、回调或外部状态 |
| 生命周期 | 初始化、关闭、重试、再次调用、恢复后的行为 |
| Coverage 补测 | 冻结记录中的真实零执行函数或零执行分支结果 |

同一入口、准备条件、外部结果及恢复方式能够共同验证的行为可以合并用例。会产生不同错误反馈、资源后果或后续状态的行为必须分别说明，不能只按代码相同或返回值相同合并。

这里的完整性不是穷举所有路径组合、所有并发交错，也不是一条 `if` 一条用例。Python 不根据 branch 数量和用例数量判断完成。

### 4.2 单条用例正文

使用现有 `test_case` 记录，正文为普通文本或 Markdown，写清：

- 标题、所测行为和受支持入口。
- 前置条件；需要开发协助时说明协助制造什么条件。
- 操作步骤与对应预期。
- 预期的依据：接口约定、需求、任务明确约定或可证的行为条件。
- 外部观测及清理/恢复。
- 冻结源码坐标；真实 Coverage ID 及具体命中目标；必要时关联已有 flow/问题记录。

以上是 Agent 的内容要求，不是 Python 必须解析出的固定字段。不固定标题措辞，不检查关键字，不设字数、用例数或每类记录的最低数量。

例子只规定表达方式，不把下述场景预置为所有模块的必测项：

```text
标题：认证超时后重新连接
行为：认证等待超时后的退出和恢复。
入口：已确认的客户端连接命令或公开接口。
准备：有效配置；测试环境可在指定阶段阻断对端响应。
步骤与预期：发起连接，检查约定的超时反馈；解除阻断后重新连接，检查后续业务。
观测：调用方结果、连接状态、业务操作结果。
恢复：移除故障条件，关闭测试连接，恢复配置。
依据与关联：实际读取的冻结源码坐标；若有匹配 Coverage，写真实 ID 与目标结果。
```

实际产物必须用当前项目已证实的入口和观测替换描述性占位；没有依据就列为待确认，不能包装成可执行用例。

### 4.3 问题、缺口和 Coverage

- 发现明确问题时，沿用 `risk` 或普通说明记录保留触发条件、实际行为、与约定的差异、证据和相关用例；本期不要求补完整六维分类、严重度和专项方法论字段。
- 已经确认的问题不能因为测试环境尚缺注入能力而删除；写明已证实事实及尚缺的复现条件。
- 无法确定入口、预期或观测时使用 `unresolved`，保留已经完成的有效用例，不把整个 Run 自动判失败。
- Coverage 是来自真实采集的执行信息；没有 Coverage 输入不能称为“零覆盖”。
- 每个已匹配缺口由 Agent 说明对应哪条用例、被哪条用例共同覆盖，或为什么仍需确认。可以在正文/最终简短说明中表达，不新增 `CoverageDecision` 强制数组。
- 函数执行、分支 true/false 执行是不同目标；不能把同一场景的全部 Coverage ID 复制给每条用例。
- “已生成补测用例”“用例已经执行”“Coverage 实际改善”必须分开表述。

## 5. 任务版本与规则落地

### 5.1 一个冻结目标标记

在 `TaskContract` 和对应 schema 增加可选字段：

```text
analysis_profile: "behavior-test-v1" | null
```

它只标识本次交付范围，用于选择输入规则和报告说明，不是新的 Graph 流程、模型可调模式或质量门禁。

规则：

1. 仅全新 `source-first-v1` Run 在创建边界默认写入 `behavior-test-v1`。
2. 已有 `progress.json` 的 Run 不补写、不改写其冻结合同；缺少标记保留原任务含义。
3. 标记随合同进入 Planning、Analysis、盲审、Comparison 和 Closure 的 task，不由模型自行选择或猜测。
4. `workflow_version` 继续使用 `source-first-v1`，不增加第二套生命周期。
5. 新报告说明 PASS 针对“业务行为用例交付质量”，不代表专项风险评估或用户系统全部测试通过。

涉及：`models/contract.py`、`schemas/task_contract.schema.json`、`cli/run_module_analysis.py`、`graph/nodes/source_first.py`、`report/source_first.py`。直接使用 Graph 的本地测试也必须显式设置正确合同，不能用缺标记伪装新版本验收。

### 5.2 一期只加载一份聚焦方法

新增短而完整的 `src/pangea_agent/rubrics/builtin/behavior_test_generation.md`，表达第 4 节及必要源码推理原则。

新 profile 的 Analysis/Reviewer 默认加载这份方法、用户实际选中的业务资料和真实 Coverage。取消自动加载 `dfx`、`risk_reproducibility`、旧 `test_case_generation` 和包含旧输出字段要求的分析方法。

需要保留的源码理解原则直接精简到这一份方法：

- 输入状态、调用顺序、错误产生点到最终返回/回调。
- 清理后的资源与状态、再次调用和恢复。
- 当前实现与正确预期分开；源码证据与解释分开。
- 共享 case 按前驱条件和结果判断，不套用相似分支。
- C/C++ 保留真实求值、类型和条件编译含义；Lua 保留真实返回值、异常和清理含义。
- 公共 API 可以是测试入口；私有 helper 及直接修改内部变量不能冒充业务操作。

不把旧五 Pass、逐 branch decision、独立 Scenario、双向多层 ID、全文抄录证据要求复制进新方法。不把整个专项规则目录自动放进新任务输入。

旧方法文件仍服务现有历史/legacy 合同，不删旧 schema、不覆盖旧 Run 已冻结的 rubric。这里只改变新 profile 的实际选取。这个有限兼容分支的用途是保护真实已有调用方，不发展成通用 profile 框架。

Planning 继续使用现有语言规划方法，保留按功能和生命周期划分的原则。对新 profile 去掉“必须选专项方法论”的要求；不因追求更多并发把一个业务流程机械拆散。250K 判断应包含预计输出和回读成本，不只比较源码文件大小。

### 5.3 AGENTS 与客户端规则

更新本工作树 `AGENTS.md`：

- 项目目标区分本期业务用例交付与后续专项增强。
- 明确 source-first notes 是本期正式外壳，正文不是旧富结构 Schema。
- 用例无须先有 Risk；记录已确认问题时保留事实与用例关系。
- 保持语义权限、安全边界、原 worker 修正及报告真实性条款。

更新 `.opencode/agents/` 下 planning、analysis、review、main 及 `.opencode/commands/module-analysis.md`。

同步 `.agents/pangea/` 下 planning、analysis、review、closure、dsh 的语义要求；DSH 的参数说明仍依据现有工具，不照搬 OpenCode 新参数。两类客户端不得互相引用规则文件。

涉及一期目标的规则明确以冻结 task 中的 `analysis_profile` 为准；旧 Run 缺标记时不能套用新目标来重解释已经完成的工作。接口说明可以同步更新，但不得借此改写旧任务的交付范围。

### 5.4 各角色执行要求

**Planning**：确定主责单元和必要参考；分配真实 Coverage；不生成用例或风险结论。

**Analysis**：按业务行为读取和理解源码，形成一段足够解释测试的 flow 和对应测试；已有共享 flow 可引用，不强迫每个函数生成记录。每完成一组可消费的行为及用例就保存，不把用例都留到最后。完成前做一次基于现有证据的一致性检查，只有发现具体疑点才补读源码。

**独立盲审**：不读 Analysis 结果，独立确认重要正常/业务/异常/恢复行为和预期依据。此时不能声称“Analysis 遗漏”，因为尚未看到 Analysis；也不需要先证明产品 bug 才能保存审查结论。

**Comparison**：续接同一 Reviewer，对照首轮和盲审。需要修正的可以是必要用例遗漏、错误预期、不可执行触发/观测、清理错误或 Coverage 对应错误，不以产品存在缺陷为前提。正确且已经表达的行为无需重写。

**Closure**：只修明确选中的问题，续接原 worker，在既有复制结果中修改；不重做整个单元，不更改首轮原件。

不能只改 Agent Markdown：插件首次派发使用的 `comparisonInstruction` 目前仍限定“证明具体错误外部结果才能成为 finding”。新 profile 必须同步改为上述业务用例审查合同，允许重要用例遗漏进入明确的单元修正。没有该用例时，以真实 unit_id 和缺项依据定位，不要求填写尚不存在的用例 record_id。验收捕获实际派发 prompt，确认没有与角色规则相冲突的风险前置条件。

专项方法未执行不自动触发 UNRESOLVED；本期必要行为或预期仍缺证据时由 Reviewer 如实作出本期结论。现有 closure 后未再独立审查的真实性说明继续保留，不为获得 PASS 新增一轮审查。

## 6. 正文提交接口：只改必要的外壳

### 6.1 OpenCode 模型看到的普通接口

```text
pangea_result_write(kind?: string, body: string)
pangea_result_supersede(target_record_ids: string[], kind?: string, body: string)
```

- 一次提交一条正文。`kind` 缺省沿用 `note`；常用 `flow`、`test_case`、`summary`、`risk`、`unresolved`。
- 正文可有换行、引号、代码片段、中文和源码坐标。正文内部不再是任意多层 JSON 对象。
- 普通语义证据和关联写在正文中；它们供 Reviewer 理解，不让 Python 从正文推断派发目标。
- 插件包装成已有 CLI `--records '[{kind,body}]'` 或 `--replacement`；CLI 存储协议和 `NoteRecord.body: Any` 保留。
- revision、request_id、当前 action/task 继续由宿主管理；保留串行写入与真实外部并发冲突。
- 不按内容相似度自动合并、退休或改写记录。真实修正仍由 Agent 提供精确目标。

同步修改插件动态生成的 `closureInstruction`、局部恢复提示和工具描述，不能残留要求模型传 `replacement` 或顶层 `relates_to` 的旧普通接口示例。专用 finding/decision 工具的机器关联仍按第 6.2 节保留。

小批次改为单条可能增加调用次数，验收要同时记录成功调用量和重发量，不能只凭接口更短宣称更省 token。

### 6.2 不合并的专用接口

- Planning 的 owned/context regions 与机器 unit_id 仍显式传递。
- Comparison finding 的 unit_ids、Reviewer disposition/correction_record_ids、替换目标仍显式传递；不能藏进 Markdown 让 Python 猜。
- `result_repair` 是“外壳损坏后的整组记录恢复”，不是普通单条追加。本轮保留其独立受限恢复合同、原字节保护及身份校验，不机械改成一个 body，不宣称解决任意大损坏产物恢复。

### 6.3 错误与原文保护

- 合法提交的正文原样保存；程序不解析 Markdown 去补齐字段、不改写语义。
- 已保存的旧 JSON body、record_id、supersedes、revision、receipts 保持可读。
- 非致命关系/类型提示不得触发整份分析重做；已有 warning 原值不得丢失。
- 工具请求本身无法调用时，明确指出参数和本次是否保存，交回同一 worker。不能把工具请求失败写成分析语义失败或自动标记完成。
- 不能承诺客户端在执行前拒绝的错误参数已经落盘；这种情况下保留已有结果和宿主轨迹。不得新增 Python `$text`/`item` 猜测转换器。
- 当前低层兼容接口未保证任意额外顶层语义字段无损。新普通接口将正文集中到一个显式 string，不以修改所有历史不规范载荷为本期目标。

主改文件：`.opencode/plugins/pangea.ts` 及相关 OpenCode 规则。只有需要改精确错误说明时才改 `graph/result_store.py`；不扩展正文校验。

## 7. 读取减负与兼容

### 7.1 新 Agent 默认看到什么

- 结果：当前有效记录的小页；历史显式读取；指定记录 ID 仍可读到原记录及有效状态。
- 索引：先看函数、声明相关坐标；分支细节按需展开。
- 搜索：文件、行号、短预览；完整正文通过已有 source_read 取得。
- 工具 JSON 使用紧凑序列化，正文值不变。

初始整页预算设为 12000 个 Unicode 字符，按最终紧凑 JSON 计算；CLI 可允许至 24000，但新模型工具不暴露任意放大页的控制。这个值是传输预算，不是内容长度要求或分析质量门禁。

不得只限制记录条数后宣称回包有界；warnings、completion.note、历史编号列表及外层元数据都计入预算。超长内容必须连续可读，不能静默截断。

### 7.2 明确区分旧接口与新分页视图

为现有 `result-read`、`comparison-read`、源码索引/搜索及 `source-read` 增加显式分页视图参数，例如 `--view compact`、`--page-token`、`--max-chars`；以下为本方案要求新增的接口，不是已经存在的命令。`source-read` 的新视图用于请求范围内的长正文/长行连续读取，不缩小原先允许请求的行区间。

- 新 OpenCode 封装统一请求 compact 视图；普通结果默认 current，历史由 `--include-history` 明确请求。
- 省略 `--view compact` 的现有 CLI 调用，保留旧整数 cursor、limit、记录 shape 和 comparison 每 entry 分页含义，保证当前 DSH 不被静默破坏。
- 两种视图读取同一份既有 notes/冻结源码，不增加影子存储或第二套生命周期。
- 新模型只接触一种读取视图；不要让它在新旧参数之间猜测或切换。
- 对旧 CLI 的兼容只是保留现有调用，不能据此声称 DSH 也已获得有界新回包。

插件用于 revision、空结果、完成状态和无进展判断的内部读取也切换为 compact 标量元数据，不再依赖 `superseded_record_ids.length` 或原 `completion` 对象形状。明确返回并使用完成布尔值、声明 revision 及 `total_record_count`；内部检查无须把正文和历史传给模型。写入前、派发前后和恢复路径一起回归，避免仅修改模型工具而破坏宿主状态判断。

### 7.3 compact 视图的确定性要求

1. 返回标量 `revision`、`total_record_count`、`active_record_count`、完成布尔值和声明版本；长完成说明、warning 正文、历史记录按页读取。
2. 普通记录能装入页时整条返回。单条过长时，以原记录的紧凑 JSON 文本返回明确片段，携带 action/record 标识、片段位置及续页 token；片段不能伪装成完整 record。
3. 按片段顺序连接后必须可还原原 JSON 值。字符切分不是语义总结；不删字段，不用“摘要”替代原文。
4. 游标使用无状态、不由模型拼装的 token，绑定当前 action、读取条件、revision 和位置。每次仍执行已有身份及路径校验。
5. 翻页期间结果发生写入时，返回读取版本已变化；调用方丢弃旧 revision 尚未拼完的片段，从当前筛选条件的第一页重新读取，不能用旧页位置续接已移位的有效记录集合。这是读游标失效，不生成语义 repair，不失败 Run。
6. Comparison 按已有冻结 version set 的 entry 和记录顺序形成一个全局页预算；游标同时绑定 version_set_id 和筛选条件。保留冻结 revision 检查，不另建快照；冻结版本不一致时按既有完整性处理，不能为了继续翻页自动切到最新结果。
7. 同一个 `rec-...` 在不同 action 中不是同一对象；回包和游标不能丢 action 归属。
8. 对超长 warning、空结果、大历史列表同样能够有限分页；避免返回无进展的空页和永远不结束的游标。

不开发通用查询引擎或缓存框架。用现有模块中的小函数完成同一数据的分页投影；若实现发现无法在保留旧 API 的前提下满足合同，先指出具体调用方冲突。

### 7.4 索引与搜索

- 新视图的索引默认不枚举每个 branch 标记；但声明可能位于 type/macro/raw，不能只留下 function/global 就把头文件和不完整解析区域藏掉。
- 保留 branch 明细显式展开和所有冻结正文读取入口。
- 搜索默认短预览，不附带一长串重叠 region ID；所有命中仍可翻页，路径和行号准确。
- 有长宏、长源码行时同样使用明确续页，保持原行号；不把截断片段说成完整函数。
- 不再降低普通 source_read 的可用正文范围来制造节省，先解决导航和结果回包。

涉及：`graph/result_store.py`、`cli/source_first_api.py`、`cli/main.py`、`inventory/source_access.py`、`models/source_first.py` 和 OpenCode 插件。存储模型与旧语义 schema 保持不变，新增字段只用于读取视图。

## 8. 删除固定追加复读，保留真实恢复

删除 `.opencode/plugins/pangea.ts` 中：

- Analysis 第一次完成后固定发送的全量源码反证 prompt。
- Comparison 第一次完成后固定发送的全量对照 prompt。
- 为上述两轮服务的“必须新增 summary/记录才能结束”判断与新流程中的 finalizationBaseRecordCount 状态。

正常 worker 完成并提交有效完成声明后，直接进入现有 settle；模型内部为执行任务产生多次工具调用，不等于新增宿主复读回合。

保留：SDK 错误及输出截断检查、空结果/未声明完成/版本不符的精确提示、至多一次同会话局部恢复、无进展暂停、原 action/task/result 续接。

已有 pending_repair 中的 finalization 字段保持可读取，依据实际完成状态续接，不清空历史，不强制补一条没有新内容的记录。若去掉这些提示造成实际错误遗漏，应先检查第 5 节角色职责是否落实，不直接恢复固定全文二次审核。

同步修正现有 `repairInstruction` 中“所有修复都必须产生新 revision”的泛化提示：正文已有效、只是缺少或过期完成声明时，核对后重新声明即可；旧 finalization 计数本身不要求改正文。只有具体内容或引用需要更正时才要求原 worker 定向修改，SDK 恢复也不能一律逼写新内容。不新增恢复框架。

## 9. 报告与状态

新 profile 报告以以下顺序呈现：

1. 本期任务范围与完成/质量状态。
2. 当前有效测试用例。
3. 必要业务流程、Coverage 对应说明和待确认事项。
4. 已确认问题及 Reviewer 对照结论。
5. 独立标注的修正记录和审计历史。

仅依据 Agent 提供的 record.kind 和真实生命周期分组；Python 不读正文判断它是不是“足够好的用例”，不自动生成业务总结。

可以优化标题和分组，但保留所有原文、引用及历史。原 worker closure 已保存且未再独立审查时继续如实说明；不自动把 UNRESOLVED 改成 PASS。

“当前有效用例”按单元展示当前被接受的交付版本：有已接受 closure 时依据既有 `original_task_path` / `original_result_path` 和 accepted revision 绑定显示其当前记录，原 Analysis 放入首轮对照；没有已接受 closure 时仍显示 Analysis。未完成修正单独标注，不能覆盖正式版本。不能把复制的同一条用例在首轮和 closure 中重复计为两条，也不能根据正文相似度、文件时间或 record_id 单独猜测跨 action 归属。

说明清楚：本报告是生成的业务用例；除非有实际执行证据，不能说用户产品已经测试通过、Coverage 已提高或专项风险审核完成。

保留 `report.md`、`report.html`、`report-complete.json` 的现有完成顺序：全部写入成功后才保存 Run complete。旧 JSON body 继续可见；HTML 至少清楚显示标题、段落、步骤和原文，复杂 Markdown 表格/代码高亮不是本期另建渲染框架的理由。

## 10. Python 获得和不获得的权限

| 改动 | 确定性工作 | 不获得的权限 |
| --- | --- | --- |
| profile 标记 | 冻结已确定的交付范围、选择对应规则、报告范围 | 自动推测用户要不要风险分析，或据标记判质量通过 |
| 平铺提交 | 组装现有外壳、保存原文、管理机器版本 | 解析正文补语义、猜测关联、将正常行为升级为风险 |
| 紧凑分页 | 按大小装页、保留原值、校验读取版本 | 以篇幅、关键词、数量决定分析是否完整 |
| Reviewer 修正选择 | 按明确 finding/unit/task ID 路由 | 自动选择该修哪些用例或改派 worker |
| 报告排序 | 根据既有 kind 和状态显示原文 | 宣称用例已执行、覆盖率已提高、缺口已解决 |

为什么不只改提示词：Run18 已出现实际的包装层级错误和工具回包截断，提示词不能改变接口形状或回包大小。

为什么不再加正文校验：这会重新把有效分析转成字段返工；内容是否正确由 Agent/Reviewer 核实。过长但有效内容使用分页，不拒绝；缺少可执行条件则保留待确认，不靠模板补齐。

新读取参数不正确、工具参数不可调用，只返回具体调用问题，不据此否决语义。硬边界继续只保护真实身份、路径、冻结/结果完整性和不可消费产物。

## 11. 开发顺序与文件清单

| 步骤 | 主要改动 | 本步退出条件 |
| --- | --- | --- |
| A | profile、AGENTS、聚焦 rubric、Graph 实际输入选择、角色职责 | 新任务确实只加载一期方法；旧任务未被改写 |
| B | OpenCode 平铺 write/supersede，修正文案 | 一条普通用例通过真实 CLI 原样保存，精确替换有效 |
| C | compact 读取、全局 comparison 分页、紧凑 JSON | 结果/导航大页不截断，全文可还原，旧 CLI 兼容 |
| D | 删除两轮固定复读及新增记录要求 | 正常完成一次宿主派发即可 settle，真实错误仍同 worker 恢复 |
| E | 用例优先报告和本期质量范围 | 新旧正文均可读；报告写失败不产生假完成 |
| F | 定向回归、真实模型和用例可执行性验收 | 达到第 12 节全部声明范围，不用单元测试替代实跑 |

建议串行完成 A、B 后再继续。可以并行开发 C 与规则文本，但 `.opencode/plugins/pangea.ts`、`graph/nodes/source_first.py` 每个文件同一时间只安排一个写入者，集成者负责核对实际合并结果。

主要文件：

```text
AGENTS.md
src/pangea_agent/models/contract.py
schemas/task_contract.schema.json
src/pangea_agent/cli/run_module_analysis.py
src/pangea_agent/graph/nodes/source_first.py
src/pangea_agent/rubrics/builtin/behavior_test_generation.md       新增
.opencode/plugins/pangea.ts
.opencode/agents/analysis-worker.md
.opencode/agents/review-worker.md
.opencode/agents/planning-worker.md
.opencode/agents/pangea-agent.md
.opencode/commands/module-analysis.md
.agents/pangea/analysis-worker.md
.agents/pangea/review-worker.md
.agents/pangea/planning-worker.md
.agents/pangea/closure-worker.md
.agents/pangea/dsh.md
src/pangea_agent/graph/result_store.py
src/pangea_agent/cli/source_first_api.py
src/pangea_agent/cli/main.py
src/pangea_agent/inventory/source_access.py
src/pangea_agent/models/source_first.py
src/pangea_agent/report/source_first.py
```

`adapter_api.py` 只在兼容已有 finalization 续接字段确有需要时调整；报告完成事务本轮以保留和回归为主。修改资产匹配、用户源码展开、旧 rich schema 或其他仓库前，必须说明实际新发现的必要性。

## 12. 验收方案

### Level 1：先验证这次最直接的功能

下面的格式检查是开发测试，不加入真实 Run 的新语义门禁。

| 检查 | 必须看到的结果 |
| --- | --- |
| 普通流程用例，没有 Risk | 能原样保存、审核、生成报告 |
| 新任务输入 | profile 正确；未自动加载旧五 Pass/DFX/专项风险规则 |
| 平铺工具 schema | 实际注册的工具中 body 是必需 string，普通写入没有 records 数组/任意 JSON body |
| 中文、换行、引号、Markdown、JSON 字样 | 通过真实 CLI 保存后正文相同，非模拟存储 |
| 修改一条用例 | 只退休指定旧记录，保留首轮和历史；其他记录不动 |
| 完成声明 | Analysis、Comparison 正常完成后都没有额外固定复读 prompt |
| 首次派发及恢复 prompt | 新 profile 允许重要用例遗漏 finding；平铺参数一致；有效正文不因旧 finalization 标记被迫改写 |
| 真实局部错误 | 原 task/result 精确恢复；空结果、SDK 截断不会被当作完成 |
| 报告 | 用例在前，状态与范围正确，三个正式产物齐备后才 complete |

工具验证至少有两层：实际依赖生成的工具 JSON Schema，以及真实 CLI/临时绑定结果的集成保存。现有 `tests/opencode_plugin.test.mts` 直接调用 execute 并模拟 CLI，单独通过不能覆盖上述第二层。

如果报告声称验证了 OpenCode 的实际 provider 请求转换，应通过本地固定响应测试端捕获真实客户端发出的 tools schema，并回放合法调用；若未做该项，就明确只验证了注册 schema 和 CLI 集成，不扩大结论。不要为此开发通用模型网关。

### Level 2：相关回归

保留并调整现有本地测试：

```text
tests/opencode_plugin.test.mts
tests/test_opencode_host_continuation.py
tests/test_source_first_correction_routing.py
tests/test_source_first_store_integrity.py
```

新增 focused 测试覆盖：

- 旧 JSON body、旧无 profile 合同和旧数字 cursor 调用仍可读取；无 profile 的旧任务续接仍遵循原冻结范围，不套入一期质量结论。
- 新分页 current/history、长单记录、长 warning、中文/emoji/转义、恰好页边界、多 action 同号记录。
- 相同游标重读相同页；写入使游标过期但不产生语义返修；全部片段还原无丢失。
- 长记录读取中途 supersede、已读页之前的记录被退休时，不跨版本拼接或跳项；Comparison 不自动切到新版本。
- 多单元 comparison 总回包有界；冻结版本和单元筛选不混淆。
- 头文件声明、raw 解析区、长宏、条件编译和完整搜索命中仍可访问。
- 一条超过 24000 字符的源码行，在新视图中每页有界且可完整还原；旧 CLI 的行游标和请求范围不变。
- 非致命 evidence/关系提示保留原值；身份错、越界、损坏产物仍按原边界处理。
- 同 Reviewer 续接、显式修正选择、同原 worker closure 和首轮不变。
- 历史 finalization 标记能读，正常完成不再被要求新增空洞 summary。
- 宿主内部 compact 读取的版本、计数和完成判断正确；首次派发、正常结束、局部恢复均无字段 shape 错误。
- 首轮与已接受 closure 的用例不重复计数；未完成 closure 不覆盖当前交付，原始证据仍完整可见。
- report 写失败、结果不可读时不保存 complete。

命令逐条执行。macOS 当前工作树可使用 `.venv/bin/python`；Windows 使用对应 `.venv/Scripts/python.exe` 或已安装的 `python`，不要复制 POSIX 激活命令。

```text
python -m unittest discover -s tests -p 'test_source_first_store_integrity.py' -v
python -m unittest discover -s tests -p 'test_source_first_correction_routing.py' -v
python -m unittest discover -s tests -p 'test_opencode_host_continuation.py' -v
node --test tests/opencode_plugin.test.mts
git diff --check
```

新增测试使用实际创建的文件名执行，并在汇报中列出。某项失败先处理该问题，不通过扩大测试数量替代修复。

### Level 3：真实 OpenCode 验收

使用新进程、新 Run，模型为已配置的 `minimax-cn-coding-plan/MiniMax-M3`。确认实际加载开发工作树插件，不附着旧服务，不输出 token/密钥。不要手工往真实 worker 的结果里补正文。

#### 样本 A：小型业务模块 + 实测 Coverage

在独立本地验收数据根中准备一个有公开入口的小型 C/C++ 或 Lua 样本，包含：正常业务、一个业务选项、中途失败、错误向上传播、清理和再次操作。源码、明确的接口约定及测试驱动一起留存，不能靠 evaluator 心中默认行为判定预期。

先实际执行一组基线测试，采集执行计数，留下至少一个未执行函数及一个未执行分支结果。通过现有 Coverage XLSX 导入路径提供给 PANGEA；不能手填几个零冒充实际采集。

现有 XLSX 格式可用分开的 sheet：

- 函数：`module, path, function, count`。
- 分支：`path, branch_id, function, condition, true_count, false_count`。

保存原始采集报告、基线源码版本、采集命令及到 XLSX 的机械映射。测试专用转换器只做格式转换，不开发新的生产采集能力。若环境暂时不能实际采集，可以完成合成 fixture 的接口测试，但样本 A 的真实 Coverage 验收仍记未完成。

现有导入命令如下，路径和 ID 用实际值替换，逐条执行：

```text
python -m pangea_agent.cli.main assets import --data-root '<独立验收数据根>' --path '<实测coverage.xlsx>' --type coverage
python -m pangea_agent.cli.main assets extract --data-root '<独立验收数据根>' --asset-id '<上一步返回ID>'
```

从真实 Agent 发起分析，`asset_ids` 使用实际返回编号，request 明确一期业务用例目标和 250K 预算。主 Agent 通过返回 action 执行完整流程。

核对生成用例后，由验收执行者实际操作本样本中的关键正常、异常、恢复和 Coverage 补测用例；记录执行方式及结果，再采集 Coverage。可以制作仅用于验收的测试驱动，但不得修改被分析模块来适配生成用例，也不得预设错误后果作为故障注入条件。

至少证明：未覆盖目标通过支持入口被实际触达；false 缺口不是只执行 true；用例确实检查结果而不只是调用函数。测试人员的可执行性核对不能仅由生成该用例的 worker 自我声明。

这里的用例验收通过，指步骤可执行且能正确判别结果，不要求被测模块的所有断言成功。用例真实揭露产品缺陷时，记录被测产品失败，不直接认定 PANGEA 生成失败，也不得修改有依据的预期使产品通过。

#### 样本 B：真实 SPDK 模块，无 Coverage

继续使用 Run18 主责模块 `lib/nvme/nvme_auth.c` 及其当时冻结源码版本：`97af299e3c76368219f0cddcc710fafd57edcc1c`。

在新 Run 中冻结同版本，核对字节一致；不要对用户源码执行 pull/reset/checkout。若当前源目录已变化，使用明确指定的只读冻结副本或报告条件变化，不能悄悄换题。

请求聚焦认证正常主干、业务选项、错误反馈、异常传播、清理及再次认证。不给模型提供 Run18 的答案、用例或已知缺陷结论，不要求固定用例数量。

验收重点：

- 单向/双向等真实业务模式能形成有依据的测试。
- 核实等待/发送/校验中的错误应传播、转换还是经恢复消除，分别说明源码实际结果与有依据的预期，不能遗漏未经处理的错误丢失。
- 失败后重试和资源恢复有实际行为推导。
- 共享 case 不能抹掉不同前驱和错误值带来的不同结果。
- 公开入口、条件编译、cleanup 和跨次状态事实准确。
- 没有 Coverage 时不伪造未覆盖 ID；缺故障注入环境时不冒充已执行。
- 独立 Reviewer 能核实这些行为与用例，并完成一次正式对照。

本样本验证真实模块的用例生成和 PANGEA 全链路。没有真实设备/注入设施时，不声称 SPDK 的这些测试已经执行通过；可执行性结论限定为已经核实的环境和入口条件。

#### 重复与失败规则

样本 A、B 都通过后，重复一次 B 的同范围新 Run 检查是否依靠偶然结果。最低为两个不同样本加一个重复样本，不要求跑十次。

任一关键失败先保留现场、定位并修复，再重新验证受影响样本；不要通过反复新建 Run 挑一个成功结果。所有已经启动的正式样本都进入验收台账。

可以有一次具体、有效的原 worker 定向修正；不能依赖同一格式错误持续重发或多轮整份重写过关。故意注入错误验证恢复的本地 fixture，与正常真实验收分开计数。

### 12.4 独立审核必须真的能指出用例问题

用本地隔离夹具构造一条错误预期、缺失恢复步骤或错误 Coverage 关联，验证 Reviewer 的 comparison 能指出具体结果记录，并选择原 worker 修正。错误夹具不写进真实样本 A/B 的正式产物，不通过篡改真实运行结果制造“发现问题”的证据。

另覆盖一条重要业务用例整体缺失：Reviewer 绑定真实 unit 并说明缺项及依据，能派回原 worker 补齐，不要求指向尚不存在的用例 record_id。

同时保留正确用例的对照：没有产品风险也可以接受；正常错误处理不能被迫升级成产品缺陷；无新 finding 的盲审可以保存真实 summary 后完成。

### 12.5 250K 与停止规则

250K 按一次模型请求的总上下文预算处理，不按整个 Run 的累计计费 token 处理。台账同时记录 `input + cache.read + cache.write` 与输出；缓存输入也占上下文。

开始前确认该模型本次输出预留。例如总预算 250000、实际输出预留 32000，则输入工作线是 218000；32000 必须以本次配置为准，不能凭旧记录固定假定。

当前插件只在整个 session.prompt 返回后取得 token，尚未证明具备逐步控制接口。本期不开发新 watchdog 或 Python token 失败门禁。验收执行者用现有可观测轨迹记录实际输入；若现有宿主提供可核实的配置或压缩能力，可以按其真实能力使用并记录，不得声称未验证的限制已生效。

观察到预算越线仍在工作、反复同一工具错误无有效进展、关键回包被截断且无法完整取得时，停止本次验收并保留当前 Run。停止本身不是通过；通过条件是任务真正完成且实际上下文满足声明预算。没有逐请求用量证据时，不能宣布 250K 验收通过。

不要用固定运行分钟数、固定记录数或风险数让 Python 判定语义失败。

### 12.6 每个真实 Run 的台账

只记录用于判定本期结果的信息，不建设新的产品监控系统：

```text
Run ID、实际工作树/代码基线、客户端/模型、根会话与Graph绑定task
冻结源码版本、任务范围、实际Coverage输入及采集来源
Planning/Analysis/盲审/Comparison/Closure是否执行、完成状态
首轮版本、被接受版本、正式报告路径
工具错误（包括completed状态里的invalid调用）、输出/回包截断
是否有重复同错、无内容替换、额外固定复读、原worker局部修正
各会话最大实际上下文、输出预留、是否满足250K
生成用例的质量核对、实际执行过哪些、Coverage是否有实测改善
通过/未通过/未验证及具体原因
```

Graph 的 `validation_failures=0` 不能代替工具错误和宿主轨迹检查。不能把启动成功、已有部分用例或单独出现 report 文件当作全链路通过。

## 13. 完成判据与 Sol 最终汇报

满足以下条件，才可交付“本期实现及 OpenCode 验收完成”：

1. 新一期规则真的进入冻结 task，正常、异常和 Coverage 用例无需 Risk 前置。
2. 新普通提交路径不再要求模型组装多层 records；原文及精确修正完整。
3. 新读取视图不靠宿主截断省上下文，全部有效内容和所需历史可完整访问。
4. 正常完成没有两轮固定复读；必要修正仍由同 Reviewer/原 worker 完成。
5. 报告用例优先，生成、执行、覆盖改善与专项风险范围分别如实说明。
6. 小样本的真实 Coverage 补测和实际用例操作通过；真实 SPDK 生成链路及重复样本达到本期质量与预算要求。
7. 旧结果和当前 DSH CLI 兼容验证通过；未开展的 DSH/内网真实验收明确列出。

最终只需向用户说明：做了什么、改了哪里、哪些真实样本通过、还有什么未验证或未解决。附改动清单、测试命令和逐 Run 台账，不输出几十屏日志。

如果只有本地测试通过，写“实现完成，本地验证通过，真实验收未完成”；真实 Run 仍有关键问题就写未通过，不以新规则或缩小统计口径包装成成功。

## 14. 可直接交给 Sol High 的执行提示

> 请在 `/Volumes/Media/pangea-agent-source-first-v1` 按 `PANGEA_BEHAVIOR_TEST_FIRST_V1_PLAN.md` 执行开发和分层验收。先完整读取必读 Skill、AGENTS 和方案，核实当前分支及未提交基线。按 A→F 顺序完成，以业务行为用例为一期交付目标，同步简化 OpenCode 正文提交、减少结果/索引回包、删除完成后两轮固定复读。保留冻结输入、真实身份、原 worker 修正、旧 notes 和 DSH 现有 CLI 兼容。不要改用户源码、历史 Run 或其他仓库，不自动提交/推送。先验证普通用例真实落盘，再做相关回归和方案指定的真实样本；保留所有启动样本的台账。遇到需要扩大范围的实际冲突，报告具体文件、原因和选择，不能自行新增语义门禁或恢复框架。
