# openUBMC Lua 框架分析方法

本规则补充 openUBMC 框架语义，不代替 Lua 通用规则，也不扩展为完整 BMC 领域知识库。

## 组件与生命周期

- 每个组件按独立 Lua VM 判断模块缓存、全局状态和故障影响，不能把一个组件的 Lua 全局变量
  当成另一个组件可直接读取的共享状态。
- 组件间协作通过冻结源码或资料中给出的资源接口判断；没有接口证据时，不假设组件能够直接
  调用或修复另一个组件的内部状态。
- 对 `mc.class` 及 `ctor`、`pre_init`、`init` 按真实框架顺序重放。分别检查每个阶段已发生的
  注册、资源创建和状态发布，以及后续阶段失败时的清理、重试、停止和重新初始化。
- 生命周期函数中途失败时，先按源码顺序列出失败点之前实际完成的注册前缀；失败点之后的 callback、
  状态提交和清理一律记为“未执行”。重试成功后，把“首次残留前缀 + 本次完整注册”逐项展开并分别
  计算各 callback 的执行次数，不能概括成“两套 callbacks 全部重复”。
- 不同阶段的失败点，以及“阶段失败后仍可从公开入口继续使用”的终态，分别记录为独立 failure path；不得把 `pre_init` 异常、`init` 失败和未初始化继续 `update` 合成一句后只生成其中一张风险卡。
- 生命周期函数存在不代表一定被调用；必须由框架入口、组件声明或当前资料建立可达性。

## signal 的部分副作用

遇到 `mc.signal` 的 callback 注册和 `emit` 时，按 callback 顺序逐个检查：

1. 发射前 callback 表及每条 callback 捕获的对象。
2. 每条 callback 实际写入哪个计数器、布尔状态或外部对象。
3. 已成功执行的 callback 及其副作用。
4. 某个 callback 的错误。
5. 后续 callback 是否仍执行。
6. `emit` 最终如何向调用方报告错误。
7. 调用方在“收到失败但多个副作用已发生”后的补救、重试和恢复。

callback 表长度、某类 callback 的执行次数和实例字段计数是三种不同状态。重试或多实例场景必须
逐条展开“残留 callback + 新注册 callback”，再按各 callback 的函数体分别累加它真正修改的字段；
不能因为表中有 N 条 callback，就把 `callback_count`、`audit_count` 或其他任一字段都写成 N。
同一 signal 上的 callback 捕获不同实例时，还要把副作用分别记到对应实例，不能合并为发射者状态。

典型重试账本：首次失败只留下 C1，重试成功追加 C1'、C2、C3，则 signal 表长度是 4；一次 emit
实际执行 C1、C1'、C2、C3。若只有 C1/C1' 的函数体写 `callback_count`，本次实际增量是 2；若只有
C2 写 `audit_count`，审计增量是 1；C3 只负责提交，不能计入前两个字段。需求若规定“每类 callback
每次只执行一次”，TestCase 的通过预期仍应写 `callback_count` 增量 1，而 RiskCard 的当前错误实现
写增量 2；二者不同正是用例的 FAIL 判据，不能把测试预期改成错误实现值。

若当前框架契约规定单个 callback 出错后仍继续执行其他 callback，并在全部执行后才由 `emit`
抛错，就不能把失败写成“后续 callback 未执行”，也不能把调用方收到错误写成整体原子回滚。
反之，没有冻结契约时不得仅凭框架常识断言这一行为，应保留为未确认。

signal 若声明在类表或模块表上，必须把 callback 列表按该表的真实生命周期处理，不能按实例私有
状态处理。重建实例、释放实例引用或普通用例清理不等于注销旧 callback；只有冻结实现中的
disconnect/clear、重新加载模块或重建 Lua VM 能作为清理依据。生成多个用例时，明确每条用例是否
复用同一 Lua VM；若共享 callback 会改变后续计数、执行顺序或目标实例状态，每条用例必须使用独立
VM/等效的确定性重置，或者把跨用例残留作为显式测试步骤和预期，不能声称“无残留注册”。

类表或模块表共享 signal 是实现事实，不自动等于产品风险。冻结源码若提供可重复调用的公开
create/ctor 入口，且没有 singleton 拒绝、第二实例阻断、disconnect 或 clear，这本身就是同一 Lua VM
多实例可达性的源码证据：必须用派生 failure path 重放实例 A 注册、实例 B 注册、由任一实例 emit 后
callback 实际写入哪个实例，并把可观测的跨实例污染转成 RiskCard/TestCase。无需再等待资料逐字写
“支持多实例”。若公开入口明确拒绝第二实例，或现行资料明确只承诺单实例，则以该阻断证据
excluded；不能仅凭“框架隔离原则”或仅凭共享声明新增风险。组件之间独立 Lua VM 的规则也不能反推
同一组件实例必须独占 signal。
匿名 callback 中的 `self` 按 Lua 词法闭包绑定注册实例，不会在另一个实例调用共享 signal 的 emit 时
改绑为发起实例。逐项状态账本按 callback 的注册实例归属字段写入；错误导致 B callbacks 尚未执行时，
B 的计数和提交状态保持调用前值，不能声称 A callback 直接把同名字段写到了 B。
callback 的条件必须在每次 emit 时使用本次 value 重算。残留 callback 只在 `trip` 分支报错时，后续
`normal` emit 不能沿用上一次错误结论；应重新按注册顺序执行并把每条副作用写回其注册实例。
校准例：A、B 各注册 C1/C2/C3 后，A 的 `trip` 在 A.C2 中断，所以 B 仍为 0、0、nil；接着 B 的
`normal` 会让 A 变为 2、1、true，B 变为 1、1、true。跨实例缺陷是 B 的事件修改了 A；不得写成
B 的计数为 2，或写成 A.C2 永久阻断后续 normal。

这类 TestCase 的触发环境和通过标准不能混写：触发环境是在同一 Lua VM 创建 A、B；正确通过标准
是 A 的一次事件只执行 A 自己的每类 callback 一次，B 不因 A 的事件发生字段变化。当前实现若让
A 的计数大于 1、callback 表出现 A/B 两套注册或 A 触发 B 的闭包，这些值属于 RiskCard 的
`system_result`、TestCase 的 `failure_observation` 或 reviewer 的 `current_behavior`，不能成为
TestCase 的 `expected_result`。

`analysis_checkpoint.failure_paths[].disposition=excluded` 只表示该路径不生成 RiskCard，不表示正常
成功行为无需需求回归测试。需求/设计规定的成功基线仍可、且在未被其他用例等价覆盖时必须生成
TestCase。reviewer 不得仅因正常 path 为 `excluded` 就要求删除成功用例或把正常 path 改成 risk。

## 模块与原生边界

- `require` 缓存按组件自己的 Lua VM 判断；组件重启、VM 重建与普通函数重试是不同路径。
- Lua 调用 C/C++ 共享库时，检查参数、返回值、错误传播和资源所有权。task 未提供原生实现或
  权威契约时，不把原生内部副作用升级为确定风险。
- 资源接口调用返回失败时，继续追踪失败前已发布的属性、事件、订阅或设备状态；接口允许失败
  不能证明状态已经恢复。

风险和测试应落到组件启动、状态发布、资源访问、事件通知、停止及重启等可执行入口。不要在
本规则中加入 Redfish、IPMI、NCSI、固件升级等完整 BMC 领域检查；只有 task 的冻结源码、资料或
后续领域规则明确要求时才分析这些行为。
