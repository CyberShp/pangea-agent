# Worker 运行规则加载

运行规则按实际客户端入口加载；文件存在于源码仓库不代表会进入每次 worker 会话。

| 执行入口 | 加载链 | 结果写入 |
| --- | --- | --- |
| Desktop 外部 ACP / Claude Code | Companion `source-first-acp.js` 创建通用会话 → workerPrompt → `docs/source-first-cli-worker.md` → task-open → task 中的冻结 rubric | 宿主提供的 Python CLI |
| 内部 DSH | Companion `report-policy.js` 按 role 读取 `.agents/pangea/*-worker.md` 并作为 persona 注入 → task-open → 冻结 rubric | 当前 DSH 的 pangea_* 工具 |
| OpenCode 原生插件 | `.opencode/agents/pangea-agent.md` → 对应角色 → task-open → 冻结 rubric | 当前 OpenCode 的 pangea_* 工具 |
| 独立 Claude 旧 Graph | `CLAUDE.md` → `.claude/protocols/action-lifecycle.md` → `.claude/agents/` 角色；Analysis/Closure 按需读取 `.claude/protocols/analysis-legacy.md` | 原 task 提供的唯一结果路径和冻结富结构合同 |

Desktop `src/main/pangea-product.ts` 将 `.agents`、`.opencode` 和 `docs/source-first-cli-worker.md` 从 runtime 复制到运行工作区。该过程不复制源码仓库的 AGENTS.md、CLAUDE.md 或 `.claude`。执行器是否另外加载用户工作区或用户级规则，应从该 provider 的实际会话记录确认；不能用这些文件的字节数代替实际 prompt 体积。

`AGENTS.md` 保留开发约束和实现不变量。Desktop 外部 worker 的身份、分页、读写、同一结果修复、路径及源码保护规则集中在现有 CLI 合同中；其他客户端保持自己的命令和权限，不调用其他客户端的角色文件。`.claude` 的 Read/Write 旧角色不提供 Desktop CLI 执行通道，不扩大该角色权限来模拟外部通用会话。

## Profile 与冻结版本

behavior-test-v2 的 Analysis 读取场景共同规则、scene_flow、当前专项及 Planning 为本单元选定的方法论。Reviewer 读取复核共同规则、scene_flow、当前专项及各单元选项的合集。选定的内置和用户方法论均保留；不自动向四场景注入整套旧 DFX。旧 profile 按该 task 已冻结的规则执行。

flow 的源码契约仅维护 `src/pangea_agent/rubrics/builtin/scene_flow.md` 中的 FLOW-CONTRACT 段。三份 analysis 角色只保存指向 task 冻结输入的指针；`tools/sync_worker_prompts.py` 更新或检查这些指针，不再内联契约。behavior-test-v1 或更早 task 未提供 scene_flow 时，继续使用原冻结生成 rubric 和结果合同。

修改运行规则后先验证同步检查、方法论派发及客户端权限/协议回归；实际模型是否遵从规则仍需真实 provider Run 验收。纯合成记录测试只证明派发和消费合同可用，不代表模型分析质量已通过。

## risk-method-v1

新 risk-analysis Run 冻结语言风险方法、风险信号与 risk/check/review_check 合同；旧 Run 继续使用冻结内容。按契约闭包规划、六镜头逐对象反证、检查账本、盲审探针与对照复核工作；不增加 Agent 或复核轮次，analysis 最多三个并发。风险用例不设黑盒比例目标，库/API 按接口层级如实交付。G1–G4 根据用户 2026-10-15 决定只提示 warning，Python 不判断风险真假。

后续 WP12：编译器/sanitizer 能力冻结与最小驱动复现，本期仅记录设计，另行确认实施。
