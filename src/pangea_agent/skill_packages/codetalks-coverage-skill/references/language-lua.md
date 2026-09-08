# Lua 语言 Profile

本 Profile 只补充 Lua 的语义边界，仍然遵守 Codetalks Skill 的九步流程、证据台账、Markdown 活文档和黑盒测试交付要求。

## 语义基线

- 只有 `false` 和 `nil` 为假；`0` 与空字符串都为真。
- `and`、`or` 会短路并返回操作数本身，不保证结果是布尔值。
- 缺失字段读取为 `nil`；调用 `nil`、对 `nil` 做不支持的运算或使用非法 table key 会抛错。
- `:` 调用会隐式传入 `self`，`.` 调用不会；定义和调用形式不一致时必须沿参数绑定确认实际结果。
- table、function 和 userdata 按引用共享；修改嵌套 table 时追踪所有可见持有者和别名。
- `pairs` 遍历顺序没有保证；带空洞的序列不能用长度运算符推断稳定边界。
- metatable 的 `__index`、`__newindex`、算术/比较/调用元方法可以改变普通字段访问与运算；`rawget`、`rawset` 会绕过对应元方法。

不得把 C/C++ 的真假值、空指针、整数边界、栈对象生命周期或线程模型直接套到 Lua。

## Module 与依赖

从每个入口确认 module 返回值、顶层副作用、公开函数、字面量和动态 `require`、`package.path`/`package.cpath`、宿主注入路径、预加载 module、生成代码、循环依赖和加载失败后的缓存状态。成功 `require` 会缓存 module；共享 table、加载时副作用和缓存隔离边界只能依据实际 Lua VM/宿主契约判断。

## 错误、状态与资源

追踪 module table、对象字段、非局部变量、闭包 upvalue、缓存、注册表、提前 return、多返回值截断/补 `nil`、可变参数，以及 `error`、`assert`、`pcall`、`xpcall` 的返回形状和错误传播。protected call 只隔离 Lua 错误，不会自动回滚之前的状态或资源副作用。

对 `coroutine.create`、`wrap`、`resume`、`yield`、`status` 形成创建、启动、挂起、恢复参数、多返回值、错误、终止和再次恢复的状态链。文件、socket、timer、锁、订阅、RPC 句柄和 userdata 必须追踪创建、发布、使用、关闭、取消、超时、重试和异常退出；GC 可回收不等于外部资源已经释放。

## 测试转换

每个风险写清测试人员可制造的输入/状态/时序/依赖故障、真实控制流、系统结果、外部观测、恢复标准，以及能够排除风险的保护分支或宿主契约。优先使用公开入口、消息、RPC、配置、文件和设备状态表达，函数名和行号只作为源码证据。Coverage 只作为补测提示，不把命中率当作语义证明。
