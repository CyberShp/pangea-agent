# PANGEA 执行提速 V1 开发方案

日期：2026-09-09。状态：范围内实现与约定验收已执行完毕；整体质量验收未通过，结果见第 9 节。

## 1. 目标与已验证依据

在保持完整源码分析、独立盲审、同 Reviewer 对照和原 worker 定向修正的前提下，减少编号搬运、源码回包重复字段和宿主无进展等待。由 Agent 决定语义，Python 负责明确选择的确定性展开、保存和路由。

基线：`m27-nvme-auth-baseline-260909-01`；OpenCode 1.18.4；`minimax-cn-coding-plan/MiniMax-M2.7` 普通版；204800 上下文预算；SPDK `97af299e3c76368219f0cddcc710fafd57edcc1c`；完整 `lib/nvme/nvme_auth.c`，1300 行。

| 已验证事实 | 含义与本次处理 |
|---|---|
| 总耗时 40:59，含一次 10:41 未完成模型请求 | 必须单列模型请求等待与恢复；不能换算为内网 DeepSeek 耗时 |
| Planning 6:18，32 个 region 编号提交 8 版；首次未就绪到成功间隔 4:31 | 整文件归属应让 Planner 提交文件选择，减少长编号列表生成 |
| Analysis 源码正文 46,816 字符，回包 139,410 字符 | 压缩展示包装，保留逐行原文、坐标和分页完整性 |
| worker 工具实际执行合计 74 秒，约占总墙钟 3% | 优先减少模型输入输出和无进展等待；没有依据重构整个 Python 调度 |
| 进程中断后 dispatched action 不能直接 dispatch/retry；已有 defer 可恢复 | 补齐宿主登记入口，复用原 action/task/result_path |
| Closure 把 F-003 的记录替换成 F-002，随后又替换原 F-002 | 定向修正先核对实际目标，保存回执提供可读身份，防止返修引入丢失 |

最终 Run 为 complete / UNRESOLVED。这个样本包含质量错误，不能作为“质量通过、耗时约 41 分钟”的基线。
完整证据见 [基线报告](/Users/shepard/.codex/outputs/pangea-m27-baseline-20260909-01/BASELINE_REPORT.md)。

## 2. 工作区与交付顺序

1. **pangea-agent**：在 `/Volumes/Media/pangea-agent` 的 `codex/pangea-semantic-analysis-rework` 上开发，基线 `a8ac748`。依次完成 P0、P1、P2、P3，每步保留可单独回退的提交范围。
2. **DSH 工具适配**：以 `/Volumes/Media/dsh-pangea-source-first-v1` 中实际 source-first companion 为依据。其分支为 `codex/source-first-v1-dsh`，当前存在未提交修改。开发前记录该现场、逐块区分已有改动；仅提交本任务差异，不 reset/stash/整体覆盖，也不把旧 `dsh-pangea` 插件当成 source-first 入口。
3. **PANGEA Desktop**：通过现有 runtime/staging 与 launch-root 同步机制加载本次 Agent 和插件版本，再验收 Desktop → DSH → OpenCode ACP 路径。预计不改页面、数据库或 Desktop 架构。

Desktop 的 `src/main/pangea-product.ts` 已负责将 runtime 的 `.agents`、`.opencode` 同步到 launch-root。开发验收必须核对实际加载副本；不把手工修改 launch-root 副本当成源码交付。

## 3. P0：定向替换先核对目标身份

### 行为

原 worker 读取 correction_records 后，仅针对本次准备退休的 `record_id` 调用现有 result_read，核对原正文；若跨页则读完该记录。从返回的记录对象取编号，核对自己要修改的流程/用例，再提交替换。

- 不从 finding 编号、TC 显示编号或记忆推算 `rec-...`。
- 同一原记录关联多个修正点，合并为一次直接 replacement；确有引用变化才级联。
- 已经核对过且 revision 未变的记录可使用当前会话已读原文，不要求重复读取。
- 提交后依据保存回执核对“退休了哪条、产生了哪条”；发现错目标交回同一 worker 修正同一 closure 文件。保留首轮原文件。

现有 supersede 回执补充 `retired_records` 与 `created_records` 的轻量身份信息：真实 record_id、kind，以及正文中已经明确存在的 flow_id/case_id/title。正文是普通文本或没有这些字段时，只显示可直接取得的字段，不从文本提取或猜测编号。
这是复制 Agent 已声明字段供其核对，不据此判断替换是否正确，也不要求新语义字段。

DSH 的现有 result-write 接受 records 与 supersedes；同样在保存结果中返回身份回执。OpenCode 保持现有 result_supersede 入口，两个客户端规则各写各的工具用法。

### 修改位置

- `src/pangea_agent/graph/result_store.py`：保存回执，原始 record/body 保持原样。
- `.opencode/agents/analysis-worker.md`、`.opencode/plugins/pangea.ts`：closure 提示与回执核对。
- `.agents/pangea/analysis-worker.md`：DSH 客户端的定向读取与替换规则。

### 验证

- 用固定 F-001/F-002/F-003 结果夹具，原 Agent 指定替换 F-002：回执准确，F-001/F-003 仍有效，首轮文件不变。
- 一条替换多条旧记录、普通文本 body、未声明显示编号等现有合法结果仍可保存。
- 故意把 F-003 替换成 F-002：保存回执必须如实显示实际变化，机械层不自动纠正、不按显示编号否决；在同 worker 的修正场景中验证恢复原流程并继续。
- 真实 Run 验收阅读最终有效流程和替换链，确认未要求修改的流程仍存在。只看记录数相同不算通过。

P0 的目标是降低修正歧义；它不能保证模型永不选错。若真实运行仍选错，本阶段不宣称质量问题已解决，也不追加 Python 语义门禁。

## 4. P1：整文件规划提交文件选择

### 工具输入

保留当前 Planning 工具名称。每个 unit 的归属选择二选一：

```json
{
  "title": "NVMe initiator 认证",
  "purpose": "由 Planner 根据本次任务和冻结源码写明职责及必要上下文",
  "owned_files": [{"repo_id": "spdk-full", "path": "lib/nvme/nvme_auth.c"}],
  "context_files": []
}
```

需要在文件内按独立生命周期拆分时，仍由 Planner 显式提交现有 `owned_regions`。同一个 unit 不混填两种选择；更新 unit 沿用当前真实 unit_id。多文件组成一个 unit 时由 Planner 列出每个文件。

### 确定性处理

1. 文件必须是当前 task 的 owned_scope_paths 和冻结清单中的精确条目；reference/context 文件不会变成 owned。
2. 复用当前规划责任清单规则，展开该文件的 function/global region；没有此类坐标时沿用已有责任坐标规则，避免额外发明划分标准。
3. 保存 Planner 的文件选择原文作为原始计划。现有派生 `inputs/source-first-plan.json` 与正式 analysis task 中保存展开后的 canonical owned_regions；不是让 Python 改写 purpose 或决定归属。
4. 诊断与 Graph 建任务使用同一展开函数和同一冻结索引，避免各自解释一遍。沿用现有唯一结果文件及版本机制。
5. Planning 首轮整文件场景先看文件目录和必要入口窄片段，无须为抄写编号枚举全部 region 页。

### 失败、误拦与权限

- 当前已验证失败：7 次保存 ready=false，包含未知/漏填编号及改正后再次漏填；现有规则已经要求完整编号，单纯重复该要求不能消除搬运负担。
- 合法局部分割继续走现有 region 选择。对混填选择、普通未知文件名、归属重复或漏配，保留已保存的可读计划并返回精确诊断，交同一 Planner 局部改正；不清空其他单元、不判 Run 失败。
- 路径越出受控边界、绑定不成立或冻结输入损坏，仍按现有真实边界处理。
- Python 获得的是“Agent 明确选中的冻结文件 → 既有责任坐标”的展开权限；不获得分单元、选 context、补分析内容、评判可达性或判定语义覆盖质量的权限。
- 只减少 OpenCode/DSH 提交负担，不能靠限制客户端读取权限替代这个映射，也不把文件大小当成自动拆分依据。

### 修改位置

- `src/pangea_agent/inventory/source_access.py`：共用的冻结选择展开。
- `src/pangea_agent/cli/source_first_api.py`：plan_write 接收及诊断。
- `src/pangea_agent/graph/nodes/source_first.py`：有效计划读取与 analysis task 构建。
- `.opencode/plugins/pangea.ts`、`.opencode/agents/planning-worker.md`、`.agents/pangea/planning-worker.md`。
- DSH companion `src/index.js`：现有 pangea_plan_write 参数接收 owned_files；生成文件按项目现有构建方式更新。

### 验证

同一冻结样本的整文件选择一次保存就 ready；得到与旧正确规划完全相同的 32 个责任编号。另测合法局部分割、多文件归属、只有 reference 权限的文件、混填选择、重复归属及同 Planner 修正后继续。新旧合同都不改变 source scope 和原始语义内容。

## 5. P2：OpenCode 源码回包改为有界文本

### 返回方式

在现有 source_read 中增加明确的 `view=text`。复用 legacy 的带行号文本展示、compact 的字符预算及绑定 token 机制；新 OpenCode 调用选用 text，现有 legacy/compact 读取保留给已部署客户端和进行中会话。

```json
{
  "format_version": "pangea-source-read-text-v1",
  "repo_id": "spdk-full",
  "path": "lib/nvme/nvme_auth.c",
  "line_start": 1085,
  "line_end": 1095,
  "text": "1085: ...\n1086: ...",
  "evidence_handle": "当前实际返回范围的引用",
  "next_page_token": null
}
```

- 完整返回仍包含当前 binding。正常页按行边界结束；超长单行保留行号与字符片段位置，完整拼接后才是该行，不能截断后冒充整行。
- 默认字符预算仍为 12000，上限沿用 24000。本步压缩重复键，不通过增大单次回包掩盖问题。
- token 绑定当前 action/task、文件/region、原始范围和读取格式；续读沿用原始范围，不要求模型自行计算下一行。旧格式 token 不能误用于新格式。
- evidence_handle 描述实际返回行范围；含单行片段时明确标识片段，不声称已读整行。
- 原文中的反斜线、Tab、引号、Unicode、空行及不同换行输入按现有源码读取语义保留。验证“内容与坐标等价”，不宣称保留源文件原始字节级换行编码。

DSH companion 当前 source-read 走 legacy 带行号文本，已不是逐行 JSON 对象。本步不强制改它的分页协议；Desktop 的 OpenCode ACP 路径通过打包的新 `.opencode` 插件获得优化。

### 修改位置与验证

- `src/pangea_agent/inventory/source_access.py`、必要的既有 token 辅助函数、`cli/main.py`/`source_first_api.py` 中 view 透传与参数选择。
- `.opencode/plugins/pangea.ts` 和该客户端关于 source_read 翻页的规则；其余结果/Comparison 分页协议不扩改。
- 逐页重建整个 1300 行样本，并与冻结源按现有读入规则逐行比较；测试精确范围、region 读取、空行、超长行、末页和跨身份 token。
- 对基线记录的相同读取选择，模型可见回包字符量目标至少下降 40%，同时无漏行、重复交付错误或证据范围失真。该指标是展示开销目标，不是承诺总时间下降 40%。

## 6. P3：宿主无进展等待与中断恢复

### 正常进程中的等待

OpenCode 1.18.4 当前 SDK 已有 Plugin event 钩子、message.part.updated（含可选 delta）、session.status 和 session.abort。先验证这些事件能覆盖真实正文、思考和工具参数增量；“会话 still busy”不计进展。

拟定默认行为：

1. 每个已绑定的 worker prompt 记录最近真实进展。工具正在执行时计入工具阶段，不以模型输出静默误判。
2. 300 秒无真实进展时通过宿主进度事件展示当前等待原因；不提前结束 dispatch，也不判语义失败。
3. 600 秒无真实进展、且已确认该客户端能可靠观察输出进展时，通过 session.abort 取消这个 worker 回合。持续收到输出的长回合不因总时长触发。
4. 等宿主确认该回合已停止，再用现有 adapter defer 写入同一 action/task 的未完成原因；不让旧请求仍运行时再次派发。
5. 复用当前单次 recoveryUsed 预算，只续接同一 worker 一次。再次无进展则保存 attention 状态并停止自动尝试，不在 Python 中判 Run 失败。
6. 取消未确认、仍可能存在旧请求时，只提示宿主处理，不能抢先恢复并造成并发写入。用户主动取消时保存中断并退出，不自动重新启动。

这两个时间值是拟定的宿主可配置等待策略，不是分析质量或任务总时长门槛。先验证真实增量可观测；若 SDK 对合法长工具参数生成不发进展事件，本步自动取消功能不交付，保留等待提示及明确中断后的恢复能力，并报告这一限制。

### 宿主进程已经退出

正常 dispose/取消路径尽力登记 defer。硬退出可能无法运行清理：恢复入口必须接收宿主已确认停止的事件，以及 Graph 已有的 data_root/run_id/action_id/task_id，转成 continue_agent 后再派发。

- 不根据目录扫描、历史会话、超时推算或 Run 的年龄决定“哪个 worker 死了”。
- 没有停止证据的 dispatched action 不能自动抢占。
- 最小接口是根 Agent 可调用的精确中断登记入口，复用 adapter defer；原 retry 入口保持用于已登记的 continue_agent，不扩大成任意 action 重建。
- DSH → OpenCode ACP 使用相同 OpenCode worker 宿主策略。DSH 原生 CodeAgent/内网 CodeAgent 的进展、取消和停止确认由各自宿主提供；本地没有证据时记录为待接入边界，不以 OpenCode 测试代替内网验收。

### 修改位置与验证

- `.opencode/plugins/pangea.ts`：事件观察、取消确认、现有局部恢复串接及中断登记工具。
- `.opencode/agents/pangea-agent.md`：收到明确中断后的原 action 续接。
- 复用 `cli/adapter_api.py` 的 defer/retry/bind；确有返回信息缺口时仅补确定性状态字段。
- DSH companion 的执行入口/策略名单只在接入已确认取消事件所必需时修改，不改 ACP Provider 本身或新增通用调度器。

先用可控流测试：持续有 token 的长输出不取消、工具长执行不误判、模型静默才触发、取消与完成同时到达只处理一次、旧回合未停止不能重派、原 task 恢复保留已保存内容、第二次静默退出自动恢复、用户取消不重启。再用真实 M2.7 运行确认事件观测；不为触发 600 秒超时反复消耗真实模型，时钟与流状态测试使用受控宿主夹具。

## 7. 验收顺序、目标与交付门槛

### 本地重点验证

依次验证 P0 的正确对象替换、P1 的首次规划保存、P2 的完整源码读取、P3 的中断恢复，再运行相关 notes/adapter/OpenCode 工具和 DSH source-first-tools/report-policy 回归。测试留在各项目规定的位置，pangea-agent 的 tests 按现有规则不提交。

可读语义结果中的普通不一致应保留正文、给出诊断并能局部修正；真实绑定/数据边界错误才进入等待处理。合法语义结果、文本 body、资料不足的 UNRESOLVED 和未完成声明都要按原意保留。不得通过只证明“能拦错误”宣布完成。

### 真实模型对照

- 固定原源码 commit、完整 target、M2.7 普通版、204800 预算和资料输入。模型、分析范围、盲审次数、结果层级定义不随提速改变。
- 开发后跑 **2 次全新 OpenCode Run + 1 次 Desktop → DSH → OpenCode ACP Run**。逐次保存版本、action/task 绑定、阶段时间、调用/输入输出、规划诊断、工具错误、修正链与报告；观察结果波动，不只选最快的一次。
- 当前基线受外部长等待干扰。如果需要对外给出严格总时间下降比例，再补 1 次固定旧版本的同条件参考运行；新旧资料版本不混用，不重做已有用户 Run。
- 工程目标：整文件规划无编号返工；相同源码选择回包字符量下降至少 40%；没有无进展无限自动恢复；连接正常的完整样本争取 **30 分钟以内**。30 分钟是目标，不是当前已经证明可达的结论，也不是强行停止分析的限时。
- 质量先过关：主要正常/异常/再次操作/清理恢复流程有对应证据和用例；定向替换没有丢失无关有效流程；用例层级和执行状态如实展示。由验收阅读源码、报告和替换轨迹确认，不增加 Python 内容判定规则或第三个流程 Reviewer。
- 若仍有 F-003 丢失、错误预期、产品入口层级误判等明显问题，该次不能成为质量合格的提速证据。若因真实资料不足保留 UNRESOLVED，可以记录性能，但仍不能宣称质量 PASS。

### 停止条件与最终交付

现有用户改动冲突、客户端实际加载版本不一致、不能确认旧请求停止、字段变化会破坏已部署读取契约时，停止对应修改并给出精确差异；不扩大修改范围。一次恢复仍无进展时停止自动调用并保留原 Run。探索发现本次范围外问题，只记录。

交付：分步代码变更、定向测试结果、三次真实运行账本、最终报告与替换链检查，以及“已实现/已验证/尚未验证”的明确结论。内网 DeepSeek V4 Flash 的最终耗时仍需内网实测。

用户已授权方案输出后直接执行，按 P0 → P1 → P2 → P3 → 双客户端验收推进；授权涵盖上述工具输入/输出调整和宿主等待策略，不涵盖改动用户源码、扩大分析范围、调整语义质量门禁或更换 Provider。


## 8. 执行补记

- `owned_files` 的参数说明明确其覆盖整文件全部责任区域，以及单个整文件只归属一个单元；context_files 明确为不带行号的精确文件引用。
- Graph 读取更新后的规划时先选择同 unit_id 的最新记录，再展开文件选择，保证早期已纠正的错误不影响同 Planner 继续。
- DSH 的 TaskStore 需要保存既有 effective_context_budget 输入字段，避免任务保存后回到 250000 默认值；新增任务的显式 204800 预算经保存、读取和启动传递。未提供字段的旧任务保持原行为。
- OpenCode 真运行观察到了正文 delta 和工具状态变化，尚未观察到工具参数生成的增量。因此按 P3 条件保留等待提示及明确中断恢复，自动无进展取消尚未交付。
- 第一轮运行受验收 Desktop/开发监听进程异常高 CPU 影响，本地 result_write 单次等待约 141 分钟。该轮用于功能和质量检查，不用于总耗时对照。后续使用已构建的 Desktop 应用，不启动开发监听进程。

- Desktop ACP 首次尝试实际模型偏离 M2.7：安装的 ACP 库未应用 agentOptions.model，真实根/worker 均用了 OpenCode 默认 DeepSeek。已停止该尝试、保存原 Run 待处理；使用验收进程 OPENCODE_CONFIG_CONTENT 明确固定 M2.7 后重跑。ACP Provider 选型缺陷单独记录，未扩大修改其源码。

## 9. 执行与验收结果

2026-09-09：范围内实现和约定的两次独立 OpenCode、一次实际 M2.7 Desktop ACP 运行已完成。正常计时样本分别为 19:47 和 20:43（Desktop Job 自身 20:39），等价源码回包减量 50.84%。整体质量验收未通过：报告仍有错误预期和层级声明，源码续读存在遗漏；Desktop 模型选择和状态同步问题单独记录。P3 自动静默取消保留前置条件，尚未交付。完整证据见 [验收记录](/Users/shepard/.codex/outputs/pangea-performance-v1-20260909/ACCEPTANCE.md)。
