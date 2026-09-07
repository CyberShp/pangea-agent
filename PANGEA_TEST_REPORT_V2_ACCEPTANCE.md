# PANGEA 测试报告 V2 验收记录

- 验收日期：2026-09-08
- 开发工作区：`/Volumes/Media/pangea-agent-source-first-v1`
- 开发分支：`codex/source-first-v1-agent-rules`
- 目标父分支：`codex/pangea-semantic-analysis-rework`

## 结论

本轮四项目标均已执行：报告记录级导航、对应回归测试、真实 Coverage 补测、限定 SPDK DSH 分析验收。报告生成质量达到合入条件；被测 SPDK 的最终质量状态仍如实保持 `UNRESOLVED`，不等同于 SPDK 功能已在真实设备上通过。

## 报告与导航

- Markdown 与 HTML 按模块流程、分支用例、未覆盖用例、风险用例和开发辅助附录组织。
- 流程图由 Agent 声明的节点、连线和路径离线生成 SVG；HTML 内嵌 SVG，不依赖外网。
- 每条正式记录使用 `unit_id + record_id` 形成唯一锚点；流程到用例、用例到流程或风险的引用均在所属 unit 内解析。
- accepted closure 存在时，报告只展示该 unit 的最终有效版本，不把首轮旧记录与修正记录混在一起。
- 浏览器实测 `#record-unit-0001-rec-000009`：页面定位到 `TC-006` 并自动展开对应折叠项。
- 重复显示编号及 accepted closure 的定向测试已覆盖，未出现跨 unit 跳错。

## 本地回归

- Python：`40` 项通过。
- OpenCode 插件：`11` 项通过。
- `git diff --check`：通过。
- 测试验证了旧字符串记录兼容、结构化行为记录、流程图安全转义、坏图软诊断、唯一记录锚点、重复编号作用域和 closure 版本选择。项目规则要求 `tests/` 保持本地开发目录，因此测试文件不纳入提交。

## 样本 B：真实 Coverage 与可执行 CLI

- Run：`sessionctl-cli-busy-cove-260908-01`
- 运行路径：PANGEA Desktop 开发版内的 DSH 工作台，经 OpenCode ACP 调用 `MiniMax-M3`。
- 冻结 Coverage：`asset-260908-001:C0009`，`disconnect_session` 初始执行计数为 `0`。
- 首轮结果：`agent-results/source-first/analysis-unit-0001.json`，accepted revision `14`。
- 最终报告：`report.md`、`report.html`，质量状态 `PASS`。

Graph 的 planning、analysis、independent review、comparison review 共四个 action 均一次 accepted；每个 action 的 `validation_failures`、`repair_dispatches`、`incomplete_attempts` 都为 `0`，没有 closure。

实际执行生成的 `TC-006`：

1. `sessionctl connect ep-a valid-token` 输出 `CONNECTED ep-a`，退出码 `0`。
2. `sessionctl disconnect ep-a` 输出 `DISCONNECTED ep-a`，退出码 `0`。
3. `sessionctl status` 输出 `DISCONNECTED`，退出码 `1`。
4. 重新采集 Coverage 后，`disconnect_session` 执行计数由 `0` 变为 `1`。

该用例从 CLI 产品入口执行，没有把函数调用写成测试人员步骤。最终报告包含 2 张流程图、8 条分支用例、1 条未覆盖用例、0 条风险用例；无已确认风险时没有为凑板块伪造风险。

Coverage 工作簿：`/Users/shepard/.codex/outputs/01a067b2-fb46-7ba2-b03e-ceb6bb5f74a1/sessionctl-coverage.xlsx`

## 样本 A：限定 SPDK DSH 验收

- Run：`spdk-nvme-initiator-dh-h-260908-02`
- owned source：`lib/nvme/nvme_auth.c`
- 运行路径：PANGEA Desktop 开发版内的 DSH 工作台，经 OpenCode ACP 调用 `MiniMax-M3`。
- 首轮结果：`agent-results/source-first/analysis-unit-0001.json`，accepted revision `17`。
- 最终结果：`agent-results/source-first/closure-unit-0001.json`，accepted revision `27`。
- 最终报告：`report.md`、`report.html`，质量状态 `UNRESOLVED`。

首轮已经正确给出两条核心结论：

- `AWAIT_SUCCESS2/AWAIT_FAILURE2` 对非 `-EAGAIN` 完成无条件转 `DONE`，不写 `auth.status`；`DONE` 以既有 `auth.status` 回调和返回。
- 同一 qpair 再次认证前没有重置 `auth.status`，而 `nvme_auth_set_failure` 只在状态为 `0` 时写入，二次结果可能继承首次错误。

Comparison Reviewer 另外发现两处需要收紧的用例问题：`TC-101` 把断开与连接保持时的错误完成混为一个触发，以及 `AWAIT_FAILURE2` 和相邻等待状态缺少独立执行用例。原 analysis worker 通过一次 targeted closure 完成修正：

- `TC-101` 改为“连接保持、目标端对 SUCCESS2 返回错误完成”的唯一触发。
- 新增 `TC-103` 验证 `AWAIT_FAILURE2` 保留 `-EACCES` 且丢弃新的完成错误。
- 新增 `TC-104` 对照验证四个前置等待状态通过 `set_failure` 传播错误。
- 新增 `TC-105` 单独验证非 poll 窗口断开以 `-ECANCELED` 回调。

planning、analysis、independent review、comparison review、targeted closure 共五个 action 均一次 accepted；每个 action 的 `validation_failures`、`repair_dispatches`、`incomplete_attempts` 都为 `0`。最终报告包含 4 张流程图、4 条正式分支用例、2 条风险用例和 5 条开发辅助用例，记录跳转均指向 closure 的有效记录。DSH 页面显示 5/5 action 完成，界面观察总耗时约 23 分 20 秒。

`UNRESOLVED` 保留的边界是：冻结范围没有 `nvme_wait_for_completion_poll` 的完整实现，无法确定所有 transport 事件映射到返回码的细节；也没有真实产品调用方证据证明同一 qpair 的复用频率。这些未知项不影响 owned source 中已确认的状态机后果，但禁止把 SPDK 设备行为宣称为已实测通过。

## 验证边界

- 本轮实际执行了 sessionctl Coverage 用例；SPDK 用例只完成冻结源码、用例语义、报告展示与 DSH 生命周期验收，没有可用 SPDK 设备和故障注入环境，因此未执行 SPDK 设备测试。
- DSH 路径是本机 PANGEA Desktop 开发版加 OpenCode ACP，模型为 `MiniMax-M3`；它不替代内网 CodeAgent 加 DeepSeek 的最终运行验证。
- Graph 与 DSH 页面没有提供可核对的 token 用量，本记录不估算上下文消耗。
