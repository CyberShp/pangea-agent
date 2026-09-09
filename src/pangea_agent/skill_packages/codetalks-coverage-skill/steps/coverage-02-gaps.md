# 阶段 02：业务路径与缺口范围
先按 references/path-and-case-design.md，在活文档/缺口台账.md 建立目标业务的轻量入口/路径目录：稳定 FLOW/BRANCH ID、外部入口、独立业务目标、相关正常/拒绝/异常/恢复或耦合路径、定位依据和待确认项。不先完成全部深度分析，不固定流程数。
再完整遍历 coverage-page，按来源/文件/类型保留每个 gap_id。结合冻结源码与设计记录 scope_status=in_scope/out_of_scope/unresolved、scope_reason、scope_evidence_ids。尚未判定项明确待办，不默认为范围内。原始实测字段原样保留。
共享函数只纳入与目标有关的调用/状态/资源影响路径，在 scope_reason 说明关联链。独立范围外行为不因共用源码纳入；证据不够就 unresolved。有依据的范围外不是已补测，也不是遗漏后不留记录。
从入口→实现、缺口→入口双向核对，避免只有函数分组而遗漏目标行为。按可共享源码组织分析组，但不把一组直接当成一个 FLOW 或一个 Case。
缺口台账同时记录组、linked_flow_ids、linked_branch_ids、定位、analysis_status、disposition、未决项。分支方向未证实时不猜测。路径尚未深入时可先发布标题草稿，analysis_status=pending。
每批更新现有投影中的 coverage_gaps 和 business_flows；保存当前页 next_cursor、待判定 gap_id、路径队列及下一条读取位置。发布后 coverage-page 的 scope_summary 可用于核对记录数，不证明语义正确。遍历至 next_cursor=null，未归属项不得被称为已全部纳入补测。
