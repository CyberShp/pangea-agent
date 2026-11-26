# C/C++ 四轨深潜检查规程（risk-method-v2）

配合 `scene_risk_v2.md` 使用，严格遵守 C/C++ 真实求值语义（短路求值、整数提升/无符号回绕、宏展开副作用、Release 下 `assert` 消失）。四条轨道职责严格正交，互不重报。

## 轨道一：异常分支与状态回滚（`error_rollback`）

以 `track_topology.error_slices` 和 `risk_blueprint.transaction_chains` 为锚点：
1. **副作用回滚账本**：对每条在产生副作用（内存/句柄申请、回调/定时器注册、加锁、全局/结构体状态字段写）之后的 `return`、`goto`、循环 `break`，逐项核对前置副作用是否按逆序完整撤销。
2. **错误码吞没与覆盖**：检查清理阶段的 `close()`/日志/回滚函数返回值是否覆盖了原始失败码 `rc`，或将失败静默映射成“空结果/成功”。
3. **跨调用脏态继承**：若本次失败修改了 `static` 标志、缓存、状态机字段或半填充输出缓冲区，追踪下一次正常调用或重试是否会读到脏态导致级联失效。
4. **打桩配方**：无法靠正常入参触发的内部失败（如 `kmalloc`/`malloc` 失败、底层 I/O 错误），标为 `needs_runtime_verification` 并给出 `kind="fault_return"` 桩点。

## 轨道二：锁纪律与存活态并发竞态（`concurrency_timing`）

以 `track_topology.concurrency_anchors`、`shared_access_matrix` 和 `risk_blueprint.concurrency_contexts` 为锚点（聚焦**存活对象**竞态；销毁/释放后跨流踩内存归轨道四）：
1. **存活态无锁/半锁共享读写**：对照 `shared_access_matrix` 核查跨线程/回调/中断读写的同一存活结构体字段或全局变量；检查“读-改-写”或“检查-使用（TOCTOU）”是否跨越锁释放点。
2. **锁纪律、死锁与持锁阻塞**：核对 `lock_held_calls`（持锁期间调用阻塞 I/O、用户回调或申请第二把锁），追踪 AB-BA 锁序反转与异常退出漏解锁。
3. **打桩配方**：依赖窄时间窗的存活态竞态标为 `needs_runtime_verification`，给出 `kind="race_window_delay"` 延迟拉窗桩点及 TSAN/状态不变量观测判据。

## 轨道三：资源配平与泄漏/重复释放（`resource_lifecycle`）

以 `track_topology.resource_anchors` 和 `risk_blueprint.resource_entities` 为锚点（聚焦**有借无还与重复释放**；释放后悬空访问踩内存归轨道四）：
1. **全路径配平防泄漏**：从获取点沿正常返回、所有错误退出、取消、reset/重连、优雅停止追踪到释放或所有权转移；跨文件配对必须穿透读对侧源码。
2. **引用计数泄漏与 Double Free**：核对初值、失败分支漏 `put`/`unref` 导致永不释放、异常清理与正常清理重复释放同一句柄/指针，以及批量申请第 k 个失败时前 k-1 个回收。
3. **打桩配方**：长稳/耗尽/异常路径泄漏标为 `needs_runtime_verification` 时，给出 `kind="leak_counter_probe"` 计数观测或 `fault_return` 桩点。

## 轨道四：踩内存与跨线程/跨进程悬空访问（`memory_corruption`）

以 `track_topology.memory_anchors`（重点看 `cross_fn_dangling_candidate`、`unnullified_member_after_release`、`post_release_use_in_fn`、`shared_mem_or_slot_op`、`raw_memory_op`）、`shared_access_matrix` 和 `risk_blueprint.memory_hazard_zones` 为锚点：
1. **跨线程 / 跨进程访问已释放或重分配资源（头号主靶）**：
   - **销毁与注销时序倒置**：对象 `free`/`kfree`/归还对象池之前，是否**先同步摘除链表/哈希表节点、停掉定时器、注销异步回调/epoll/IPC 订阅并等待在途执行流排空（drain/join/cancel_sync）**？若先释放后注销，或异步取消未阻塞排空在途回调，必在堆/池重分配后踩坏新对象内存。
   - **跨锁 / 跨休眠残留裸指针**：线程 A 持锁查表取出裸指针 `p = tbl[idx]` 后解锁继续读写 `p->field`，期间线程 B 销毁 `tbl[idx]` 并释放/复用该内存块，导致线程 A 异构写踩内存。
   - **跨进程共享内存 / 对象池槽位（Slot）复用无世代号保护**：`shm`/`mmap`/IPC 环缓冲/全局对象池槽位释放重分配后，另一进程或工作线程持旧句柄/旧槽位下标/旧偏移写入时，是否校验锁与世代号（`gen`/`epoch`/`magic`）？
   - **引用计数归零窗口悬空写**：非原子递减引用计数，或在调用 `put`/`unref`（此时对端线程可能已将计数减至 0 并释放内存）之后仍读写对象字段。
2. **空间越界写与非法指针踩内存（次级副靶）**：
   - `memcpy`/`memmove`/`memset`/`strcpy`/`snprintf` 长度算术错误（有符号负数转 `size_t`、无符号减法下溢回绕、`sizeof(指针)` 误作缓冲区大小、未扣除包头长度）、数组/RingBuffer 下标越界写、未初始化野指针解引用。
3. **打桩配方**：必须在 `interleaving_or_path` 写出释放/复用流与访问流的精确行号交错或越界算式；需运行时拉大窗口或毒化抓现场时标 `needs_runtime_verification`，给出 `kind="memory_poison_or_bound_probe"` 或 `"race_window_delay"` 桩点（如释放后填充毒化字节 + 延迟窗口 + ASAN/PageGuard 观测）。
