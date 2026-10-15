# 分支业务覆盖与流程稳定性升级说明

本次调整面向分支测试设计和业务流程展示。分支分析按业务条件、配置、状态、操作顺序、结果与恢复发现测试差异，不承担产品风险判断；正常拒绝和预期错误处理仍是正常负向行为。

## 新任务行为与兼容性

- 新建分支任务冻结 `method_version=behavior-branch-v1`，使用源码驱动的行为发现、适用测试方法和双向覆盖核对。专属 `scene_branch_review` 是必读规则，不依赖 Planning 选择可选方法论。
- 新建四场景任务冻结 `flow_layout_version=stable-v1`。旧 Run 继续读取原场景与方法论快照，恢复任务不会追加新规则；旧流程和旧 note 仍可读取。
- 不新增 Agent、全局分析阶段或完整复核轮次。普通业务覆盖 note 不增加 Python 语义门禁；宿主不代写预期、不判定业务充分性。

## 业务覆盖与用例

Analysis 在已有 `kind=note` 中保存 `format_version=behavior-coverage-v1`：包括范围、组合策略、依赖缺口，以及业务操作、条件、约束、预期、源码依据和实际用例/变体引用。报告与工作台保留原记录，并提供覆盖项展示及参数变体的输入/预期。

| 覆盖项状态 | 含义 |
| --- | --- |
| `designed` | 已有对应测试设计；不表示已执行或通过 |
| `missing` | 已确认行为与预期，尚缺对应设计 |
| `unresolved` | 行为、约束、预期或依赖仍待核实 |
| `not_applicable` | 有明确范围或约束依据证明不适用 |

用例的 `execution_readiness` 继续单独说明是否具备执行入口与设施。`designed`、用例数量和变体数量都不是实测代码覆盖率。异常或旧格式 note 保留原文；缺项由原 Worker/Reviewer 处理，不由宿主猜造内容或自动判 Run 失败。

## 流程稳定性分两层

**展示层**：新流程用 `semantic_key`、`edge_id`、`paths[].edge_ids` 和显式 `main_path_ids` 表达身份与路径，宿主进行确定性排序和布局。节点、边、路径数组重排不应改变同一结构的展示。允许多条主干或没有适用主干，不把第一条路径当作默认业务主干。

**分析层**：冻结源码、范围、资料、方法论、目标及已声明模型设置的指纹，按显式身份比较流程变化。输入或模型元数据不完整时如实标记未知；`model_settings` 用于记录，不会自动设置模型采样参数。此机制不保证 LLM 每次得出相同结论，也不证明不存在未发现的业务遗漏。源码未变时仍允许有证据地修正旧图。

工作台点击节点时，唯一声明路径可直接高亮；存在多条路径时保留适用的当前选择或由用户选择。没有路径、平行边身份不明确时提示缺口，不推导最短路或把多条路径合成一条。

**单独导出的 SVG 是静态图。** 节点点击、路径选择等交互依赖工作台查看器运行时；下载 SVG 不会附带这些事件处理逻辑。

## 显式流程基线

基线由任务合同显式指定，不自动选择“最新 Run”：

```json
{
  "flow_baseline": {
    "run_id": "已审核通过的实际 Run ID",
    "version_id": "该 Run 输出的实际流程版本 ID"
  }
}
```

源 Run 必须完整结束、质量状态为 `PASS`、不是部分交付，并存在匹配身份与内容指纹的 `outputs/flow-baseline.json`。没有合格基线时可以不指定；不能用当前任务自身、未审候选或不完整结果替代。

`flow_baseline` 和 `flow_comparison` 仅提供给 `comparison_review`，普通新建 Analysis 不接收这两个输入。显式增量 Analysis 仍可按冻结合同读取 `incremental_parent_records` 等父记录，包括旧流程，并重新核验；这与流程稳定性基线是两条不同的输入通道。采用本版稳定规则的新 Run，其独立盲审不接收首轮结果、流程基线或增量父记录；旧 Run 保留原冻结输入合同。Reviewer 在对照阶段判断差异来自输入变化、旧结论修正、展示调整还是未确认因素；宿主不做标题模糊匹配、不自动改图或更换用户选定的基线。

相关文件：当前输入指纹 `inputs/flow-stability.json`；显式选定基线快照 `inputs/flow-baseline.json`；首轮对照差异 `inputs/flow-comparison.json`；最终交付差异 `outputs/flow-delivery-comparison.json`。定向修正后的最终差异单独保存，不覆盖 Reviewer 当时读取的版本；`reviewed: false` 表示该机械差异不构成额外语义审核。工作台和报告优先展示最终交付差异，结构未变但文字或源码依据变化时仍提示核实并保留前后值。旧 Run 缺少这些文件是兼容情形，不代表其原任务损坏。

## 验证与验收边界

以下命令已在开发环境执行通过（仓库根目录）：

```text
python verification/branch_behavior_contract.py
python verification/test_case_contract.py
python verification/flow_stability.py
python verification/incremental_smoke.py
python verification/observability_recovery.py
python verification/report_delivery_smoke.py
```

上述验证涵盖新旧规则冻结、普通 note 非阻断、用例契约、真实 Graph 基线绑定和盲审隔离、增量恢复、最终交付差异、静态 SVG 与报告链接。另已通过真实 Node 插件 → Python CLI → 冻结基线 → 工作台元数据的跨组件检查；该检查使用合成的已审核父记录，未调用模型。

插件在 `plugins/dsh-pangea-companion` 中执行 `npm test`，同时生成 `lib/client.js` 并检查前端与宿主回归；新增布局测试验证输入数组重排、平行条件、循环及多主路径。桌面构建脚本已接入新增 Python 验证。它们验证宿主行为，不代替真实模型的业务理解验收。跨配置、边界、三因素决策、状态/顺序及跨单元关系的语义验收样例见 [branch-behavior-acceptance.md](branch-behavior-acceptance.md)。
