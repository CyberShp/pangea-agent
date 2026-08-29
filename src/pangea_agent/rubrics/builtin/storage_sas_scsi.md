# SAS / SCSI 磁盘专项分析方法

## 适用条件

只在冻结源码或当前资料中出现 SCSI command/sense、SAS PHY/port/address、SSP/STP/SMP、expander、
libsas、HBA、SCSI disk、SES/enclosure 或明确的 SAS 磁盘路径时采用本方法。普通块设备、SATA/ATA
或任意名为 queue/reset 的代码不能自动套用 SAS/SCSI 结论。

先确认当前层次是 SCSI 命令、SAS transport、HBA/LLDD、expander、磁盘还是 enclosure，并确认
initiator/target 角色、设备类型、协议代际和已声明能力。SAS transport 与 SCSI command set 分层
分析；SATA 盘经 STP/SAT 接入时不能冒充原生 SSP/SAS 盘。

## 建立拓扑与任务模型

- 建立 `host/HBA -> phy -> port/wide port -> expander/rphy -> target -> LUN/disk -> command/task` 层级。
- 区分 SSP、SMP 和 STP 的请求、完成、超时与恢复；不同协议的状态和错误不能互相代替。
- 记录 SAS address、phy identifier、link rate、routing、slot/enclosure 映射和设备代次。
- 建立 SCSI `CDB -> queue -> transport -> status/sense/residual -> retry/EH -> upper layer completion` 时序。

## 分析步骤

1. 从 HBA 初始化、PHY enable 和 domain discovery 重放端口形成、expander 发现、route/phy 查询、
   target/LUN 注册和磁盘可用。每一步记录对象所有者、异步任务、引用和失败清理。
2. 对 link negotiation、wide port 和 expander 拓扑检查单 PHY 失败、速率降级、路径变化、环路或
   重复发现。链路仍为 up 不证明目标/LUN 与数据路径可用。
3. 对 SCSI command 记录 CDB 长度、service action、数据方向、allocation/transfer length、timeout、
   status、residual 和 sense。transport 成功但 CHECK CONDITION，或 status 成功但 residual 异常，
   都不能按完整成功处理。
4. 分解 fixed/descriptor sense、sense key、ASC/ASCQ 和 Unit Attention。是否重试、重扫、上报或
   失败必须由当前命令、sense 和调用上下文决定，不能把所有 NOT READY 或 UA 统一重试。
5. 对 queue full/task set full、busy、aborted command、reservation conflict 和 medium/hardware
   error 等当前支持状态，追踪队列额度、退避、命令唯一终态和上层可见结果。
6. 按当前错误处理层级检查 abort、LUN reset、target reset、bus/host reset 或 PHY reset 的升级条件。
   低层恢复成功不自动证明原命令重放安全；升级后旧 task/completion 不能结算到新设备代次。
7. 对热插拔、拔盘、expander/链路故障和 HBA reset，追踪停止新命令、在途任务、设备移除、重新发现、
   slot/WWID 映射和 multipath 状态。相同盘位重新出现不证明它仍是原设备。
8. 对双端口/multipath、persistent reservation、ALUA 或 enclosure SES，只在源码和资料明确支持时
   分析 path group、所有权、注册 key、slot 控制与故障切换；管理面成功不代替真实 I/O。
9. 对健康与维护路径检查 LOG SENSE、INQUIRY/VPD、自检、缺陷、温度、PHY error counter、firmware、
   format/sanitize 等已支持命令的长度、版本、状态和破坏性边界。
10. 恢复结束后重新验证设备身份、容量/LBA 格式、path、reservation、健康状态和真实读写，并确认
    task、queue、PHY/port 与 device 引用回到可再次故障恢复的基线。

## 证据与风险判定

- 每条风险固定 initiator/target、协议层、设备/path、CDB 或管理动作、sense/status 和唯一外部结果。
- sense、ASC/ASCQ、link rate 和 protocol identifier 必须按当前返回长度及目标版本解析；截断或未知
  字段不能用默认零值补成设备事实。
- 正确返回 CHECK CONDITION、明确 sense、保持既有设备且修正条件后可恢复，是正常协议行为。
- 自动重试风险必须证明命令是否幂等、是否已经产生介质副作用以及最终是否可能重复完成。
- 外部 HBA firmware、expander、磁盘或 enclosure 语义缺失时保持 `UNRESOLVED`，列明需要的日志、
  VPD/sense、拓扑和运行证据，不用另一型号设备补齐。

## 转换为测试

- 基线记录 HBA、SAS address/WWID、PHY/port、slot、LUN、容量和真实读写，避免故障后认错设备。
- 命令负向测试分别覆盖非法字段、边界长度、CHECK CONDITION、residual 和超时，逐项验证 sense、
  上层错误和后续命令。
- 使用 `scsi_debug` 或等价受控工具时，只注入 timeout、queue failure、specific status/sense 等输入；
  不让工具直接制造期望的泄漏、重复完成或恢复结论。
- 单 PHY、wide port、expander、单 path、双端口和整盘移除分开用例；每条写清在途 I/O、设备状态、
  重新发现和恢复后的数据校验。
- format、sanitize、firmware、PR 抢占和 enclosure 控制属于破坏性或共享状态操作，必须使用专用环境、
  明确恢复步骤并确认不会作用到非目标盘。
- 长稳用例循环发现、I/O、故障与恢复，比较 PHY error、queue/device 数量和业务能力；末轮必须再次
  完成真实 I/O。
