# NVMe-oF 专项分析方法

## 适用条件

只在冻结源码或当前资料中出现 NVMe-oF/NVMf、Fabrics Connect、discovery controller、subsystem/NQN、
NVMe/TCP、NVMe/RDMA、admin/IO qpair 或对应认证与传输实现时采用本方法。通用 NVMe PCIe、普通 TCP
或普通 RDMA 路径不能自动套用 NVMe-oF 状态和恢复结论。

先确认 host/target 角色、transport、支持的 NVMe 版本与能力。SPDK、Linux nvme/nvmet 和 nvme-cli
测试只提供分析维度；其超时、默认值、状态名和恢复策略不是目标实现的默认事实。

## 分析步骤

1. 从 discovery 或静态配置入口建立 `listener -> subsystem -> controller -> admin qpair -> IO qpair ->
   namespace/request` 层级，记录 NQN、host 身份、transport 地址和资源所有者。
2. 重放 discovery、transport 建链、Fabrics Connect、controller 建立、admin queue 可用、IO queue 建立、
   namespace 可见和首个 IO。每一步记录已经创建的连接、qpair、request、poller/timer、buffer 和认证状态。
3. 将 TCP、RDMA 和其他 transport 分开分析。只有上层 controller/qpair 处理确实共享时才能合并结论；
   transport 专属连接、完成、超时、资源和错误状态不得互相代替。
4. 对 TLS/PSK 与 DH-HMAC-CHAP 分开检查配置、身份绑定、能力协商、成功后的允许命令、失败后的断开和
   资源清理。双向认证、算法选择、密钥轮换、重新认证或 secure channel 拼接只在源码明确支持时分析。
5. 对 admin/IO qpair 分别追踪队列创建、深度/额度、request 分配、提交、完成、超时、abort、disconnect
   和释放。Connect 成功不证明所有 IO queue 已建立；命令提交成功也不证明 completion 已被处理。
6. 检查 capability 与参数协商：取值来源、共同能力选择、无交集处理、回退是否被契约允许，以及协商值
   是否真正传到 transport/controller/qpair。不得把静默降级当成普遍正确行为。
7. 从 target 退出、listener 移除、链路中断、keep-alive/command 超时、认证变更和 queue 错误分别重放
   controller loss、重连、reset、failover 或最终移除。不同触发及终态分别形成 failure path。
8. 对 multipath/failover，逐 path 记录活跃状态、在途 IO、切换条件和最终 controller 状态。一条 path
   存活不能证明 IO 已切换；最后一条 path 移除后的 controller 终态也不能由前面的成功切换推断。
9. 断开或失败后确认 qpair/request/buffer/timer/poller 是否释放；随后用同一受支持入口重新建链并执行
   IO，验证资源额度和服务能力真正恢复。只看到析构被调用不足以证明异步完成已经排空。

## 证据与风险判定

- 每条风险绑定一个 transport、一个认证组合、一个 queue/controller 状态和一个唯一终态。
- 认证/协商失败若按当前契约拒绝连接、释放本次资源且不影响既有连接，是正常负向行为，不形成风险。
- 资源不足必须绑定具体 pool/queue/request 额度、实际申请顺序和返回处理；“高并发可能耗尽”不是证据。
- 重连或 failover 风险必须证明在途 IO 的终态及新 IO 的外部结果，不能只引用内部 controller 状态。
- 参考项目中的 key rotation、multipath、TLS、特定 digest/dhgroup 或 transport 能力，只有当前源码或资料
  声明支持时才能成为测试期望。
- peer、内核 NVMe 层或 transport 对关闭/flush 的语义不在冻结输入时，保持 `UNRESOLVED`，说明缺失
  的版本、契约或实现；不得用另一 transport 的行为补齐。

## 转换为测试

- 基线至少包含发现/连接、namespace 可见和真实 IO；只验证 controller 名称出现不能代替数据路径成功。
- 认证测试从产品连接入口执行，分别覆盖正确凭据、错误/缺失凭据和能力无交集；协商算法或内部状态
  只作辅助观测。现有连接在密钥变更后的行为必须按当前契约写唯一预期。
- connect/disconnect 压力按“连接成功 -> IO -> 断开完成 -> 资源回到基线”逐轮验证，末轮再次连接并 IO。
- target 重启、listener 移除、链路中断和 multipath 切换分成不同用例；每条用例明确在途 IO、新 IO、
  controller/path 状态和恢复超时的判据。
- queue/共享 buffer 耗尽用例使用源码比较式确定并发数或队列深度，验证失败请求、既有连接和释放后的
  新连接三者结果；不能只把创建参数调大并期待失败。
- 内部 capsule、qpair 或 transport 故障需要开发工具时写成灰盒前置条件，测试人员仍从 discovery、
  connect、disconnect、IO 和管理接口观察。
