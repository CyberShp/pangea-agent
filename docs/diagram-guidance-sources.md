# 流程图与时序图：方法来源和产品适配

本说明记录 2026-10-20 对 GitHub 绘图 Agent skills 的调研及采纳范围。实际执行规则由当前 Run 冻结的 rubric 和图任务合同决定；本文件是来源与适配说明，不是新的执行入口，不修改已有 Run 的规则。

## 固定来源

下表的 stars 来自查询当日 GitHub 仓库 API，是**整个仓库**的关注数，不是单个 skill 的安装量、质量分数或稳定性保证。最近推送时间与技能目录修改时间分别列出，不能把其他文件更新当作技能维护。

| 来源 | 固定提交 | 查询时仓库 stars | 维护记录 | 许可与用途 |
| --- | --- | ---: | --- | --- |
| [softaworks/agent-toolkit：mermaid-diagrams](https://github.com/softaworks/agent-toolkit/tree/3027f20f3181758385a1bb8c022d4041dfb4de84/skills/mermaid-diagrams) | `3027f20f3181758385a1bb8c022d4041dfb4de84` | 2,571 | 仓库最近推送 2026-03-05；技能目录最近修改 2026-02-01 | MIT；参考业务判断、错误处理、分层视图、时序条件/并发表达 |
| [WH-2099/mermaid-skill](https://github.com/WH-2099/mermaid-skill/tree/982d9035231312c527197501a882cf6c8f4394cc) | `982d9035231312c527197501a882cf6c8f4394cc` | 293 | 最近推送 2026-08-11；仓库配置每周同步 Mermaid 官方文档的 workflow，不能据此保证每周都成功执行 | MIT；参考流程与时序语法概念及按图类型读取指南的方法 |

本次阅读的具体文件：

- softaworks：[SKILL.md](https://github.com/softaworks/agent-toolkit/blob/3027f20f3181758385a1bb8c022d4041dfb4de84/skills/mermaid-diagrams/SKILL.md)、[flowcharts.md](https://github.com/softaworks/agent-toolkit/blob/3027f20f3181758385a1bb8c022d4041dfb4de84/skills/mermaid-diagrams/references/flowcharts.md)、[sequence-diagrams.md](https://github.com/softaworks/agent-toolkit/blob/3027f20f3181758385a1bb8c022d4041dfb4de84/skills/mermaid-diagrams/references/sequence-diagrams.md)、[LICENSE](https://github.com/softaworks/agent-toolkit/blob/3027f20f3181758385a1bb8c022d4041dfb4de84/LICENSE)。
- WH-2099：[SKILL.md](https://github.com/WH-2099/mermaid-skill/blob/982d9035231312c527197501a882cf6c8f4394cc/.claude/skills/mermaid/SKILL.md)、[文档同步配置](https://github.com/WH-2099/mermaid-skill/blob/982d9035231312c527197501a882cf6c8f4394cc/.github/workflows/sync-mermaid-docs.yml)、[LICENSE](https://github.com/WH-2099/mermaid-skill/blob/982d9035231312c527197501a882cf6c8f4394cc/LICENSE)。其 references 来自 Mermaid 官方文档；产品没有整包复制这些上游参考文件。
- 语法含义交叉核对：[Mermaid 流程图文档](https://mermaid.js.org/syntax/flowchart.html)、[时序图文档](https://mermaid.js.org/syntax/sequenceDiagram.html)、[事件绑定说明](https://mermaid.js.org/config/usage.html)。这些链接是调研出处，不是运行时依赖；在线文档随版本变化，不能代替产品的固定合同。

## 采纳到产品的规则

以下是结合 PANGEA 结构化产物合同重新编写的规则，不是原样装载上游 SKILL.md。

1. **流程图表达业务选择与结果。** 动作、判断、状态/资源变化、恢复及出口分开表达；连线只承载条件或事件。`recv_fin`、`recv_rst` 等有独立处理时需展示对应处理节点，不能把完整处理过程压缩为边标签。正常关闭和预期拒绝不等于产品缺陷。
2. **以源码决定分支深度。** 在当前对象与必要依赖内核实触发条件、处理动作、业务结果和回接/退出位置。没有证据时说明未知，不为满足图形形式虚构清理动作、调用关系或时序。
3. **复杂图按真实业务边界拆分。** 父图保留判断和分支去向，子图展开处理；不能通过限制节点数量省略分支。节点与连线使用稳定 ID，显示名称不承担身份匹配职责。
4. **时序图表达参与方之间的交互。** 区分请求、响应、异步事件；明确互斥分支、循环、并发和中止。对应 Mermaid 的 `alt/else`、`loop`、`par`、`break` 等概念，实际输出仍遵守产品图任务合同；不能把流程节点顺序直接当作跨参与方调用顺序。
5. **表达与正确性分别验证。** 上游 skill 提供表达方法，源码和 Agent/Reviewer 判断语义；宿主只处理明确结构与展示。路径的 `edge_ids` 数量、顺序及端点对应需确定性核对，但不授权宿主猜测缺边、补造节点或改写业务路径。

流程分析与复核规则分别落在 [scene_flow.md](../src/pangea_agent/rubrics/builtin/scene_flow.md) 和 [scene_common_review.md](../src/pangea_agent/rubrics/builtin/scene_common_review.md)。时序图指导由 companion 的 `src/sequence-guidance.md` 提供。新规则按现有冻结机制进入任务，旧任务继续遵守原合同。

## 未采用的工具与行为

- 不安装或自动更新上述 skills，不调用其联网安装、`npx`、Docker、Mermaid Live 或外部模型；用户内网运行无需连接 GitHub。
- 不把 Mermaid 源码作为 `behavior-flow-v1` 的替代物。分析 worker 继续提交结构化流程，由宿主确定性渲染；时序图使用其自身显式任务合同。
- 不引入 Mermaid 或 beautiful-mermaid 运行依赖，不因为公开仓库 stars 较高而替换现有渲染器。`mermaid-js/mermaid` 和 `lukilabs/beautiful-mermaid` 是渲染库，不应把它们的热度归算给同名第三方 skills。
- 不通过放宽 SVG 安全约束解决交互问题；导出交互、稳定命中区域和悬停重绘属于产品实现职责，skills 本身不能修复这些问题。
- 不把这些外部指南当作新的语义门禁，不增加完整复核轮次，不自动续接、重做分析或修改历史记录。结构告警和语义审核沿用产品权限边界。

## 上游版权与许可

为明确方法出处，保留下列已核实的上游 MIT 版权与许可。项目新增的适配说明不表示上游作者认可或维护 PANGEA；若以后复制更多上游文件，应重新核对那些文件的许可及其第三方来源。

### softaworks/agent-toolkit

```text
MIT License

Copyright (c) 2026 Leonardo Flores

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### WH-2099/mermaid-skill

```text
Copyright 2026 WH-2099

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
