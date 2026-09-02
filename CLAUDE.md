# PANGEA Codetalks Skill 直启协议

本仓库以冻结的 `codetalks-skill 1.2.0` Run 作为唯一分析流程。主会话只创建 Run、冻结输入并读取真实状态；不自行分析源码、不编排旧 action、不填写语义结果。

## Run 生命周期

- 新模块分析先在 `pangea-data/repositories/` 自动搜索目标模块并确定最小 `source_scope`，再通过当前客户端的稳定入口启动；`.c` / `.h` / `.cc` / `.cpp` / `.cxx` / `.hpp` / `.hh` 识别为 `c_cpp`，`.lua` 识别为 `lua`，用户不需要另填语言。没有当前会话明确 `run_id` 时不扫描历史 Run。
- Desktop/DSH 只启动一个 Skill Run；Skill 自己按 Step 01–09 管理 Producer、独立 Judge、正式交付和门禁。
- 语言与资产输入在 Run 创建时冻结；运行中只读取 `request.md`、冻结的 `inputs/assets/`、冻结的方法论和 Skill 包。
- 任何门禁或契约失败都保留原始错误和当前步骤；不得跳步、自动修复、默认填充或静默降级。
- 只按 `内部索引/运行状态.json` 和 `run_guard.py` 的真实状态继续，不根据 Agent 回复文字推断阶段。

旧 Run 只读兼容：可以读取其自身冻结 manifest 和状态，但新建 Run 不得发送旧字段或重新启用旧 action 生命周期。

命令按 Windows PowerShell 可执行方式组织，一次执行一个命令。不得修改 `pangea-data/repositories/` 下用户源码的 Git 状态。
