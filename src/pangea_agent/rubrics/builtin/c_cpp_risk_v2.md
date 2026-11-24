# C/C++ 三轨深潜检查规程（risk-method-v2）

配合 `scene_risk_v2.md` 使用，严格遵守 C/C++ 真实求值语义（短路求值、整数提升、宏展开副作用、Release 下 `assert` 消失）。

## 轨道一：异常分支与状态回滚（`error_rollback`）

以 `track_topology.error_slices` 和 `risk_blueprint.transaction_chains` 为锚点：
1. **副作用回滚账本**：对每条在产生副作用（内存/句柄申请、回调/定时器注册、加锁、全局/结构体状态字段写）之后的 `return`、`goto`、循环 `break`，逐项核对前置副作用是否按逆序完整撤销。
2. **错误码吞没与覆盖**：检查清理阶段的 `close()`/日志/回滚函数返回值是否覆盖了原始失败码 `rc`，或将失败静默映射成“空结果/成功”。
3. **跨调用脏态继承**：若本次失败修改了 `static` 标志、缓存、状态机字段或半填充输出缓冲区，追踪下一次正常调用或重试是否会读到脏态导致级联失效。
4. **打桩配方**：无法靠正常入参触发的内部失败（如 `kmalloc`/`malloc` 失败、底层 I/O 错误），标为 `needs_runtime_verification` 并给出 `kind="fault_return"` 桩点。

## 轨道二：时序并发与异步交错（`concurrency_timing`）

以 `track_topology.concurrency_anchors`、`shared_access_matrix` 和 `risk_blueprint.concurrency_contexts` 为锚点：
1. **无锁/半锁共享状态**：对照 `shared_access_matrix` 核查跨线程/回调/中断读写的同一结构体字段或全局变量；检查是否存在“读-改-写”跨越锁释放点（TOCTOU）。
2. **锁纪律与阻塞**：核对 `lock_held_calls`（持锁期间调用阻塞 I/O、用户回调或申请第二把锁），追踪 AB-BA 锁序反转与错误路径漏解锁。
3. **异步取消与晚到完成（Late Completion）**：重点排查超时定时器/主动销毁与异步完成回调并发命中同一对象；必须写出两路执行流的具体行号交错（写入 `interleaving_or_path`）。
4. **打桩配方**：依赖窄时间窗的竞态标为 `needs_runtime_verification`，给出 `kind="race_window_delay"` 延迟拉窗桩点及 ASAN/TSAN 观测判据。

## 轨道三：资源全生命周期（`resource_lifecycle`）

以 `track_topology.resource_anchors` 和 `risk_blueprint.resource_entities` 为锚点：
1. **全路径配平**：从获取点沿正常返回、所有错误退出、取消、reset/重连、优雅停止追踪到释放或所有权转移；跨文件配对必须穿透读对侧源码。
2. **引用计数与对象池**：核对初值、失败分支漏 `put`/`unref`、提前减至 0 后的悬空指针、重复释放（Double Free）以及批量申请第 k 个失败时前 k-1 个回收。
3. **注销与悬空回调**：对象销毁前是否同步摘除链表节点、注销定时器与回调订阅，防止晚到事件解引用已释放对象。
4. **打桩配方**：长稳/耗尽/异常路径泄漏标为 `needs_runtime_verification` 时，给出 `kind="leak_counter_probe"` 计数观测或 `fault_return` 桩点。
