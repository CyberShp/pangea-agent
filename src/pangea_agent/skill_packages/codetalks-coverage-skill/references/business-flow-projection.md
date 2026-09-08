# 业务流程投影

在现有 business_flows 项上保留 flow_id/title/description/entry/evidence，添加 mainline_steps 与 branches；Markdown 仍是分析事实载体。由 Agent 写明语义，工具不推导。
mainline_steps 按顺序，每项 step_id,title,external_action,processing,state_change,external_observation,evidence_ids。
branches 每项 branch_id,from_step_id,kind,condition,processing,result,residual_state,external_observation,to_step_id 或 terminal_result,status,linked_risk_ids,linked_test_case_ids,evidence_ids。
kind 可用 normal/exception/timeout/retry/recovery/concurrency/other；方向与接回点以源码为准，不确定时保留描述和 status=unresolved。
ID 在 Run 内稳定；未提供不等于不存在。不要从覆盖率 BRDA 编号推测条件真/假。引用现有 Risk/Case/Evidence ID，未知引用供复核说明，不补造目标。
