# 业务流程投影

## 可直接照写的格式

下面是格式示例，ID、动作、证据和路径必须替换为当前 Run 的实际内容：

````markdown
# FLOW-01 示例流程
```pangea-flow
{
  "flow_id": "FLOW-01",
  "title": "示例流程",
  "mainline_steps": [
    {"step_id": "S1", "title": "提交请求", "external_action": "从支持入口提交请求", "processing": "实现中的实际处理", "state_change": "实际状态变化", "external_observation": "实际可观测结果", "evidence_ids": []}
  ],
  "branches": []
}
```

## 推导正文
记录源码依据和未决项。
````

在工作台投影的 business_flows 中绑定这份文件：

```json
{"flow_id": "FLOW-01", "title": "示例流程", "document_path": "活文档/流程讲解/流程-FLOW-01-示例流程.md"}
```

常见错误：将代码块写成 `json`，只在上方放一个“pangea-flow”标题；或只在文档内部填 document_path，却漏掉工作台投影的 document_path。这两种写法都不能完成发布绑定。
每次 publish-stage 成功后，读取当前 Run 的 内部索引/工作台投影.json 中 business_flows 和 flow_document_warnings，逐条确认 document_status=parsed、主干及分支与原文相同；命令返回的 publication 摘要不包含这些内容。没有绑定或未解析时，由原 Agent 修正同一文档/投影并重新发布；未修复前如实记录展示未完成。禁止让程序从标题或自由正文猜步骤。

流程文件统一位于 活文档/流程讲解/。在 Markdown 内写一个 `pangea-flow` fenced 内容块，内容是 JSON 对象，包含 flow_id、title、mainline_steps、branches 和需要的证据/关联字段。它是主干和分支的唯一结构化版本；详细推导在同一 Markdown 正文展开，不另写重复步骤。
投影 business_flows 项只需保留 flow_id、title、document_path（相对 Run 的该 Markdown 路径）及索引关系。publish-stage 会复制内容块到投影；每完成一个流程即可发布，无需等待整个 Step 03。旧版直接填写 mainline_steps/branches 的投影仍可读取。
内容块未写完或无法读取时保留原内容并提示未解析，不把它描述为零步骤流程。工具只复制明确字段，不从自由正文猜测流程。

在现有 business_flows 项上保留 flow_id/title/description/entry/evidence，添加 mainline_steps 与 branches；Markdown 仍是分析事实载体。由 Agent 写明语义，工具不推导。
mainline_steps 按顺序，每项 step_id,title,external_action,processing,state_change,external_observation,evidence_ids。
branches 每项 branch_id,from_step_id,kind,condition,processing,result,residual_state,external_observation,to_step_id 或 terminal_result,status,linked_risk_ids,linked_test_case_ids,evidence_ids。
kind 可用 normal/exception/timeout/retry/recovery/concurrency/other；方向与接回点以源码为准，不确定时保留描述和 status=unresolved。
ID 在 Run 内稳定；未提供不等于不存在。不要从覆盖率 BRDA 编号推测条件真/假。引用现有 Risk/Case/Evidence ID，未知引用供复核说明，不补造目标。

流程以独立入口/业务目标区分，分析组不等于流程；相关分支不能因合并源码阅读而消失。
阶段 02 可以先发布目录草稿；阶段 03 已分析流程提供真实 mainline_steps，直线流程允许 branches=[]。不确定的分支方向/接回点保留 unresolved，不补造。
流程/分支可带 linked_gap_ids；test_cases 使用 verification_goal、linked_flow_ids、linked_branch_ids、linked_gap_ids 连接路径和补测。所有 ID 在当前 Run 内保持稳定，正文与投影一致。
