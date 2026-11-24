# Lua 三轨深潜检查规程（risk-method-v2）

配合 `scene_risk_v2.md` 使用，严格遵守 Lua 语义（仅 `nil`/`false` 为假、`0` 和 `""` 为真、区分 `error()` 抛错与 `nil, err` 双返回、`__gc`/`__close` 时机不可依赖）。

## 轨道一：异常分支与状态回滚（`error_rollback`）

1. **半提交状态回滚**：枚举修改 `self.*`、模块级表或外部注册状态之后可能抛错（`error`/`assert`/未保护的 `skynet.call`）或提前返回的路径，核对已改字段是否回滚。
2. **异常约定混用**：检查调用方用 `pcall`/`xpcall` 还是检查第二返回值；混用导致错误被吞没或把错误表当字符串拼接时记为风险。
3. **重入脏态**：失败被 `pcall` 捕获后，下次请求是否读到残留中间状态或未清理的 in-flight 标志。

## 轨道二：时序并发与协程交错（`concurrency_timing`）

以 `track_topology.concurrency_anchors` 中的 `lua_yield_point` 和 `lua_cross_yield_state` 为锚点：
1. **跨 `yield` 状态漂移**：协程 A 在 `skynet.call` / `skynet.sleep` / `skynet.wait` / `coroutine.yield` 挂起前读取 `self.*` 或模块状态，挂起期间协程 B 修改或销毁该对象，A 恢复后基于过期状态继续写入或发包。
2. **重入与乱序响应**：同一服务对并发消息是否可重入；超时后晚到的 RPC 响应或定时器回调是否误作用在已重建的新会话（缺少 generation 校验）。
3. 必须写出 `协程 A @ file:line 挂起 → 协程 B @ file:line 修改 → 协程 A 恢复` 的具体交错序列；需延迟拉窗实锤时标 `needs_runtime_verification` 并给出 `race_window_delay` 桩点。

## 轨道三：资源全生命周期（`resource_lifecycle`）

1. **异常路径句柄泄漏**：核对 `io.open`、socket、临时订阅、定时器在正常与 `pcall` 捕获异常路径上是否均显式关闭/注销（不能仅靠 `__gc`）。
2. **无限增长缓存表**：按请求/会话写入的模块级表或订阅回调表，在断连、异常或超时分支是否及时置 `nil` 摘除，是否存在无界增长泄漏。
3. **销毁后晚到回调**：对象销毁时是否注销已注册的 `skynet.timeout` 或事件回调，避免晚到回调踩到已清理的半残对象。
