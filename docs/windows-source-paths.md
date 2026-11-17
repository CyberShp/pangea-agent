# Windows 源码快照长路径修复

触发场景：原仓库文件可读取，但新 Run 的 `inputs/.source-staging/<repo_id>/` 前缀使复制目标超过传统 MAX_PATH，Python 3.12 `shutil.copy2` 的 CopyFile2 路径报 WinError 3。正式快照和单文件结构索引也可能超过该限制。

## 实现

- `fs_paths.long_path` 只在 Windows 文件系统调用处转换为扩展长度绝对路径，支持本地盘符和 UNC；已有扩展前缀不重复添加，POSIX 保持原行为。
- `resolve_path` 执行真实路径解析后返回普通路径；目录遍历返回普通路径。清单、任务绑定、引用、路径归属校验和界面继续使用普通路径。
- 覆盖快照复制、暂存清理与替换、预览/目录验证、多语言扫描及历史 C/C++ 扩展、worker 读取、结构索引 JSON 原子写入、补充源码物化与继承、引文校验、增量及报告源码指纹。
- 复制失败保留异常链，同时给出源/目标路径、长度、errno 和 winerror。
- 插件的证据预览和补充源码校验在 Node I/O 边界使用 `path.toNamespacedPath`，保留真实路径越界检查及普通展示路径。
- 不修改注册表，不改变既有 Run 的合同，不下载 LFS，不改变分析范围和 Agent 语义规则。

## 验证

`python verification/windows_long_paths.py` 使用生产准备与读取入口，覆盖：

1. 盘符/UNC/已有前缀、相对路径及 POSIX 路径。
2. 原路径不足 260、快照目标超过 260 的复制与报告指纹。
3. 长路径残留 staging 清理、重复冻结、四语言 inventory 和单文件结构索引。
4. 真实新 Run 准备、Planning 提交、绑定 analysis worker 读取、冻结内容不受现场变动影响、引文校验。
5. 补充源码原子写入与子 Run 继承。
6. 越界拒绝和复制异常诊断。

Desktop 构建用内嵌 Python 执行该脚本；插件 `source-preview.test.mjs` 覆盖长路径预览、补充源码完整性与 junction 越界。Linux 上通过不替代 Windows 原生验收。

本次开发环境全量 verification 发现一个基线已有错误：`analysis_objective_contract.ObjectiveContracts.test_report_counts_same_declared_ready_fraction_for_all_objectives` 的 SimpleNamespace 夹具缺少 `binding`。已在未修改的 `96f5721` 基线上复现相同异常，本修复未改该夹具或相关业务逻辑。
