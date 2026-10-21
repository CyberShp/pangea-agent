# 显式源码范围预览

Desktop 新建分析先明确选择目录、文件或整个仓库，再调用只读入口：

```powershell
python -m pangea_agent.cli.main source-scope-preview --data-root 'C:\Pangea\data' --repository 'storage' --source-scope '["tls"]'
```

返回标准 CLI envelope。`result` 包含 `preview_state=complete`、`normalized_scope`、全量去重 `matched_file_count`、`files[{relative_path,size_bytes}]`、`rules_revision`、`scope_revision`。此命令不创建 Run，不派发 Agent，不读取凭据。

预览复用 `inventory.on_demand.requested_files`、语言检测和忽略规则；不扩展父目录、不根据目标名称推测路径。空选择、目录消失、路径或软链越界、混合语言和读取故障显式失败；合法范围没有支持的源码时返回完整的 0 命中结果。客户端不能以 0 命中或预览失败启动新建分析。

新合同可带 `expected_scope_revision`。该版本绑定仓库、规范范围、枚举规则和文件路径集合，不表示源码内容版本；实际内容仍由原冻结流程复制保存。source-first 在输入冻结前、后核对当前文件集合；变化时返回 `SOURCE_SCOPE_CHANGED`，要求重新预览。旧合同不带该字段时行为保持兼容。没有新增源码质量判断、语义门禁或分析步骤。

跨仓库集成回归在 dsh-pangea 的 `tests/source-scope-engine.test.mjs`，由 `PANGEA_TEST_PYTHON` 和 `PANGEA_AGENT_ROOT` 指向此版本运行时；验证中文与逗号路径、整仓、重叠选择、忽略目录、0 命中、越界、读取故障、确认后变化，以及预览与真实 Run 冻结的完整文件集合一致。
