# openUBMC Lua 专项分析方法

仅在 task 已选择本 rubric 时应用。它补充通用 Lua 语义，不替代源码和项目自身契约。

## 组件与运行边界

- 先确定当前组件的 service 入口、`src/lualib` 业务 module、生成代码、配置/模型文件和测试之间的关系。
- 按组件 Lua VM 隔离判断 module cache、全局变量和内存状态；跨 service 状态共享必须有消息、RPC、持久化或宿主框架证据。
- 核对部署后的 component `lualib` 与公共 `lualib` 搜索根、module 名称和文件路径映射。仓库内没有找到的 require 可能来自平台公共库。
- `gen`、MDS、service/model JSON 等生成或声明输入作为接口证据；只有当前功能依赖其具体实现时才提升为分析上下文。

## Skynet 生命周期与消息路径

围绕 `skynet.start`、service 注册/发现、消息 dispatch、RPC 调用和返回形成一条闭环：

1. service 启动前置条件与初始化顺序；
2. 名称注册、唯一 service 或依赖 service 的可用性；
3. 消息协议、command、参数和调用方身份；
4. handler 对对象状态、配置、硬件接口或资源的修改；
5. 返回值、错误传播、超时和调用方恢复；
6. 停止、重启、重复初始化和残留订阅/定时器的处理。

消息 handler 运行于宿主调度的 coroutine 时，依据 Skynet 调度和当前封装的真实契约判断并发。不得仅因 Lua 代码没有显式锁就形成竞态结论。

## openUBMC 编码约定对应的风险点

- class 的构造与 `init` 分工、继承链和对象字段初始化顺序；
- `obj:method()` 与 `obj.method()` 的 `self` 绑定是否与定义一致；
- 普通 `pcall`、`xpcall` 及平台封装（例如 bus/RPC protected call）的返回形状和错误分支；
- service/module 顶层执行的注册、订阅和缓存副作用；
- resource collaboration interface、消息总线、设备访问和持久化接口的故障注入点；
- timer、event subscription、watcher、RPC/session 和硬件句柄的取消、重连与幂等恢复。

## 测试证据

LuaUnit 测试、mock 和平台测试脚本可以证明已有意图、输入形状和部分行为，但不能单独证明生产路径完整或 Coverage 已闭环。把已有测试映射到公开入口、关键状态、错误注入和恢复 oracle，再补齐未覆盖的生命周期边界。

优先生成测试人员能操作的 component/service 级用例：配置或模型输入、消息/RPC、设备状态、依赖 service 故障、超时、重启和重复请求。源码函数名只用于定位证据。
