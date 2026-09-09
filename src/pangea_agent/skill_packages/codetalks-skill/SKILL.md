---
name: codetalks-skill
description: >
  源码驱动的黑盒测试分析 Skill。用于模块全量测试分析、问题单/代码修改回归、
  问题+日志+代码根因辅助定位及专项风险分析。模块分析采用五阶段，其他场景保留九步；通过开发实现讲解、
  多源场景增殖、SFMEA、黑盒转换和独立 Judge 形成可追溯交付。
metadata:
  version: "1.4.1"
  derived_from: codetalks-fused-v2.4
license: CC-BY-SA-4.0
allowed-tools: Read Search Grep Glob Bash Write Edit Agent AskUserQuestion
---

# Codetalks Skill 1.4.0

本文件只定义运行入口和全局约束。每个步骤的具体要求在 `steps/`，专项方法在
`references/`；必须按 JIT 顺序读取，不要在启动时一次性加载全部文件。

## 1. 目标与事实载体

Agent 必须先像熟悉模块的开发一样建立实现模型，再向黑盒测试人员交付可以执行、
可以观察、可以清理恢复的测试内容。

主事实载体是 `活文档/` 中的 Markdown。JSON 只用于运行状态、计划、输入材料索引、
方法论选择、独立审查状态和工作台投影，不得代替自然语言分析。

正式内容必须分两层：

- 开发实现讲解：函数与调用路径、状态、资源、判断、异常传播、源码证据和缺陷机理；
- 黑盒测试交付：外部接口、协议、配置、业务状态、注入方式、可见结果、Oracle 和恢复。

不得把函数清单、搜索命中、少量风险 Bucket、纯表格或 JSON 当成完整分析。

## 2. 输入与冻结副本

新建分析只接受 `request_version=2.0`，包含 `run_id`、`data_root`、`repository`、
`target`、`source_scope`、`asset_ids` 及可选的 `scenario`、`mode`。分析重点、手工
结构化资产 ID 和文本用例路径不属于新请求。

源码、资产、启用的方法论和本 Skill 已复制到当前 Run。分析只能读取运行请求中
给出的冻结目录，不得回读原仓库或原资产。源码清单只记录复制范围、路径和大小；
文件读取失败时报告具体路径和错误，不要搜索其他目录兜底。

语言 Profile 已由运行器根据用户范围识别：

- C/C++：使用默认路径；
- Lua：在相应步骤读取 `references/language-lua.md`；
- openUBMC Lua：同时读取 `references/openubmc-lua.md`；
- 同一范围同时包含 C/C++ 与 Lua：当前版本不支持，必须停止并明确说明。

## 3. 三条 Bootstrap 核心规则

在任何源码搜索和路径定位前，必须完整读取：

1. `references/path-fidelity.md`
2. `references/evidence-consumption.md`
3. `references/markdown-narrative-first.md`

核心含义：

- 路径、标识符、日志和协议字段按字面值保真，不能凭相似项纠正；
- 搜索、索引、覆盖率和调用图只产生候选，结论必须回到真实实现、接线或运行证据；
- 每份输入材料必须记录实际消费范围、事实、用途、未读范围和限制；
- Markdown 是分析主版本，表格只能用于索引、比较和追溯。

## 4. 深度型启动

深度型第一条执行命令必须是：

```bash
python3 {skill-root}/scripts/run_guard.py init \
  --skill-root "{skill-root}" \
  --workspace "{workspace_root}" \
  --source-raw "{source_root_raw}" \
  --source-verified "{source_root_verified}" \
  --output "{output_root}" \
  --scenario "{scenario}" \
  --mode depth
```

读取三份核心规则后，一次性确认：

```bash
python3 {skill-root}/scripts/run_guard.py ack-core \
  --workspace "{workspace_root}" \
  --all
```

未完成 `ack-core --all`，不得搜索源码或启动 Step 01。

速度型仍须执行广度盘点、深度分析、场景增殖和黑盒转换；发生截断、compact 丢失或
多条核心流程未持久化时，最高只能给出 `PARTIAL` 或 `INCONCLUSIVE`。

## 5. JIT 阶段与恢复

`module-analysis` 使用五阶段，speed/depth 不改变这五个阶段，也不能通过切换速度型绕过深度型复核：

1. `steps/module-01-input.md`：输入与范围。
2. `steps/module-02-inventory.md`：模块盘点。
3. `steps/module-03-flow.md`：每个流程连续完成讲解、分支推导、风险/SFMEA 和测试设计。
4. `steps/module-04-review.md`：复核与定向修订。
5. `steps/module-05-delivery.md`：发布已审内容。

模块分析不加载下面旧九步文件/覆盖门禁模板。其他场景维持下列顺序，由运行器选择 `legacy-workflow-manifest.json` 冻结为当前 Run 的 `workflow-manifest.json`；独立使用 Skill 时 init 根据 scenario 选择对应 manifest。

其他场景严格按顺序执行，每次只读取当前步骤文件：

1. `steps/01-intake-and-scope.md`
2. `steps/02-evidence-consumption.md`
3. `steps/03-breadth-inventory.md`
4. `steps/04-flow-deep-analysis.md`
5. `steps/05-scenario-expansion.md`
6. `steps/06-sfmea-blackbox-translation.md`
7. `steps/07-test-design.md`
8. `steps/08-independent-judge.md`
9. `steps/09-final-delivery.md`

开始步骤：

```bash
python3 {skill-root}/scripts/run_guard.py start-step \
  --workspace "{workspace_root}" \
  --step "01"
```

脚本返回 `only_load_step_file` 后读取该文件，完成要求的工件，再执行：

```bash
python3 {skill-root}/scripts/run_guard.py complete-step \
  --workspace "{workspace_root}" \
  --step "01"
```

步骤有可数业务项目时用 `progress` 登记真实进度。模块分析阶段 02–05（其他场景为 Step 03、04、05、07、08、09）
产生可消费结构化结果后，用 `publish-stage` 发布工作台投影。投影必须同时包含
`business_flows`、`risks`、`test_cases`、`evidence`、`review_issues` 五个数组；
未生成的数组使用空数组，不能让工作台从 Markdown 猜测。

不得跳步、提前读取后续步骤、手工修改运行状态、在缺少工件时直接进入 SFMEA 或用例。

## 6. 全局分析约束

### 6.1 开发给测试讲代码

每个 P0/P1 流程必须说明：用途、外部触发、运行时接线、前置状态、主路径、分支进入
条件、状态变化、资源申请与释放、超时重试恢复、并发窗口、异常传播、潜伏故障、
黑盒构造和观察、源码证据与未决项。

分支必须形成：

```text
外部可构造条件
→ 内部判断/状态机制
→ 外部可观察结果
```

### 6.2 场景与风险

模块阶段 03（其他场景 Step 05）读取 `references/scenario-expansion-engine.md` 和
`references/worker-judge-protocol.md` 的候选裁决部分。候选来自：分支、状态、资源与
不变量、数值/N/2N/翻转、并发、异常传播、需求/协议/安全/配置、覆盖率与历史证据。

每个候选保留契约、范围、传播、反事实和根因归并的判断。历史故障、设计意图和覆盖
缺口不能直接写成当前实现风险。风险必须说明触发条件、系统结果、残留影响、看似正常
的原因、外部暴露和黑盒证明；不按固定数量或固定负向比例凑用例。

### 6.3 黑盒用例

测试名称、目的、前置条件、操作、注入、预期结果、观测和恢复必须使用外部可执行语言。
内部函数、变量和源码行只放在开发讲解或追溯字段中。每条正式风险至少关联一个用例；
只有 Agent 基于冻结源码确认无法从受支持入口到达时可以例外，并记录原因与证据。

### 6.4 输入材料和 Coverage

需求、设计、日志、问题单、覆盖率和历史故障必须按允许步骤从冻结资产中实际解析。
覆盖率是输入之一，不是唯一真相；有覆盖率时映射到 Flow/Branch/State/Resource 和场景，
没有覆盖率时仍执行全部场景增殖。测试用例示例只作为格式和粒度参考，不得充当证据。

### 6.5 问题单回归

`issue-regression` 必须读取 `references/codehub-mr-access.md`。MR 链接缺失时先向用户索取；
有链接时必须使用 `codehub-mcp-server` 读取元数据、Commit、修改文件、Diff、评审和可访问
的检查结果。工具不可用时按该参考文件输出固定提示并标记 `BLOCKED`。用户提供完整离线
Diff 时可以继续，但必须明确证据边界，不能伪装成读取了完整 MR。

## 7. 目录与产物

`--workspace` 必须是 `<run_root>`。目录固定为：

```text
<run_root>/
├── 活文档/
├── 内部索引/
└── 正式输出/
```

非交付阶段只写 `活文档/` 和允许的 `内部索引/`。只有当前 manifest 的最后阶段可以写
`正式输出/`。不得创建嵌套的 `活文档/活文档/`，不得在运行根目录散落过程文件，
不得生成 `progress.json`、`final-state.json`、`agent-results/`、旧 `report.md` 或
`report.html`。

每个步骤的必需工件以当前 Run 的 manifest 和阶段文件为准。模块分析取消字数下限、固定章节和叙述长度门槛，按复杂度展开；不以篇幅证明质量。

模块活文档固定为：输入与范围、模块盘点、分析台账、风险点与SFMEA、黑盒测试用例、复核记录；另按实际流程维护 `流程讲解/流程-*.md`。台账合并分析覆盖与未决项，风险/用例通过稳定 ID 链接流程证据，不复制正文。JSON 保留现有六种内部索引用途。

## 8. Producer、Judge 与结论

模块阶段 03 完成后（其他场景 Step 07 后），深度型创建与 Producer 分离的真实 Judge 执行模块阶段 04（其他场景 Step 08）。
Judge 按 `references/worker-judge-protocol.md` 独立读取计划、活文档、冻结源码和证据，
主动寻找反例、保护条件、等价结果和遗漏。环境不支持独立执行时如实记录，不能宣称独立审查。

审查与交付必须分别报告：

- 流程状态；
- 交付完整性；
- 实际审查方式；
- Reviewer 的 `PASS` 或 `UNRESOLVED` 语义结论。

`READY` 只表示 run_guard 的流程与交付结构检查通过，不等于 Python 认可语义正确。
不得用篇幅、用例数、步骤通过或 `independent=true` 证明分析质量。

## 9. 恢复与最终交付

compact 或续跑时依次读取：运行状态、运行计划、输入材料索引、`活文档/任务交接.md`、
当前阶段工件、模块分析台账和当前相关源码。不得扫描历史 Run 猜测当前身份；任何 `in_progress` 项重新验证。若当前阶段已启动，直接继续其中的流程游标，不再次 start-step 重置进度；`init --resume` 保留状态、游标和成果。

模块阶段 05 只发布已审风险/用例原文到 `正式输出/风险点与SFMEA.md`、`正式输出/黑盒测试用例.md`，以及补充摘要、索引、限制的 `正式输出/完整分析报告.md`。报告链接 Run 内已审流程、台账和复核记录，不重写结论。

其他场景 Step 09 从已审查的活文档生成以下 UTF-8 Markdown：

1. `开发给测试讲代码.md`
2. `流程分支状态资源与异常传播.md`
3. `风险点与SFMEA.md`
4. `黑盒测试场景.md`
5. `黑盒测试流程.md`
6. `黑盒测试用例.md`
7. `覆盖审计与分析限制.md`
8. `完整分析报告.md`

正式返回前执行：

```bash
python3 {skill-root}/scripts/run_guard.py validate --workspace "{workspace_root}"
python3 {skill-root}/scripts/run_guard.py handoff --workspace "{workspace_root}"
python3 {skill-root}/scripts/run_guard.py finalize --workspace "{workspace_root}"
```

`finalize` 非零退出、`delivery_integrity.repair_required=true`、审查未完成或仍为
`UNRESOLVED` 时必须如实报告，不得新建 Run、删除有效内容或伪装成全部通过。

时间统一 UTC+8。历史 Run 只读展示自身冻结 manifest、步骤和产物，不迁移、不重编号、不用新 Skill 校验旧 Run。

业务流程工作台的主干/分支字段见 references/business-flow-projection.md，在阶段分析流程时按需读取并维护。
