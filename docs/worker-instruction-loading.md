# Worker 运行规则加载

新建 Run 只执行 Planning → Analysis → 报告，不派发 Reviewer、盲审、对照审核或 targeted_closure。分析完成后直接组装报告；结果未经过独立审核，不得把流程完成表述为审核通过。审核/定向修正实现与规则保留，但当前主链暂不调用；历史审核记录和冻结合同保持可读，不改写用户已冻结的数据。

运行规则按实际客户端入口加载；文件存在于源码仓库不代表会进入每次 worker 会话。

| 执行入口 | 加载链 | 结果写入 |
| --- | --- | --- |
| Desktop 外部 ACP（CodeAgent、NGA、OpenCode）/ Claude Code | Companion `source-first-acp.js` 创建通用会话 → workerPrompt → `docs/source-first-cli-worker.md` → task-open → task 中的冻结 rubric | 宿主提供的 Python CLI |
| 内部 DSH | Companion `report-policy.js` 按 role 读取 `.agents/pangea/*-worker.md` 并作为 persona 注入 → task-open → 冻结 rubric | 当前 DSH 的 pangea_* 工具 |
| OpenCode 原生插件 | `.opencode/agents/pangea-agent.md` → 对应角色 → task-open → 冻结 rubric | 当前 OpenCode 的 pangea_* 工具 |
| 独立 Claude 旧 Graph | `CLAUDE.md` → `.claude/protocols/action-lifecycle.md` → `.claude/agents/` 角色；Analysis/Closure 按需读取 `.claude/protocols/analysis-legacy.md` | 原 task 提供的唯一结果路径和冻结富结构合同 |

Desktop `src/main/pangea-product.ts` 将 `.agents`、`.opencode` 和 `docs/source-first-cli-worker.md` 从 runtime 复制到运行工作区。该过程不复制源码仓库的 AGENTS.md、CLAUDE.md 或 `.claude`。执行器是否另外加载用户工作区或用户级规则，应从该 provider 的实际会话记录确认；不能用这些文件的字节数代替实际 prompt 体积。

Desktop 仅在 source-first 外部 worker 请求中注入 `PANGEA_WORKER_TRANSPORT=desktop-cli`。OpenCode PANGEA 插件检测到此值时立即返回空注册对象，不注册任何 `pangea_*` 工具或 hooks；worker 通过执行器原生命令执行工具调用 Python CLI，唯一绑定来自 Companion 提供的 exact data_root/run_id/action_id/task_id。模型探测、资产任务与独立图任务不注入该标记。未标记的独立 OpenCode 继续使用原生插件派发和绑定，工具与 hooks 保持可用。

两条通道的会话绑定不能互换：Companion 的 CLI `adapter bind` 不会填充原生 OpenCode 插件的 session 绑定表。Desktop worker 遇到原生工具的未绑定错误时应直接使用已提供的 CLI，不能反复调用该工具、自动绑定或从历史任务猜测 action；CLI 本身的绑定错误仍交宿主检查，不修改 Graph 身份或语义合同。

`AGENTS.md` 保留开发约束和实现不变量。Desktop 外部 worker 的身份、分页、读写、同一结果修复、路径及源码保护规则集中在现有 CLI 合同中；其他客户端保持自己的命令和权限，不调用其他客户端的角色文件。`.claude` 的 Read/Write 旧角色不提供 Desktop CLI 执行通道，不扩大该角色权限来模拟外部通用会话。

## Profile 与冻结版本

behavior-test-v2 的 Analysis 读取场景共同规则、scene_flow、当前专项及 Planning 为本单元选定的方法论。新建 Run 不派发 Reviewer 或加载审核 rubric。选定的内置和用户方法论均保留；不自动向四场景注入整套旧 DFX。旧 profile 按该 task 已冻结的规则执行。

flow 的源码契约仅维护 `src/pangea_agent/rubrics/builtin/scene_flow.md` 中的 FLOW-CONTRACT 段。三份 analysis 角色只保存指向 task 冻结输入的指针；`tools/sync_worker_prompts.py` 更新或检查这些指针，不再内联契约。behavior-test-v1 或更早 task 未提供 scene_flow 时，继续使用原冻结生成 rubric 和结果合同。

修改运行规则后先验证同步检查、方法论派发及客户端权限/协议回归；实际模型是否遵从规则仍需真实 provider Run 验收。纯合成记录测试只证明派发和消费合同可用，不代表模型分析质量已通过。

## 业务场景与用例粒度

新建 behavior-test-v2 Run 使用共同生成规则中的场景提炼与拆合方法：结合注释、上下文及状态来源解释业务情形，按业务或公开接口验证目标组织用例。scene_branch 负责有意义的行为发现，Analysis 同时核对过度拆分、错误合并和不可执行表达。流程路径与用例不要求一对一；三种图仍由宿主根据原 Worker 的结构化事实渲染。

角色文件和 Desktop CLI 合同只指向当前 task 的冻结规则，不额外要求内部路径逐条成例。OpenCode 辅助 Skill 按 task 实际提供的 profile/rubric 读取，不要求 v2 加载未提供的 v1 规则；协议字段、FIN/RST、抓包及已有注入设施可用于真实刺激和观察。旧 Run 继续使用其原 rubric 快照，不改写历史冻结合同或记录；新运行仅规划、分析、报告。

`python verification/branch_behavior_contract.py` 覆盖安装规则变化后的真实冻结与派发：旧 Run 字节保持不变，新 Run 读取新快照，必读生成规则不依赖可选方法论。语义验收样例见 [branch-behavior-acceptance.md](branch-behavior-acceptance.md)，其中真实 provider 验收与机械验证分开记录。

## risk-method-v1

新 risk-analysis Run 冻结语言风险方法、风险信号与 risk/check 合同；旧 Run 继续使用冻结内容。按契约闭包规划、六镜头逐对象反证、检查账本；不派发审核模型，analysis 最多三个并发。风险用例不设黑盒比例目标，库/API 按接口层级如实交付。G1–G4 根据用户 2026-10-15 决定只提示 warning，Python 不判断风险真假。

后续 WP12：编译器/sanitizer 能力冻结与最小驱动复现，本期仅记录设计，另行确认实施。

## 首条用例首次交付

新冻结的 scene_common_generation 在 FIRST-CASE-DELIVERY 段规定同一 Analysis worker 的逐例首写责任。
ACP 初始、原会话续接、压缩恢复及修复共用该冻结规则引用；CLI 合同与三个客户端 Analysis 角色同步指向它。
旧 Run 缺此段时不追读安装目录新版、不改旧记录。compact_task_view 优先交付小体积设置、目标、职责及工具索引；
超量内容仍按 deferred_fields/input-read 分页，不由 Python 摘要或筛选语义。

验证命令：`python tools/sync_worker_prompts.py --check`、`python verification/first_case_delivery.py`、
`python verification/blackbox_semantic_examples.py`。Companion 使用 `node --test tests/source-first-acp.test.mjs`。
首写回放覆盖 Analysis 当回合首次落盘、同 action 流式续写、公开 API、unknown、缺设施、错误凭据冒充精确协议故障的正反例。
反例仍允许结构写入，不新增正则语义门禁；这只证明提示装载、分页和持久化合同，不证明模型质量。

真实 provider 首轮验收尚未执行：给 Analysis 未标注场景与冻结证据，不提供 expected_review/正例答案，
分别记录初始、同 action 续接、上下文压缩和机械修复后的首次 test_case result-write 载荷与落盘版本。
在 Analysis 首次交付时检查业务目标、具体参数、精确触发依据、逐步外部判据、恢复与准备度；合法公开 API/协议字段不得误删，
工具不足不得虚构命令或伪 ready。保存首写版本作为验收对象，不能以事后修正结果替代。
