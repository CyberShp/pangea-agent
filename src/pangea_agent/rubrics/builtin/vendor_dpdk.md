# DPDK 专项分析方法

## 适用条件

只在冻结源码或当前资料中出现 DPDK EAL、`rte_*`、PMD、mbuf/mempool、ring、ethdev、lcore、hugepage
或明确的 DPDK datapath 时采用本方法。普通轮询、无锁队列或 DMA 代码不能仅凭相似结构套用 DPDK
语义；具体 PMD 和厂商扩展另按对应证据分析。

先确认 DPDK 版本、process 类型、PMD、NUMA 拓扑和实际使用的 API 模式。DPDK 主仓库的 app/test 与
Programmer's Guide 提供检查维度；测试常量、默认 cache、socket 和 queue 数不是目标产品默认值。

## 分析步骤

1. 建立 `EAL/process -> device/port -> RX/TX queue -> ring/mempool -> mbuf -> burst` 资源层级，
   标出 primary/secondary、lcore 和 socket 的所有者与共享边界。
2. 重放初始化顺序：EAL、设备探测/配置、queue setup、mempool/ring 创建、port start、lcore 启动。
   每一步失败时核对已创建对象、已启动线程/port 和共享内存是否按依赖逆序处理。
3. 对 RX/TX burst 记录请求数量、实际返回数量、未发送/未接收对象的所有权和后续动作。burst 返回部分
   数量不是全成功；重试、丢弃和释放必须逐对象或逐批次可证明。
4. 对 mbuf 追踪来源 mempool、segment 链、clone/indirect buffer、external buffer、引用计数和最终
   归还位置。释放一个 packet 是否归还全部 segment，必须按当前构造和 API 路径判断。
5. 对 mempool 区分全局池、per-lcore cache、外部 cache 和实际 in-use 对象。检查 get/put 的 bulk
   失败语义、cache flush、跨 lcore 归还、pool 销毁前的存活对象和重复初始化后的同名资源。
6. 对 ring 确认容量语义、SP/SC 或 MP/MC 模式、bulk 与 burst 的返回差异、full/empty 边界，以及
   zero-copy start/finish 的保留区是否在所有失败路径提交或取消。模式与真实生产者/消费者数量必须一致。
7. 对 NUMA 逐 queue/lcore/device 核对 socket 选择、内存来源和跨 socket 访问。只有当前环境与配置明确
   存在跨 NUMA 路径时才形成性能或容量结论。
8. 对并发 datapath 记录每个 lcore 的启动、停止标志、退出确认和共享状态同步。控制面停止 port、释放
   queue/ring/mempool 前，必须证明数据面不再访问这些对象。
9. 对设备错误、queue full、mempool empty、链路变化、热插拔和异常退出，追踪 PMD 回调/轮询结果、
   未完成 burst、资源回收和重新 start 后的能力。PMD 专属行为不能推广为所有 DPDK 驱动。

## 证据与风险判定

- 每条风险绑定具体 DPDK API 模式、资源对象、lcore/queue 和返回值；不能只写“DPDK 资源泄漏”。
- `rte_*` 调用成功只证明该层接受操作，不证明 NIC 完成、completion 已回收或对端收到数据。
- pool empty、ring full 或 burst 短返回若被正确处理、既有报文所有权清晰且释放后可恢复，不形成风险。
- 只有计数、cache 或 NUMA 差异能落到持续吞吐下降、丢包、阻塞、资源不可用或无法重启时才形成系统风险。
- 当前源码未冻结 PMD、EAL 或 secondary process 的外部语义时保持 `UNRESOLVED`；不得用 testpmd 或
  某一 PMD 的实现补成产品结论。
- `SIGTERM`、`SIGKILL`、进程崩溃和正常退出的回收能力不同。只有产品契约承诺对应场景时才写恢复预期。

## 转换为测试

- 正常基线至少包含 port/queue 就绪、稳定收发和资源计数；仅成功启动 EAL 不算 datapath 通过。
- burst 测试覆盖 0、部分和完整返回，逐批次验证未发送 mbuf 的重试/释放及对端实际收到的数量。
- mempool/ring 耗尽使用真实容量关系制造，验证首次失败、既有流量、释放后的新申请和最终收发恢复。
- NUMA 用例固定 lcore、queue、device 和内存 socket，并分别记录吞吐、时延和错误；不把不同拓扑混在
  一条用例中。
- stop/restart 用例明确停止数据面、等待 lcore 退出、停止 port、释放资源和重新建立的顺序；末尾执行
  真实收发。需要强制退出的场景另立用例。
- 内部 mbuf/ring/mempool 状态只作灰盒辅助观测，产品级判据仍是报文、连接、服务、管理状态和恢复能力。
