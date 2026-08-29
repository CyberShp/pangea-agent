# mlx / RDMA 专项分析方法

## 适用条件

只在冻结源码或当前资料中出现 libibverbs/librdmacm、QP/CQ/PD/MR/MW/SRQ、RDMA CM、RoCE/IB、
mlx4/mlx5、DevX、doorbell 或对应驱动路径时采用本方法。先区分通用 verbs、RDMA CM、mlx direct
verbs/DevX 和上层协议；厂商路径的状态、限制和恢复不能推广到通用 RDMA。

先确认设备/provider、link layer、transport、QP 类型和能力查询来源。`rdma-core` 与 mlx5 tests 提供
状态和故障检查方式；设备上限、错误码、CQE 格式和恢复动作仍必须由目标版本的源码或资料证明。

## 分析步骤

1. 建立 `device/context -> PD -> MR/MW -> CQ/SRQ -> QP/CM connection -> WR/CQE` 依赖图，
   记录每个对象的创建者、共享者、销毁前置条件和异步事件渠道。
2. 重放 QP/connection 的实际状态迁移，区分 RESET、INIT、RTR、RTS、SQD、ERR 及销毁。只按当前
   QP 类型和 attr mask 判断合法迁移；创建成功或 CM established 不证明数据面已经可收发。
3. 对 post send/recv 记录 WR 链、成功前缀、`bad_wr`、queue 深度、signaled/unsignaled 和 fence。
   一次 post 部分失败时，已提交 WR 与未提交 WR 的所有权和完成预期必须分开。
4. 对 CQ 追踪 completion 产生、poll、event notification、re-arm、ack 和 overrun。提交成功不证明
   CQE 已消费；收到 event 也不证明所有 completion 已 drain。
5. 对 MR/MW 记录 PD、地址/长度、lkey/rkey、access flags、registration/reregistration/deregistration
   和 DMA buffer 寿命。旧 WR 引用旧地址、旧 key 或旧 PD 时，继续追踪 completion、QP 错误和恢复。
6. 对 buffer、DMA、doorbell、UAR/BF 和 WQE/CQE 内存顺序，只在目标源码存在 direct verbs/DevX
   或显式 MMIO 路径时分析。通用 verbs 调用方不能凭 mlx5 实现细节推导 doorbell 风险。
7. 对 async event 分别处理 QP、CQ、SRQ、port/device 和 CM 事件，记录事件对象、ack、状态更新、
   在途 WR 终态和上层通知。日志出现事件不等于资源已经恢复。
8. 对 queue overflow、CQ overrun、remote access/operation error、连接断开和设备事件，追踪 QP 是否
   进入 ERR、未完成 WR 是否 flush、completion 是否排空，以及 reset/recreate 后能否恢复真实 traffic。
9. 销毁时确认停止新提交、处理/取消在途 WR、drain/ack CQ 与 async event，再按 QP/SRQ/CQ/MR/PD/
   context 依赖释放。外部组件自动 flush 或销毁的语义必须有当前 provider 契约。

## 证据与风险判定

- 每条风险固定 provider、QP 类型、操作类型、local/remote 角色、失败 completion/event 和唯一终态。
- 非法 capability/attr 被创建接口拒绝且没有残留资源，是正常负向行为，不形成风险。
- remote access error 不能只归因于网络；必须核对 rkey、权限、地址、MR 生命周期和 peer 状态。
- QP 进入 ERR 后是否可 reset 回 RTS、是否必须重建，按目标调用路径证明。参考测试中的恢复序列不能
  自动成为产品承诺。
- 只看到 CQ 未 poll、doorbell 写入或 refcount 不对称不足以形成风险；必须证明 completion 丢失、
  数据错误、队列永久不可用、资源不可回收或恢复失败。
- provider/firmware 对 flush、事件或销毁的语义不在冻结输入时保留 `UNRESOLVED`，明确需要查询的
  capability、event 或 provider 契约。

## 转换为测试

- 基线先建立双方连接、完成一组真实 send/recv 或 RDMA read/write，并记录 QP/CQ 与业务结果。
- capability 边界测试使用设备查询值构造边界内与首次越界配置；不硬编码另一设备的 queue/SGE 上限。
- 权限或 MR 变更测试按“成功 traffic -> 改变一个权限/映射 -> 预期失败 completion -> 恢复映射或
  重建 QP -> traffic 再成功”执行，每一步使用同一对象代次。
- CQ/queue 压力明确是否停止 poll、预计产生多少 WR/CQE、何时恢复 drain，并观察事件、完成和后续
  traffic；不能仅以等待超时判定 overrun。
- 断链、port event、QP error 和 device reset 分开用例。对象被销毁后重新创建资源，不使用旧 MR/QP。
- mlx/DevX 专项条件需要开发工具时写成灰盒前置条件；主要 PASS/FAIL 仍来自 completion、连接、数据、
  服务状态和恢复后的真实 traffic。
