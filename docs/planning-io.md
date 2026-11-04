# Planning 读取与提交往返优化

## 适用范围

新 Run 的 Planning task 冻结 `planning_io_policy=planning-io-v1`。能力由当前 `task-open.write_contract.capabilities` 提供；已有 Run 沿用原冻结规则，不改写其任务、Coverage、结果或交接记录。Planning 仍只分配主责、必要依赖和未知边界，Python 不决定单元归属或证据是否充分。

## 输入与保存

- `task-open` 随附有界的 `planning_startup.pages`，交付必要规划规则、场景和目录导航的原文页。未交付部分在 `pending_inputs` 保留 input_id/cursor。Desktop 初始提示可直接传递宿主已经取得的有界回包，worker 无需重复开任务；同 action 续接恢复当前结果，不沿用初始 revision。
- `coverage-read` 只查询当前绑定 task 授权的冻结 Coverage。summary 提供目录/文件计数，index 提供真实 gap ID 和位置，records 提供原记录。支持按 repo/path/ID 筛选以及 compact 分页；未知状态、歧义匹配和内部零计数分支不丢失。聚合只是导航，不是覆盖结论。
- `read-batch` 合并已知且互不依赖的只读查询。每项保留原分页信息，结果载荷预算默认 24000 字符，范围为 3000–24000 字符；尚未执行的请求对原请求数组用 `--start-index <next_request_index>` 继续。`all_succeeded=false` 时逐项处理 ok/error，不能把失败项当成功。下一步依赖上一步结果时仍分次查询，不猜测后续路径。
- `plan-write-batch` 用一次原子写入保存几个已确定单元，返回各项机器 ID 和 revision。确定几个就保存几个，不等全部探索结束。相同 request-id 的同一载荷重试复用成功结果，不重复创建单元；改内容使用新 ID。重试回包 replayed=true 时 revision 保留原保存版本，后续新写入和 work-finish 使用 current_revision。普通 note/unresolved 通过现有 result-write 数组一次保存。
- 新 target-first 回包不默认展开未分配候选清单；保留数量，仅具体边界问题需要时才用 `plan-candidates --max-chars N` 按需查询，以 next_page_token/--page-token 续页。未知引用、主责冲突等真实诊断继续提供。候选未分配不是新的完成义务，原 ready 判定与语义职责保持。

批量接口是受控的数据传输，不执行任意 shell，不猜测绑定、不读取其他 Run、不自动改派 worker，也不增加模型审核阶段。详细参数和示例见 [CLI 合同](source-first-cli-worker.md)。

分页预算限制接口结果载荷；coverage-read/plan-candidates 按紧凑 JSON 计量，read-batch 按缩进后的载荷计量。CLI 封装和 readable 格式化有额外字符，max-chars 不保证 stdout 总字符数，不能据此截断返回内容。

## 工程验收

验证同一冻结输入的聚合、筛选和分页能够还原原记录；批量读取每项保留绑定与续读位置；有效批量规划与逐条规划的当前语义内容相同；重试不重复建单元；批次失败保留此前结果，原 worker 修正后可继续；Planning 的 note/unresolved 和单元关系继续进入 Analysis 交接。旧 Run 不被补写新合同。

这些回归验证数据、权限边界和接口兼容性，不能证明实际模型一定减少调用或用时。真实 provider 验收固定源码快照、Coverage、模型、执行器版本和任务目标，对比以下数据：

| 指标 | 统计方法 |
| --- | --- |
| Planning 工具次数 | 按真实 tool_call_id 去重；失败事件不另计一次调用 |
| 主模型请求与工具时间 | 记录各自起止及 parent/request ID，用区间并集计算；交叠单列，不相加冒充墙钟 |
| 读取与生成量 | 记录输入页数、返回字符、输出 token、重读页及提交修正次数 |
| 下游收益 | 检查 Analysis 交接内容、重复搜索及首个有效产物时间，不能只看 Planning 结束时间 |

## 内网 CLI 固定等待对照

Desktop 的 ACP `permission: allow` 与外部 CodeAgent/NGA 内部权限流程是不同层。PANGEA 源码不能证明某个外部版本一定存在两个辅助模型请求，也不能把静默空档直接命名为 hook 耗时。本改造不设置全局权限放行，也不猜造 CodeAgent 配置项。

在隔离测试 Run 绑定一个测试 worker，固定一条当前合同允许的只读命令，保持 Python、工作目录、绑定、输入、输出大小和执行器版本一致。建议选 `input-read` 的一个小型冻结规则页；只比较读取，不执行 bind/settle/work-finish 或重放生产任务的写入。基线与候选配置交替运行至少 10 组，保留首轮冷启动记录并单列，不能把冷启动与热缓存混算。

| 组别 | 唯一变化 | 必须观察的证据 |
| --- | --- | --- |
| 基线 | 现有配置 | 外部 CLI 的权限决策、辅助模型请求、子进程开始/退出、hook 开始/结束及 ACP 工具事件 |
| 精确允许规则 | 仅按目标 CLI 版本已验证的语法允许这条测试命令 | 规则确实命中；哪些辅助请求被跳过；路径与写入限制保持 |
| hook 对照 | 仅调整已由日志或网络记录确认的 hook | 对应请求消失或移出关键路径；不把其他静默空档归给该 hook |
| 合并配置 | 合并前两项已证明有效的变化 | 收益是否叠加，是否仍有其他等待 |

每条记录关联 tool_call_id、主模型/辅助请求 ID、父请求 ID，以及统一时钟下的开始结束时间。分别报告中位数、P95、工具区间并集、主模型区间并集、两者交集和其他等待，核对总和等于会话墙钟。原生 Read/Write、PANGEA CLI 命令和其他 Bash 分开统计，不能把某类允许规则收益乘以全部调用数。

若目标版本不提供某类时间戳，只报告可见区间和未归因等待，不伪造阶段拆分。相同输入的完整 Planning 对照应在微基准确认后运行；20 分钟是待验证目标，不是本代码交付的承诺。
