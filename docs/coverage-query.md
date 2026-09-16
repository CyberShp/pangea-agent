# langgraph 内网覆盖率查询

在 Desktop「新建分析 → 分析资料 → 从内网查询覆盖率」填写产品、C 版本、模块和可选 B 版本，点击「查询并加入本次分析」。成功或部分成功的查询会生成 Coverage 资产并自动选中，无需上传文件。

查询使用安装目录已有的私有 Skill，不把内网脚本、地址或凭据放进源码仓库：

```text
<PANGEA 解压目录>/local-skills/coverage-query/SKILL.md
<PANGEA 解压目录>/local-skills/coverage-query/scripts/coverage_query.py
```

Desktop 通过 `PANGEA_LOCAL_SKILLS_ROOT` 传入 `local-skills` 路径。`system capabilities` 返回 `coverage_query_skill.available`；安装 Skill 后刷新工作台即可重新检测。

## 查询约定

`assets query-coverage` 使用当前 Python 执行一次 `coverage_query.py combined --product <产品> --version <C版本> --module <模块> [--b-version <B版本>]`。与 codetalks-skill 一样，参数按原文分开传入，由私有 Skill 负责平台名称匹配，不自行拼接产品前缀。

- 接受 UTF-8 / UTF-8 BOM 的 combined JSON，保留原始 stdout、stderr 和查询参数。
- 保留 auto/summary 来源、函数/行/分支明细、missing、warnings 与 query_resolution。
- `partial` 保留可用记录并展示缺失来源；`no_data` / `error` 不创建可选资产。平台对象 `not_found` / `ambiguous` 显示查询失败。
- 查询超时、非 JSON 或非零退出码与 success 冲突时显示错误，不解释为没有缺口。默认超时 300 秒。
- 原始查询记录保存在数据目录 `coverage/queries/<查询编号>/`；资产保存 combined JSON，重新解析资产不会重复请求平台。

## 进入分析

查询结果通过现有 `asset_ids` 进入模块分析，不增加 Graph 阶段或修改 Run 契约。函数按文件和函数名匹配；行/分支按文件和行号匹配，分支 block/branch 原文保留，不能冒充源码解析器的分支编号。

只有唯一匹配到本次 `source_scope` 的零覆盖记录进入 `coverage-gaps.json`；参考源码、其他模块、同名歧义或未知计数不作为已定位零覆盖提示。业务可达性和是否需要补测仍由 Agent 判断。

查询状态与缺失来源冻结到 `coverage-diagnostics.json`，供规划、分析和复核读取；已有 Run 不会因新查询或重新解析资产而被修改。UI 重新查询前移除上一次自动选中的查询资产，避免失败后沿用旧数据。

## 验证

使用合成数据完成本地验证：查询 CLI 的真实子进程与字面参数、BOM、success/partial/no_data/error、对象匹配失败、超时、旧 XLSX、同名路径歧义，以及实际 LangGraph 新 Run 的范围过滤和诊断冻结。内网服务与 Windows 成品需在对应环境验收。
