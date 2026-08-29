# NVIDIA / DOCA 专项分析方法

## 适用条件

只在冻结源码或当前资料中出现 DOCA SDK、`doca_*`、BlueField/DPU、DOCA device/context/PE/task、
mmap/buf inventory、DOCA RDMA/DMA/Flow/Comch 等明确路径时采用本方法。仅有 NVIDIA NIC、CUDA、
mlx5、DPDK 或普通 RDMA 不能套用 DOCA 生命周期和错误语义；对应专项规则应独立使用。

先确认目标使用的 DOCA 版本、库、host/DPU 侧、设备模式和 capability 查询。NVIDIA 官方 samples
展示的是特定版本的正确调用形状，不是产品契约；API 顺序、错误码、权限和恢复要求必须与目标源码及
当前安装版本一致。

## 分析步骤

1. 建立 `doca_dev -> PE -> context -> connection -> mmap/buf inventory/buf -> task -> completion`
   依赖图，标出 host、DPU、peer 和控制面/数据面的对象边界。
2. 按当前库重放 configure、capability/permission 设置、connect PE、start context、建立连接、分配 task、
   submit、progress、completion/error callback、stop 和 destroy。顺序中每一步的已创建资源单独记录。
3. 对 capability 检查确认查询对象、设备、任务类型、队列/连接大小和 transport。SDK 支持某 API 不证明
   当前 device/firmware 支持；unsupported 被正确拒绝时不形成风险。
4. 对 mmap/buf 检查内存来源、范围、local/remote 权限、export/import descriptor、buffer inventory、
   refcount 和底层内存寿命。descriptor 可交换不证明 peer 获得了目标操作所需权限。
5. 对 task 记录 task 类型、user data、关联 buffer、提交返回、in-flight 计数、成功/错误 callback 和
   task free。`doca_task_submit()` 成功只表示已接受；必须由 PE 推进到 completion 才能更新业务结果。
6. 对 progress loop 确认谁持续调用 `doca_pe_progress()`、停止条件、空轮询、事件模式和线程归属。
   没有 completion 时先区分未推进、peer 未就绪、connection 断开、queue full 和真实 task error。
7. 对 RDMA/DMA/其他库分别检查任务权限、方向和 peer 角色。DOCA RDMA 的连接、mmap 权限或恢复动作
   不得推广到 DOCA DMA、Flow、Comch 或通用 verbs。
8. 对错误 callback、连接失败/断开、queue full、bad state、not permitted 和 device error，按目标源码
   实际处理追踪：是否停止新提交、如何处理在途任务、何时 stop context、错误如何上报给产品层。
9. stop 时继续推进 PE 直至 context 到达可销毁状态，并确认在途 task 的 completion/flush 和释放；随后
   按 task/buf、connection、context、inventory/mmap、PE、device 的真实依赖逆序清理。
10. 恢复路径必须重新确认 capability、context/connection 状态、descriptor 与 buffer generation，最后
    提交新 task 并收到 completion。重新 start 成功不能单独证明数据面恢复。

## 证据与风险判定

- 每条结论注明 DOCA 库、API/ABI 版本、运行侧、device/capability 和任务类型；缺一项时不推广。
- 官方 sample 只能证明一种参考顺序。目标代码与 sample 不同不自动构成风险，必须结合当前 API 契约、
  返回处理和系统结果判断。
- `DOCA_ERROR_*` 名称只能按目标版本解释。相同错误在 configure、submit、progress 或 callback 阶段的
  资源后果不同，不能合并。
- context stop/destroy、mmap stop/destroy 和 task flush 涉及外部 SDK 状态；冻结输入不足以确认时保留
  `UNRESOLVED`，明确缺少的版本文档、callback 或运行证据。
- 只有错误导致任务结果错误、永久无 completion、资源额度下降、连接/服务不可用、数据损坏或移除故障
  后仍无法恢复时，才形成产品风险。
- 厂商 sample、文档或其他版本观察到的行为不能作为当前 RiskCard 的唯一证据，必须同时有目标源码或
  当前资料证据。

## 转换为测试

- 先用目标产品支持入口完成一次已知可用的正向任务，并从 completion 与数据结果建立基线；只编译成功
  或 submit 成功不算运行通过。
- capability/permission 负向用例每次只改变一个 task 类型、权限、设备或连接条件，验证明确拒绝、既有
  context 状态和修正配置后的新任务成功。
- peer 测试写清双方启动顺序、descriptor/连接信息交换、哪一侧提交、哪一侧提供内存，以及双方的
  completion/数据观测。单侧无 peer 运行产生的等待不能直接判成产品缺陷。
- progress、queue 和 in-flight 压力使用当前配置值确定任务数，验证短暂 full/无进展、drain 后继续提交
  和最终资源恢复；不得用无限轮询掩盖停止条件。
- error callback、断连、stop 和重启分开用例。context 或 mmap 已销毁后重新创建并交换新 descriptor，
  不复用旧 task、buf 或 connection。
- 需要 BlueField、特定 firmware 或 host/DPU 协同时标为明确前置条件；环境不具备时保持
  `Developer-confirm`，不能以无硬件的 smoke test 冒充真实完成。
