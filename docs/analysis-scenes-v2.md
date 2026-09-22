# 四场景实施说明

新建任务通过 `behavior-test-v2` 选择 module-analysis、risk-analysis、branch-analysis 或 coverage-analysis，
使用同一 source-first-v1 Graph 和 depth/speed 调度。旧 profile 和旧合同保持原解释。

## 冻结与输入

`inputs/analysis-scene.json` 冻结场景职责、方法论集合和展示设置。任务只携带引用；规则、资料、
源码和 Coverage 匹配表使用现有分页读取工具。Planning 通过既有 purpose 指定文字主干 owner，
选中的用户方法论进入 Analysis、Review 和原 worker 的 Closure，不另建流程 Agent。

覆盖数据沿用查询、文件解析、既有资产三入口，最终使用 asset_ids。可选 asset_revisions
映射的值是字符串内容指纹：SHA-256(排序后的 AssetRecord JSON + NUL + 解析结果字节)。
现有资产没有整数 revision，不能用虚构的修订号锁定输入。选择后变化会要求重新选择；
未提供 revision 的调用者在创建时冻结当时内容。旧合同不补填该字段。

asset-snapshots.json 保存选定的元数据、解析正文和指纹；coverage-match-summary.json 保存
按来源区分的匹配、未匹配与歧义。有效空结果允许创建；无可用覆盖资产时专项在创建阶段报错，
留存 preparation-error.json，尚未创建执行会话。全部不匹配的输入保留诊断交 Agent 解释，
不能自动判定没有测试价值或将其当成 100% 覆盖。恢复不重新查询实时资产。

## 交付与展示

四场景均先保存 kind=flow，正文使用 module-flow-text-v1 的 flow_id/title/text/source_evidence。
nodes、edges、paths 可选；Python 不以这些字段缺失阻断结果。Agent 与 Reviewer 决定流程
是否准确完整、覆盖数据是否适用、用例是否可实施。文字流程不是 Archify 绘图输入 schema。

模块保留综合业务、轻量风险及可选补测；风险专项保留现有风险板块；分支和覆盖率不要求
正式风险账本。页面、讨论、CSV/XLSX 和报告按冻结场景展示；异常出现的风险原文保留供核对。
报告不自动生成流程 SVG。用户单独创建 Archify 或函数变量图，保存 source_records 精确版本；
逻辑 flow_id 加 unit_id 用于查找同一流程的历史图，旧快照不会被新结论覆盖。

## 验证入口与边界

- Desktop `scripts/verify-analysis-scenes.py`：真实 Graph 的四场景 × 两种模式，用户方法论读取、
  原 worker 定向修正、有效空 Coverage、输入变更、冻结恢复和 16/2000 文件紧凑视图。
- Companion `tests/analysis-scenes.test.mjs` 与 `tests/client.test.mjs`：能力协商、精确资产输入、
  文字流程、来源诊断、风险展示、导出和图表版本。
- Desktop 构建脚本已执行该验证；staging 校验要求完整 scene_* rubric 与场景实现文件。

这些测试使用合成源码和工具提交的合成 Agent 记录，验证协议和生命周期，不冒充真实模型的
源码理解质量验收。Windows 成品安装升级、真实 NGA/ACP 语义表现仍须在发布验收中验证。
