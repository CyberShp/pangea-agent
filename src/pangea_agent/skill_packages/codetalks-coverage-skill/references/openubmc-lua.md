# openUBMC Lua Profile

检测到 openUBMC 目录或组件时，在 `language-lua.md` 之外读取本 Profile。它补充平台运行边界，不替代冻结源码和项目契约。

## 组件边界

先确定 service 入口、`src/lualib` 业务 module、生成代码、配置/模型文件、公共 `lualib` 搜索根和测试之间的关系。按组件 Lua VM 判断 module cache、全局变量和内存状态；跨 service 共享必须有消息、RPC、持久化或宿主框架证据。没有在仓库中找到的 `require` 只能记录为外部依赖或未决项，不得臆造实现。

## Skynet 与服务生命周期

围绕 `skynet.start`、服务注册/发现、消息 dispatch、RPC 调用和返回形成闭环：启动前置、名称唯一性、消息协议和调用方、handler 对状态/配置/硬件接口/资源的修改、返回值、错误、超时、调用方恢复、停止、重启、重复初始化和残留订阅/定时器。handler 位于宿主调度 coroutine 时，依据真实 Skynet 契约判断并发，不能仅因没有显式锁就认定竞态。

## 平台专项边界

重点核对 class 构造与 `init` 顺序、继承字段、`.`/`:` 调用、平台封装的 protected call 返回形状、顶层注册/订阅/缓存副作用、resource collaboration interface、消息总线、设备访问、持久化、timer、event subscription、watcher、RPC/session 和硬件句柄的取消、重连与幂等恢复。

LuaUnit、mock 和平台测试脚本只能证明已有意图和部分行为。应把它们映射到公开 service 入口、关键状态、错误注入和恢复 oracle，再补齐生命周期边界。
