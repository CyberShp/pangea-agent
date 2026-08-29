# NVMe 核心专项分析方法

## 适用条件

只在冻结源码或当前资料中出现 NVMe controller/namespace、Admin 或 I/O command、submission/completion
queue、NVMe PCIe、NVMe status/log page，或明确的 NVMe 设备管理路径时采用本方法。NVMe-oF 的
discovery、Fabrics Connect、认证和 transport 恢复另用 NVMe-oF 方法；仅有通用块设备或 PCIe 代码
不足以套用 NVMe 协议结论。

先确认实现位于 host、controller/target、管理工具还是测试端，并确认目标声明支持的 NVMe 规范版本、
command set、transport 与可选能力。规范和参考实现提供检查方向；当前设备未声明的能力不能写成事实。

## 建立协议与资源模型

- 建立 `subsystem -> controller -> namespace -> admin queue/IO queue -> command/completion` 层级。
- 区分 controller、namespace 和 path 的身份、状态、代次与所有者；同一 namespace 的多个 path 不得
  当成多个独立数据对象。
- 记录 queue depth、CID、doorbell、submission、completion、timeout、abort、reset 和释放的作用域。
- 对 metadata、Protection Information、ZNS、reservations、firmware、sanitize 等可选能力，先由
  Identify、capability、配置或源码分支证明支持，再进入专项分析。

## 分析步骤

1. 从 probe/attach 或创建入口重放 controller enable、Admin Queue 可用、Identify、namespace 枚举、
   I/O Queue 建立和首个 I/O。每一步记录已经建立的映射、队列、request、timer、interrupt/poller
   和 namespace 引用，以及失败后的撤销顺序。
2. 对 Admin 与 I/O command 分别追踪输入字段、能力检查、提交、完成状态和调用方处理。命令被提交
   不等于设备已完成；completion 到达也不等于数据、metadata 和上层请求已一致结算。
3. 对 SQ/CQ 记录生产者、消费者、head/tail 或 phase 的更新点、queue full 判定、CID 复用条件和
   completion 消费顺序。reset 前后的旧 completion 不得结算到新代次命令。
4. 对 namespace attach/detach、rescan、format、容量或 LBA 格式变化，核对旧设备对象、开放句柄、
   在途 I/O、缓存信息和新枚举结果。管理命令成功不能单独证明上层已经看到一致拓扑。
5. 对 read/write/flush/compare/write zeroes/DSM 等已支持命令，分别检查 LBA、长度、对齐、数据方向、
   FUA/flush 顺序、部分完成和错误状态。未支持命令被明确拒绝是正常行为。
6. 对 error log、SMART/Health、AER 和 telemetry 等已支持观测，确认读取时机、日志代次、异步事件
   ack/重发和上层通知。日志出现事件不证明对应恢复已经完成。
7. 从 command timeout、queue stall、controller fatal、PCIe 错误、surprise removal、firmware
   activation 和主动 reset 分别追踪停止新提交、在途命令终态、queue/controller 重建和 namespace
   重新可见。不同 reset 层级不得合并成一个恢复路径。
8. 对 multipath/ANA 仅在当前实现支持时逐 path 记录状态、选择、切换与在途 I/O 终态；一条 path
   可用不证明故障 path 的命令已经安全结算。
9. 恢复后重新执行 Identify/枚举和真实读写，确认 queue/CID/request 额度、namespace 可见性、数据
   结果及健康状态回到可再次使用的基线。

## 证据与风险判定

- 每条风险必须绑定具体 controller/namespace、命令、queue、失败状态、调用方处理和外部结果。
- status code、DNR、日志字段和 capability 按目标规范版本及当前实现解释；不得用另一版本枚举补齐。
- timeout 或 reset 只有在导致命令终态矛盾、数据错误、设备永久不可用、资源不可回收或恢复后仍无法
  I/O 时形成风险；一次可控失败本身不是风险。
- 边界或可选能力被明确拒绝、既有状态未受损且修正输入后可恢复时，是正确保护。
- 设备固件、PCIe 层或内核 block layer 的语义不在冻结输入时保持 `UNRESOLVED`，说明需要的规范、
  capability、日志或运行证据。

## 转换为测试

- 基线至少包含 controller/namespace 枚举、Identify 与真实数据读写；只看到设备节点不足以通过。
- command 用例覆盖支持、非法字段/边界、设备返回错误和恢复后的再次提交，每次只改变一个条件。
- queue/command 压力使用当前 queue depth、request 额度或源码比较式确定边界，验证首次失败、既有
  I/O、drain 后新 I/O 和最终资源恢复。
- namespace、format、firmware 与 sanitize 操作可能破坏数据，必须标明专用设备、备份、清理和不可
  逆边界；不能在普通回归环境默认执行。
- timeout、reset、surprise removal 和 multipath 切换分别成用例，明确在途 I/O 唯一终态与恢复时限。
- 内部 SQ/CQ、CID、doorbell 或寄存器只作灰盒辅助观测，产品级判据仍是设备、数据、I/O、日志和恢复。
