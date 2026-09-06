# PANGEA 返修机制修复与真实验收

## 当前结论

代码修改与 31 项定向验证已完成；Run18 真实验收未通过。

首轮 Analysis 尚未完成时，实际输入已达 299689 tokens，超过 250000 预算；同时出现
5 次相同的缺 body 写入错误，以及 1 次输出截断导致的 JSON EOF。验收执行者于
2026-09-06 20:37 将本次 Run 停止，保留全部原始产物。没有进入盲审、对照或 closure，
没有正式报告，不能声称这些阶段已真实验收。

## 本轮修改

- OpenCode 宿主管理同一 action 的写入顺序、revision、request_id 和冻结 comparison 版本；保留底层真实并发冲突与身份保护。
- 回合结束后读取真实 SDK 完成/错误状态；空结果及时交回同一 worker 做一次精确恢复。未完成保留原 action/task/result，无进展由宿主暂停自动派发。
- Reviewer 用 correction_record_ids 一次选择需更正的 finding；Graph 使用其已绑定单元续接原 worker，质量结论与修正选择分别表达。
- 盲审完成且没有新增发现时，由 Reviewer 保存实际审查 summary 后正常完成。
- 创建修正任务前核对全部原 worker 的真实身份和结果；报告三个产物写入成功后才保存终态。

没有修改用户 SPDK 源码、旧 Run17 结果、dsh-pangea 或 pangea-desktop 仓库，没有提交或推送 Git。

## 定向验证

执行目录：`/Volumes/Media/pangea-agent-source-first-v1`。

| 检查 | 结果 |
| --- | --- |
| Python：宿主续接、显式修正派发、报告写入失败、结果存储完整性 | 22/22 通过 |
| Node：真实插件配合模拟 SDK/CLI 的宿主协议测试 | 9/9 通过 |
| git diff --check | 通过 |

命令：

```text
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/opencode_plugin.test.mts
git diff --check
```

这些测试不替代真实模型验收。Node 有现有 package.json 模块类型提示，测试正常执行。

## Run18 证据绑定

| 项目 | 实际值 |
| --- | --- |
| Run | lib-nvme-nvme_auth-c-dh-260906-18 |
| OpenCode 根会话 | ses_f8962acccffeQl684QyGzqDHRd |
| 模型 | minimax-cn-coding-plan/MiniMax-M3 |
| 客户端 | 全新 OpenCode CLI 进程，未附着旧会话 |
| 工作树 | /Volumes/Media/pangea-agent-source-first-v1 |
| 分支 / HEAD | codex/source-first-v1-agent-rules / 5dae058；验收使用未提交工作树代码 |
| 冻结 SPDK 版本 | 97af299e3c76368219f0cddcc710fafd57edcc1c |
| 主责源码 | lib/nvme/nvme_auth.c |
| 源码 SHA-256 | bca0bbaf5835b70b337b3f7f0cdc612db2a9d2bea909a4ba0d1b9eea1e21853c |
| 上下文预算 | 250000；实测按 input + cache.read + cache.write 统计 |

Run18 的主责源码字节与 Run17 相同。自然语言输入保留 Run17 的认证状态机、错误持久化、共享 case、条件编译和可执行黑盒测试要求，仅更新 Run 编号。

## 启动时关键文件指纹

| 文件 | SHA-256 |
| --- | --- |
| .opencode/plugins/pangea.ts | e3dd703cf3898852c0a5ae6db411c237eb178ac14bbde482b42ccc2306a62cdf |
| .opencode/agents/review-worker.md | 5b52a455e268517fe4776a3913d701f6bdf57b1b1817d3bc494231cdd02b94b5 |
| src/pangea_agent/cli/adapter_api.py | 4205bf511f6328f612393971ff4329a85c85ed1b96e94fff55b1c2c5ca54edd3 |
| src/pangea_agent/cli/source_first_api.py | 09dc8b55262954c42fd08a6296e17b80e6b19f3256060f8eeaf00d95833cc85f |
| src/pangea_agent/graph/nodes/source_first.py | df394cbff3c691143bec1fc9a78c36567f6b3da42fb6ce18b8b0b59900020292 |
| src/pangea_agent/graph/nodes/finalize_workflow.py | 0502475a6a25f8e6bdc4e747fd427877db58f057130c5834306bf22786e8c6b0 |
| src/pangea_agent/report/source_first.py | 8b48de74952d4dba7b1178b82379cc9a498ec96f3298715f0e3c3ecf0d342dcf |

验收期间没有修改运行代码或人工补写语义结果。停止后回读上述文件指纹，与启动时一致。

## 验收判断

- 工具内部错误与 Graph 返修次数分别统计；不能仅用最终 validation_failures=0 判断稳定。
- 反复相同机械错误导致停滞、已明确选择的修正漏派、未完成被描述为完成，任一重现均属于本轮大问题。
- 必须核对真实 action/task 对应关系、原 Reviewer 续接、原 worker 修正、报告正文与 report.md/report.html/report-complete.json 三个产物。
- 语义结论有真实证据缺口时可以保留 UNRESOLVED；这不等于流程自动通过，仍需分别审查工作是否完成与结论是否可信。
- 单次真实 Run 不能证明长期稳定；本轮不代表 DSH 或内网 DeepSeek 已验收。

## 运行台账

| 阶段 | 会话 / 结果 | 当前观察 |
| --- | --- | --- |
| Planning | ses_f8962691effeyD6gJEOGauyW4j | 1 回合完成，创建并更新 1 个单元，32 个函数 region，工具错误 0，输入峰值 55212 |
| Analysis | ses_f895ea03affe2qmwDxMMXxcGgX | 主动停止；revision49，67条历史记录、34条退休、33条有效；completion=null |
| Independent review | 未派发 | 未验收 |
| Comparison / closure / reports | 未执行 | 未验收 |

工具轨迹数据源：`/Users/shepard/.local/share/opencode/opencode.db`，仅按上述根会话及本 Run Graph 明确绑定的 task_id 查询。

## 最终真实统计

| 指标 | 结果 |
| --- | --- |
| 非停止原因的工具输入失败 | 6 次 |
| `record[1] 必须包含 body` | 5 次，均来自 result_write |
| JSON `Unexpected EOF` | 1 次；OpenCode 记为 invalid 工具的 completed 事件，不能据 completed 漏计 |
| revision conflict | 0 次 |
| SDK info.error | 0 次 |
| SDK finish=length | 1 次，与 JSON EOF 属于同一消息，不重复计为另一根因 |
| Analysis result_write | 15 次成功、5 次失败 |
| Analysis result_supersede | 34 次成功、1 次停止时未完成 |
| 完全同内容替换 | 3 次，rec13→20→21→22；之后 rec23 只改变 DFX 字段表示 |
| Analysis result_read | 43 次 |
| Analysis source_index / read / search | 3 / 45 / 61 次 |
| Analysis work_finish / first_finish | 0 次 / 不存在 |
| Graph validation_failures / incomplete_attempts | 均为 0，不能代表上述工具内部错误为 0 |
| 最大单次输入含缓存 | 299689 tokens，比预算多 49689，约 19.9% |
| 根会话创建至停止落盘 | 20:06:57.075→20:37:13，约 30 分 16 秒 |

峰值消息 `msg_076b81539001km46OG5Ak29upC`：input=70、cache.read=299619、
cache.write=0、output=394。输出截断消息 `msg_076acbb1e0018Wdh5An4CoQ0E9` 的
output=32000；对应 invalid 事件为 `prt_076acd28c0011R1zpkIGN43UgE`。

3 次完全同内容替换比较了 kind/body/evidence/relates_to，忽略 JSON 字段顺序。
它们与第四次格式替换合计跨约 24.8 秒。该时间段内工具均成功且 warnings=[]，
宿主只有最初一条 prompt，没有局部恢复或最终回读提示，因此不能归因于校验催修。
其他替换中可能包含有效语义修正，不能把全部 34 次都称为无效格式返工。

## 上下文增长证据

截至 20:30:17 的固定快照，源码工具输出累计 273957 字符；result_read 累计
108265 字符。两次大结果回读附近，实际模型输入分别增加 13220 和 16336 tokens；
后续 9 次定点回读累计增加 8424。三次源码搜索合批附近增加 21485。
这些增量包含相邻请求间完整对话变化，不能当作某个工具的精确 token 消耗。

两次大 result_read 的 OpenCode metadata.truncated=true；上述输出字符数以
OpenCode 保存的实际工具回包为准，不是结果文件原始体积，也不是 token 换算。

该时点尚无 Analysis work_finish，强制 finalization 和 Reviewer 均未启动。
因此已发生的上下文压力来自首轮源码读取、结果回读与重写累积；不能说删掉尚未运行的
Reviewer 就能够解决本次已发生的问题。

## 停止与保存事实

验收执行者先向本次专用 OpenCode 进程发送 SIGINT，确认退出码 130，再通过标准
`runs stop` 保存终止状态。progress 中 lifecycle_status=stopped、quality_status=null。
标准停止接口将 Analysis action 记为 failed、error="用户停止 Run"；这里代表操作停止，
不是 Reviewer 或 Python 对分析语义作出失败判决。

SQLite 保留根 Analysis dispatch 和末次 supersede 的两个 running 事件，作为中断残留
单独记录，不计入 6 次输入失败。专用 OpenCode 进程已经退出。

report.md、report.html、report-complete.json 均不存在。67 条历史记录及原始任务、
冻结源码仍在当前 Run 目录，未清空、未重开或人工替换结果。

## 后续裁减建议

1. 优先简化正文提交合同：由 Agent 写普通文本/Markdown，机器管理身份和版本外壳；
   风险条件、步骤、预期、证据仍需表达，但无需为正文拼装多层任意 JSON。当前重复的
   missing body 与 item 嵌套格式重写直接支持优先处理这一点。
2. 减少结果反复回读：默认读取当前有效记录的有限页，只有核对修改历史时才打开退休版本；
   源码索引和搜索返回紧凑坐标，按需取正文。保持源码可读和证据可追溯。
3. 评估移除 Analysis 完成后的强制全量复读、Comparison 完成后的再一次全量复读。
   它们不是本次前半段失败的已证原因，但在现有高上下文上继续执行会增加负担。
   保留一次独立盲审、同 Reviewer 的必要对照，以及身份、路径和文件完整性保护。

以上为下一轮范围建议，本轮没有在失败现场继续修改运行实现。下一轮方案应分别验证
正文提交的稳定性和 250K 内的实际完成，再确认是否还需要调整审核层数。
