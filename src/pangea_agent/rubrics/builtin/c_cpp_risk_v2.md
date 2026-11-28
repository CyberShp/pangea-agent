# C/C++ 四轨深潜检查规程（risk-method-v2）

配合 `scene_risk_v2.md` 使用，严格遵守 C/C++ 真实求值语义（短路求值、整数提升/无符号回绕、宏展开副作用、Release 下 `assert` 消失）。四条轨道职责严格正交，互不重报。

## 轨道一：异常分支与状态回滚（`error_rollback`）

以 `track_topology.error_slices` 和 `risk_blueprint.transaction_chains` 为锚点（**聚焦业务状态、锁/标志回滚与错误码传播，以及 Stage 1 点名兜底的跨轨代码缺陷；异常早退导致的内存/句柄泄漏统一归轨道三主责上报，避免两轨互踢**）：
1. **状态与注册副作用回滚账本**：对每条在产生状态/注册副作用（回调/定时器注册、加锁、全局/结构体状态字段写、状态机切态）之后的 `return`、`goto`、循环 `break`，逐项核对前置副作用是否按逆序完整撤销。
2. **错误码吞没与覆盖**：检查清理阶段的 `close()`/日志/回滚函数返回值是否覆盖了原始失败码 `rc`，或将失败静默映射成“空结果/成功”。
3. **跨调用脏态继承**：若本次失败修改了 `static` 标志、缓存、状态机字段或半填充输出缓冲区，追踪下一次正常调用或重试是否会读到脏态导致级联失效。
4. **打桩配方**：无法靠正常入参触发的内部失败（如底层 I/O 错误、内部调用失败），标为 `needs_runtime_verification` 并给出 `kind="fault_return"` 桩点。

## 轨道二：锁纪律与存活态并发竞态（`concurrency_timing`）

以 `track_topology.concurrency_anchors`（含 `global_lazy_init_or_alloc`）、`shared_access_matrix` 和 `risk_blueprint.concurrency_contexts` 为锚点（聚焦**存活对象与共享全局态**竞态；销毁/释放后跨流踩内存归轨道四）：
1. **存活态无锁/半锁共享读写与单函数并发重入**：对照 `shared_access_matrix`（含 `scope="global_or_static"` 的单函数写者）与 `global_lazy_init_or_alloc`，严禁把并发竞态窄化为“两个不同函数读写”——重点核查**同一函数被多线程并发重入**（如首次并发建连、收包、定时器、事件回调同时进入同一注册/处理函数）时的无锁“读-改-写”与“检查-申请/初始化-赋值（TOCTOU）”。若无锁惰性初始化导致读端看到半初始化缓冲或状态错乱，直接在本轨上报；若后果纯属覆写全局指针产生孤儿块泄漏，由轨道三（L7）主责建账，本轨在 `check` 核实并发可达性与缺锁事实。
2. **锁纪律、死锁与持锁阻塞**：核对 `lock_held_calls`（持锁期间调用阻塞 I/O、用户回调或申请第二把锁），追踪 AB-BA 锁序反转与异常退出漏解锁。
3. **打桩配方**：依赖窄时间窗的存活态竞态标为 `needs_runtime_verification`，给出 `kind="race_window_delay"` 延迟拉窗桩点及 TSAN/状态不变量观测判据。

## 轨道三：资源配平与内存/句柄泄漏、重复释放（`resource_lifecycle`）

以 `track_topology.alloc_flow_slices`、`track_topology.resource_anchors`（重点看 `global_alloc_assign`）、`track_topology.error_slices` 和 `risk_blueprint.resource_entities` 为锚点（**全权主责一切正常路径、异常早退路径及并发惰性初始化/重入路径的内存/句柄泄漏与重复释放**；释放后悬空访问踩内存归轨道四）。严禁只查底图宏观实体或轻信索引摘要就交差，必须按实际分配点逐函数通读源码核对 **LSan 七大高频泄漏靶标**：
1. **L1 异常早退、校验后置与 `goto` 阶梯错位漏释放**：`alloc_flow_slices[*].post_alloc_exits` 仅作起点导航，**必须 `source-read` 通读分配点至函数末尾 `}` 的完整函数体**，逐条核对每个 `return`、`goto`、循环 `break`/`continue` 及隐藏早退宏（`macro_exit_candidate`）。重点盯防**“函数前部先申请工作缓冲，随后执行一长串合法性/特定配置兼容性校验并在中途某分支直接 `return` 失败”**的申请与校验次序倒置；同时防范**堆栈陷阱**（申请点可能在本函数前部、封装 helper 或调用链上游尚未移交所有权的缓冲，排查基本单位是“申请点与全部退出路径的配对关系”，而非单一栈顶帧）。
2. **L2 Callee 内部分配传出（返回值 / `&out` 二级指针）调用方漏释放**：关注 `ownership_out_candidate=true`、返回指针或通过 `&buf` 出参赋值的封装分配/构造函数，穿透读调用方源码核查成功与失败分支是否接管并释放；同时核查 helper 内部半初始化报错时是否自行清理已分配子块。
3. **L3 销毁/重置函数浅释放（Indirect Leak）**：核查 `destroy`/`free`/`reset`/`clear` 函数在释放外层结构体 `free(obj)` 或 `memset(obj, 0, ...)` 之前，是否已递归释放 `obj->sub_buf`、`obj->name`、嵌套链表/哈希节点等全部动态子成员。
4. **L4 循环/重试/重载覆盖指针未先释放旧块**：对照 `alloc_flow_slices[*].overwrite_lines`，核查指针变量或结构体成员在循环体、重配、重试路径中被二次赋新堆地址前是否先释放旧内存；核查 `realloc` 失败直接覆盖原指针 `p = realloc(p, n)` 导致的原块丢失。
5. **L5 容器挂接/异步投递失败孤儿泄漏**：对象分配并初始化后调用 `list_add`/`hash_insert`/`enqueue`/`send`/异步提交返回失败（或重复键被拒）时，是否兜底释放该节点及其子成员。
6. **L6 标准库/三方库隐式堆分配与引用计数泄漏/Double Free**：核查 `strdup`/`strndup`/`asprintf`/`realpath`/`getline`/`cJSON_Parse`/`cJSON_Print`/`cJSON_Create*`（未挂入父 JSON 树）及 C++ `new`/`new[]` 的释放配平；核对引用计数失败分支漏 `put`/`unref` 及异常清理与正常清理重复释放同一指针。
7. **L7 全局/静态资源的并发惰性初始化竞态与重入覆盖泄漏（跨轨必查，严禁按单线程放行）**：对照 `resource_anchors` 中 `kind="global_alloc_assign"`、`alloc_flow_slices` 中 `global_or_static_target` 非空项及源码中一切向全局指针、函数内 `static` 指针或全局结构体成员赋新申请地址（含 `P = alloc(...)`、先申请给局部 `tmp` 再 `P = tmp`、或 `&P` 出参申请）的位置，**严禁因外层有 `if (P == NULL)` 或“进程常驻缓冲退出不回收”就直接排除**，必须逐处核查：
   - **无锁“先判空、再申请、后赋值”三步组合（Check-Alloc-Assign TOCTOU）**：若惰性初始化写在并发可达路径上（首次建连/连接注册绑定、收包、定时器回调、事件处理、故障恢复等），且判空与写回全局指针 `P` 之间无互斥锁、无原子 CAS、无 `pthread_once`/单线程启动保证——多个并发线程同时看到 `P` 为空将各自申请内存并依次覆写 `P`，除最后一次写入外的缓冲全部失联泄漏（有界但进程不重启不回收，多连接并发建连等压力场景天然可达）。
   - **连接建立与模块开工链全局分配核查**：凡连接注册、通道绑定相关函数内出现“全局变量 ← 资源申请”赋值，无论是否带判空，均须沿调用链核查并发线程上限与持锁窗口大小。
   - **镜像反向模式**：模块开工、断连恢复、重试或重载流程对全局资源重复申请前未判断是否已申请且未先释放旧块，直接覆写导致泄漏。
   - **排除项**：进程单线程启动期串行执行一次且后续只读的全局初始化，以及诊断/故障注入命令（`diagnose`/`pgd` 等带重入保护、由管理命令显式触发）中故意的测试泄漏申请，记为 `excluded` 不误报。
8. **打桩配方**：正常输入或多连接并发首次建连即可触发的泄漏标 `static_proven`（并发窄窗口难以必现时可标 `needs_runtime_verification` 并配 `kind="race_window_delay"` 在判空后、赋值前拉宽窗口）；需内部失败触发的配 `kind="fault_return"` 或 `kind="leak_counter_probe"`，观测判据注明 LSan 或内存管理器计数增量。

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
