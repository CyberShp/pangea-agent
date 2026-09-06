# PANGEA 业务行为用例优先：开发与验收记录

记录日期：2026-09-07
实现工作树：`/Volumes/Media/pangea-agent-source-first-v1`
分支 / 基线：`codex/source-first-v1-agent-rules` / `5dae058` 加当前未提交修改
验收数据：`/Volumes/Media/pangea-agent/pangea-data`

## 1. 当前结论

本方案的代码开发和本地定向回归已经完成，但真实客户端验收整体**未通过**。

- 小型 C fixture 的 A2 Run 通过，生成的用例能够编译执行，目标文件的函数、行和分支覆盖率均从不完整提升到 100%。
- 大型 SPDK `lib/nvme/nvme_auth.c` 范围没有通过：B8 正式结束为 `UNRESOLVED`，B9 在 Comparison 尚未完成时停止。
- B5、B6、B8、B9 没有复现过去反复出现的 schema / 字段校验失败；当前主要问题已经转为首轮语义理解仍会绕开真实失败后的自然状态。
- DSH 尚未创建真实 PANGEA Run。当前安装和 profile 没有注册任何目标 Provider，因此不能把本地规则同步或 UI 配置保存描述为 DSH 验收通过。

所以，本记录只能证明“新合同可在小范围走通、机械返修显著减少”；还不能证明“复杂源码首轮分析质量达标”或“双客户端验收完成”。

## 2. 本轮开发内容

### 2.1 业务行为用例合同

- 新建 `behavior-test-v1` 分析目标；新 Run 默认以业务主干、业务分支、异常传播、清理恢复和真实 Coverage 缺口生成用例。
- 用例不再要求先创建风险记录；已确认问题仍保留事实、证据和关联用例。
- 新增聚焦方法文件，避免在一期任务中自动加载完整专项风险与六维 DFX 负担。
- 报告改为用例优先，并明确区分“生成用例”“实际执行用例”和“Coverage 已改善”。

### 2.2 提交和读取减负

- OpenCode 普通结果提交改为单条 `kind + body`，版本、身份和路径仍由宿主管理。
- 新增有界分页读取，缩短源码索引、搜索和结果读取回包；修正源码范围续读。
- 删除 Analysis 和 Comparison 完成后无条件追加的全量复读，仅在出现具体疑点时补读。
- Closure 默认一个 finding 对应一个直接替换，避免在无关记录间连续 supersede。

### 2.3 首轮语义约束

- Planning 的 purpose 只写职责、主生命周期和必要上下文，不预先罗列 helper、内部状态、分支和错误结论。
- Analysis 首轮目标历史控制在约 145K，为后续审查与定向修正保留约 70K。
- 重试用例必须继承第一次失败后的自然状态；除非存在真实公开恢复入口、宿主状态迁移或对象重建，不能直接把内部 adapter / transport 状态改回 ready / running。
- 对返回值为 0 的异步入口，必须继续证明底层事务确实重新启动、资源确实重新创建以及后续 poll 指针有效，不能只根据公开返回值推断成功。
- Reviewer 对 Comparison finding 使用真实 finding ID；Planning 中出现的背景说明本身不自动增加 Analysis 交付义务。

上述内容没有把语义判断移入 Python，也没有弱化冻结源码、action/task 绑定、受控路径、并发版本保护、原始首轮结果和 `UNRESOLVED` 真实性要求。

## 3. 本地回归

执行目录：`/Volumes/Media/pangea-agent-source-first-v1`。

| 检查 | 最近结果 |
| --- | --- |
| OpenCode 插件协议测试 | 10/10 通过 |
| Python 定向单元测试 | 34/34 通过 |
| Python 修改模块编译 | 通过 |
| task contract JSON 解析 | 通过 |
| `git diff --check` | 通过 |

Node 测试仍会输出已有的 `MODULE_TYPELESS_PACKAGE_JSON` 提示，不影响测试退出状态。本次不为消除提示而扩大 package 配置范围。

## 4. 真实 Run 台账

| Run | 关键会话 | 终态 / 停止点 | 主要事实 | 判断 |
| --- | --- | --- | --- | --- |
| A1 `behavior-260906-001` | Root `ses_f88fd96a4ffeRjMRFlvbwd40Np`；Planning `ses_f88fd5ce8ffeRxooMmSGPGuzxT`；Analysis `ses_f88fbf27cffekDIwYo4vccGFOU`；Reviewer `ses_f88f6a9c3ffeGu3r4xtsd374AH` | `UNRESOLVED` | 首轮发明符号并使用错误 header 范围 | 不通过 |
| A2 `run-behavior-fixture-001` | Root `ses_f88e9e16affeemNd630uReycN2`；Planning `ses_f88e99e09ffef9RC6ajpseCEAf`；Analysis `ses_f88e74acaffe70ySk1VR7lQIh6`；Reviewer `ses_f88e07fcaffeIEaNcrjPV58a9C` | 完成，revision 3/12/2/2，无 Closure | 无 warning、schema、字段、stale revision 或截断；生成的 C 测试编译运行退出 0；`transfer.c` 函数/行/分支从 66.67/51.22/43.75 提升为 100/100/100 | 小范围通过 |
| B1 `spdk-full-nvme-auth-bt-v1-20260906` | Root `ses_f88da5822ffetRY1SKPURMUAes`；Planning `ses_f88d9f323ffem10b230LnPPSb4`；Analysis `ses_f88d67691ffek1HrUoQyvYL69K`；Reviewer `ses_f88cd988bffee3uRYUqGv5b37Y` | `UNRESOLVED` | Reviewer 保留 6 个语义 finding，Closure 未完成 | 不通过 |
| B2 | Root `ses_f88b06b33ffe05AcwODRyH2YF9` | Comparison 停止 | 旧分页 token 处理导致连续工具失败；Analysis 上下文约 153.8K | 不计通过 |
| B3 | Root `ses_f889aeeccffelMk2bEcwgHkt28` | Planning 后停止 | Planning 上下文选择不合理 | 不计通过 |
| B4 `spdk-nvme-auth-behavior-b4-20260906` | Root `ses_f88956c92ffeTffSH83cEpbxf7`；Planning `ses_f8894d35dffeZVN53748yt2w2U` | Planning 约 206K 时停止 | 尚未形成 plan，Run 仍保留 running/planning 现场 | 不计通过 |
| B5 `spdk-nvme-auth-behavior-b5-20260906` | Root `ses_f888ff8a5ffensb7CLpm0fReKO`；Planning `ses_f888fc5e2ffebukXs7WjQSxX5f`；Analysis `ses_f888cc4d7ffeLdRKYAinjVZgcW`；Reviewer `ses_f88863383ffeg4we2wbsSpw7z2` | `UNRESOLVED`，revision 3/49/2/3，无 Closure | 无 schema / 字段失败；有 2 次读取工具错误；首轮写错 DONE 重复行为和计数，Reviewer 又选成 Analysis record ID，导致没有派发 Closure | 不通过 |
| B6 `spdk-nvme-auth-behavior-b6-20260907` | Root `ses_f88773c6effe71xjumG5YoqsRO`；Planning `ses_f88771565ffefYQJPeXjNw9Pd8`；Analysis/Closure `ses_f8874f3c7ffemcmM6hoIeNjMjV`；Reviewer `ses_f8869b882ffeZknHVYP8Fw21EE` | Closure 后 `UNRESOLVED`，revision 2/10/2/3/15 | ID 路由与续接原 worker 已走通；无 schema / 字段失败；Reviewer 把 Planning 背景中的 helper/context 过度当成交付义务 | 不通过 |
| B7 `spdk-nvme-auth-behavior-b7-20260907` | Root `ses_f885a61bfffetI1V5sbk69ZNKU`；Planning `ses_f885a0630ffe5V6VCg3iZ6jn06` | Planning 后停止 | Root 发明 API 名，并先提交带仓库前缀和 `@commit` 的错误 scope | 不计通过 |
| B8 `spdk-nvme-auth-behavior-b8-20260907` | Root `ses_f885811f4ffew210aX4IJPynBT`；Planning `ses_f8857d3c8ffeMlAgJw7NZOhPt5`；Analysis/Closure `ses_f8854a543ffeXWFnTv6r8LJweb`；Reviewer `ses_f884961d0ffePN41ArA1FuI0Su` | lifecycle `complete`，quality `UNRESOLVED`；revision 2/16/5/4/21 | 无 schema / 字段失败或截断；存在 3 次读取/猜路径类工具错误；修复了 callback 计数，但仍错误假定 retry 时 transport 会自行恢复 running，Closure 峰值约 277K | 不通过 |
| B9 `spdk-nvme-auth-behavior-b9-20260907` | Root `ses_f8836f12affew8Cvv20HJ8hE8G`；Planning `ses_f8836d1abffeAUz1pnRidHiwFW`；Analysis `ses_f88358624ffebDTsVk1FfdPSFn`；Reviewer `ses_f8832087fffe0hbLg1aJBsZk7W` | Reviewer 已派发、Comparison 未完成；为停止外部模型请求而中止监视 | Planning 约 53K，Analysis 约 117K，无字段/schema/tool 错误；但首轮虽然读到 transport `< RUNNING` guard，仍把 retry 前置条件手工设为 RUNNING，绕开失败后自然遗留的 AUTHENTICATING 状态 | 停止，不通过 |

A2 是唯一通过的真实 Run；它只证明小型 fixture。B 系列没有任何一次达到复杂 SPDK 范围的正式通过标准。

## 5. 大型 SPDK 尚未解决的核心问题

B8、B9 的主要问题不是格式，而是用例构造时改变了被测系统本应自然产生的状态：

1. 第一次认证失败后，TCP/RDMA transport 可能仍处于 `AUTHENTICATING`。
2. 第二次调用公开入口时，`< RUNNING` guard 可能直接返回 0，但不等于新的认证事务已经启动。
3. 若测试在第二次调用前手工把内部 transport 改成 `RUNNING`，就避开了真实重试路径，也无法发现后续 poll 可能使用空指针的风险。
4. 因此“入口返回成功”必须和“底层异步工作实际重建、下一次 poll 指针有效、资源与 callback 数量正确”一起验证。

本轮已把这条约束加入 Analysis、Reviewer、Rubric 和运行时提示，但修改后尚未完成一次新的真实 DSH Run，所以只能称为已修代码，不能称为已验收。

## 6. DSH 验收环境检查

### 6.1 已验证事实

| 项目 | 当前事实 |
| --- | --- |
| DSH Desktop | `/Applications/DSH Desktop.app`，bundle `io.dsh.desktop`，版本 `0.5.0` |
| UI 更新提示 | 可见 `0.7.2`，本轮未授权自动升级，因此未执行 |
| 当前 web profile | `/Users/shepard/Library/Application Support/dsh-desktop/harness/profiles/web/package.json` |
| 当前 bundles | `@deepseek-ai/dsh-base`、`@deepseek-ai/dsh-web-app`、旧 `dsh-pangea` |
| 当前 PANGEA plugin 来源 | `file:/Volumes/Media/dsh-pangea-integration/plugins/dsh-pangea` |
| Agent Runtime | NGA、CodeAgent、OpenCode、Claude Code 均显示“未注册” |
| 保存的 OpenCode 配置 | command `/opt/homebrew/bin/opencode`，args `acp`，model `minimax-cn-coding-plan/MiniMax-M3`；保存并重启 Harness 后仍为“Provider 未注册” |
| 产品 bundle | `dsh-pangea-product` 源码会注册 `pangea-nga`、`pangea-codeagent`、`pangea-opencode`、`pangea-claude-code`，但当前 web profile 未加载它 |
| 缺失依赖 | `/Volumes/Media/pangea-desktop/node_modules` 中没有 `@deepseek-ai/dsh-subagent-acp` 和 `@deepseek-ai/dsh-subagent-claude-code` |

### 6.2 阻塞判断

只修改 profile 加载 `dsh-pangea-product` 仍会因为缺少 subagent 依赖而导入失败。补依赖、迁移 profile 或升级 Desktop 都会改变 `pangea-desktop` / DSH 部署，超出本方案只修改 `pangea-agent` 的授权范围。因此本轮没有继续操作。

这意味着 DSH 当前阻塞发生在 Provider 注册之前，尚未进入 PANGEA task 创建、源码读取、Analysis、Reviewer 或字段校验。不能把它归因于 pangea-agent，也不能用它证明 pangea-agent 正常。

## 7. 验收门槛与下一步

下一次真实 DSH 验收至少应满足：

1. DSH Agent Runtime 能看到并启动一个已注册的 CodeAgent 或 OpenCode Provider。
2. 使用当前未提交的 pangea-agent 实现创建全新 `behavior-test-v1` Run，不续接 B1–B9。
3. Planning、Analysis、盲审、Comparison 和必要 Closure 均保留真实 task/action/result_path 绑定。
4. 统计 schema/字段错误、其他工具错误、repair 次数、上下文峰值和终端报告，不能只看 Graph counters。
5. Reviewer 必须检查失败后自然状态、第二次公开调用是否真正重建异步事务、下一 poll 指针、callback/message/free 次数和清理恢复。
6. 复杂 SPDK 首轮不能通过手工改内部状态规避真实重试链；若证据不足可以 `UNRESOLVED`，但不能报告 PASS。
7. 最终报告必须与 Run 终态一致；中止或未完成的 Run 不计通过。

在 DSH Provider 注册完成之前，本轮停止在环境修复授权边界，不再通过 Codex 启动或监视 OpenCode 进程。
