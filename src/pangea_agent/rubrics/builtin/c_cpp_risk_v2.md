# C/C++ 四轨深潜检查规程（risk-method-v2）

配合 `scene_risk_v2.md` 使用，严格遵守 C/C++ 真实求值语义（短路求值、整数提升/无符号回绕、宏展开副作用、Release 下 `assert` 消失）。四条轨道职责严格正交，互不重报。

## 轨道一：异常分支与状态回滚（`error_rollback`）

以 `track_topology.error_slices` 和 `risk_blueprint.transaction_chains` 为锚点（**聚焦业务状态、锁/标志回滚与错误码传播，以及 Stage 1 点名兜底的跨轨代码缺陷；异常早退导致的内存/句柄泄漏统一归轨道三主责上报，避免两轨互踢**）：
1. **状态与注册副作用回滚账本**：对每条在产生状态/注册副作用（回调/定时器注册、加锁、全局/结构体状态字段写、状态机切态）之后的 `return`、`goto`、循环 `break`，逐项核对前置副作用是否按逆序完整撤销。
2. **错误码吞没与覆盖**：检查清理阶段的 `close()`/日志/回滚函数返回值是否覆盖了原始失败码 `rc`，或将失败静默映射成“空结果/成功”。
3. **跨调用脏态继承**：若本次失败修改了 `static` 标志、缓存、状态机字段或半填充输出缓冲区，追踪下一次正常调用或重试是否会读到脏态导致级联失效。
4. **打桩配方**：无法靠正常入参触发的内部失败（如底层 I/O 错误、内部调用失败），标为 `needs_runtime_verification` 并给出 `kind="fault_return"` 桩点。

## 轨道二：锁纪律与存活态并发竞态（`concurrency_timing`）

以 `track_topology.concurrency_anchors`、`shared_access_matrix` 和 `risk_blueprint.concurrency_contexts` 为锚点（聚焦**存活对象**竞态；销毁/释放后跨流踩内存归轨道四）：
1. **存活态无锁/半锁共享读写**：对照 `shared_access_matrix` 核查跨线程/回调/中断读写的同一存活结构体字段或全局变量；检查“读-改-写”或“检查-使用（TOCTOU）”是否跨越锁释放点。
2. **锁纪律、死锁与持锁阻塞**：核对 `lock_held_calls`（持锁期间调用阻塞 I/O、用户回调或申请第二把锁），追踪 AB-BA 锁序反转与异常退出漏解锁。
3. **打桩配方**：依赖窄时间窗的存活态竞态标为 `needs_runtime_verification`，给出 `kind="race_window_delay"` 延迟拉窗桩点及 TSAN/状态不变量观测判据。

## 轨道三：资源配平与内存/句柄泄漏、重复释放（`resource_lifecycle`）

以 `track_topology.alloc_flow_slices`、`track_topology.resource_anchors`、`track_topology.error_slices` 和 `risk_blueprint.resource_entities` 为锚点（**全权主责一切正常路径与异常早退路径的内存/句柄泄漏及重复释放**；释放后悬空访问踩内存归轨道四）。严禁只查底图宏观实体就交差，必须按实际分配点逐函数核对 **LSan 六大高频泄漏靶标**：
1. **L1 异常早退与 `goto` 阶梯错位漏释放**：对照 `alloc_flow_slices[*].post_alloc_exits` 与 `error_slices`，逐条核对分配点之后的每个 `return`、`goto`、循环 `break`/`continue` 以及隐藏早退的判错宏（`macro_exit_candidate`，须穿透看宏定义）；确认多步申请第 k 步失败时前 k-1 步资源已全部逆序释放、`goto` 目标标签未跳过刚分配的指针。
2. **L2 Callee 内部分配传出（返回值 / `**out`）调用方漏释放**：关注 `ownership_out_candidate=true` 及返回指针/通过出参赋值的 helper 函数，穿透读调用方源码核查成功与失败分支是否接管并释放；同时核查 helper 内部半初始化报错时是否自行清理已分配子块。
3. **L3 销毁/重置函数浅释放（Indirect Leak）**：核查 `destroy`/`free`/`reset`/`clear` 函数在释放外层结构体 `free(obj)` 或 `memset(obj, 0, ...)` 之前，是否已递归释放 `obj->sub_buf`、`obj->name`、嵌套链表/哈希节点等全部动态子成员。
4. **L4 循环/重试/重载覆盖指针未先释放旧块**：对照 `alloc_flow_slices[*].overwrite_lines`，核查指针变量或结构体成员在循环体、重配、重试路径中被二次赋新堆地址前是否先释放旧内存；核查 `realloc` 失败直接覆盖原指针 `p = realloc(p, n)` 导致的原块丢失。
5. **L5 容器挂接/异步投递失败孤儿泄漏**：对象分配并初始化后调用 `list_add`/`hash_insert`/`enqueue`/`send`/异步提交返回失败（或重复键被拒）时，是否兜底释放该节点及其子成员。
6. **L6 标准库/三方库隐式堆分配与引用计数泄漏/Double Free**：核查 `strdup`/`strndup`/`asprintf`/`realpath`/`getline`/`cJSON_Parse`/`cJSON_Print`/`cJSON_Create*`（未挂入父 JSON 树）及 C++ `new`/`new[]` 的释放配平；核对引用计数失败分支漏 `put`/`unref` 及异常清理与正常清理重复释放同一指针。
7. **打桩配方**：正常输入即可稳定触发的泄漏标 `static_proven`；需内部失败或长稳耗尽触发的标 `needs_runtime_verification`，给出 `kind="fault_return"` 或 `kind="leak_counter_probe"` 桩点，观测判据注明 `ASAN_OPTIONS=detect_leaks=1`（LSan）或堆计数增量。

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
