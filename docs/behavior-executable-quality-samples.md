# 可执行业务用例质量样本

本轮根据真实源码生成并人工逐项核对的样本。未经过内网 NGA 实跑，也未在 SPDK 设备上执行；不能据此声称模型通过率或性能改善比例。

冻结来源：SPDK `b95c6aa3342e6af15c0f90066600794b4673f690`。

- [文件后端](https://github.com/spdk/spdk/blob/b95c6aa3342e6af15c0f90066600794b4673f690/module/keyring/file/keyring.c)
- [文件注册 RPC](https://github.com/spdk/spdk/blob/b95c6aa3342e6af15c0f90066600794b4673f690/module/keyring/file/keyring_rpc.c)
- [密钥管理](https://github.com/spdk/spdk/blob/b95c6aa3342e6af15c0f90066600794b4673f690/lib/keyring/keyring.c)
- [列表 RPC](https://github.com/spdk/spdk/blob/b95c6aa3342e6af15c0f90066600794b4673f690/lib/keyring/keyring_rpc.c)

以下用例共享前置条件：Linux 隔离测试实例已启动 SPDK RPC 服务并加载 file keyring 模块；有可发送 JSON-RPC 的客户端和文件管理权限。测试文件由 SPDK 进程所属用户创建；使用独立名称 `qa-key`，先查询确认没有同名密钥，不使用生产密钥。客户端连接地址由实际环境配置，不编造命令行参数。`ready` 表示执行入口与判断依据明确，不表示测试已经运行。

## 正常注册与移除

`execution_readiness: ready`，`readiness_reason: 文件操作与公开 JSON-RPC 接口均明确`。

1. 创建绝对路径测试文件 A，权限 0600；通过 JSON-RPC 发送 `keyring_file_add_key`，参数为 `{"name":"qa-key","path":"<A 的绝对路径>"}`。
   预期：响应 `result: true`。
2. 发送无参数的 `keyring_get_keys`。
   预期：存在名称为 `qa-key`、`removed: false` 的记录，其 `path` 为 A 的绝对路径。
3. 发送 `keyring_file_remove_key`，参数 `{"name":"qa-key"}`，再次查询。
   预期：移除响应 `result: true`；不存在该名称的未移除记录。不得要求列表绝对不存在任何同名项，因为仍被引用的已移除项可以保留。

恢复：确认无测试密钥在使用后删除 A。

## 拒绝其他用户可读的密钥文件

`execution_readiness: ready`，`readiness_reason: 可通过文件权限及公开注册结果验证`。

1. 创建同属主的绝对路径文件 B，权限设置为 0644；用 `qa-key` 和 B 的绝对路径发送注册 RPC。
   预期：响应包含错误，Linux 下 `error.code: -1`（EPERM），没有成功的 `result: true`。
2. 查询密钥列表。
   预期：没有新增名为 `qa-key` 的未移除记录。

恢复：将 B 权限改回 0600 后删除。无需断言某个内部权限变量或固定日志文本。

## 同名注册不能覆盖已有密钥

`execution_readiness: ready`，`readiness_reason: 注册响应及列表路径可验证原密钥保持不变`。

1. 准备权限 0600 的两个绝对路径文件 A、B，先用名称 `qa-key` 注册 A，确认成功。
2. 再用相同名称注册 B。
   预期：响应错误，Linux 下 `error.code: -17`（EEXIST）。
3. 查询列表。
   预期：该名称的未移除密钥仍关联 A，未被改成 B。

恢复：移除测试密钥，确认不再使用后删除两份文件。

## 分配失败恢复（待补设施）

`execution_readiness: needs_instrumentation`。

`readiness_reason: 当前冻结来源没有提供可由测试人员启用的内存分配故障注入设施；缺少工具、目标分配点、仅失败一次的配置及恢复方式`。

目标：注册过程中资源分配失败时不遗留可用密钥，也不影响后续正常注册。只有补齐并验证注入设施后才能编写可执行步骤；目前不写“直接调用内部函数使变量为假”，不计入具备执行条件的用例。内部 allocation 分支可以保留为分析证据。

## 核对结果与边界

- 上述三个 ready 样本通过公开 RPC 触发，预期使用 RPC 返回和列表可见状态；内部函数名只用于追溯源码。
- 注册路径只检查路径、权限、属主等条件，不校验 PSK/CRC 内容。因此不能从“文件注册成功”推出“TLS 会话成功”，也不能将 CRC 错误的拒绝点编造为注册接口。
- 本样本不添加额外全量语义复核调用；用于检查生成规则是否表达清楚。真实 NGA 输出质量及耗时仍需内网实跑验证。
