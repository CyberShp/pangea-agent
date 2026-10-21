# 三图格式诊断与修正

`diagram-contract-v1` 只在新 task 的 `record_contracts.diagram` 中冻结。Python
`diagram_contract.diagram_diagnostics` 与 companion `src/diagram-contract.js` 的
`diagramDiagnostics` 使用同名 fixture；完整诊断 payload 由
`verification/diagram_contract.py --js-root <companion目录>` 比较。

诊断只判断对象/数组/字符串、枚举、ID、引用、片段的连续占用区间与已声明消息顺序。
不判断业务分支是否完整、源码理解是否正确，也不创造节点、消息、变量或源码依据。
已有 flow 写入格式检查保持原有规则，附图诊断不新增拒写条件。

| classification | 含义 | 处理 |
| --- | --- | --- |
| invalid | 确定性字段类型、枚举或引用不符 | warning，保存原值，本轮提示原 worker 局部修正 |
| valid_but_unsupported | 已知合法的旧文字流程不能绘制为结构图 | info，展示能力边界，不追责旧 worker |
| unknown | 未知格式版本，或 worker 明确声明 partial/unknown/not_applicable | info，保留原结论，不假设合法或完成 |
| missing | 新冻结合同字段或附图未提供 | info，保留已有结果，请原 worker 确认 |

消息数组的片段占用区间必须连续且互不重叠；互斥/并行 operand 的声明顺序与数组中各分支
存放顺序互相独立。布局按 operand 声明分别展示，原 messages 不改写。
旧 writes 字符串数组逐项展示；新冻结合同提示 writes 应为字符串。
显式 `main_path_ids` 数组或 `layout_version=stable-v1` 均使用声明主路径，不以 paths[0] 推测。

保存到 result.warnings 的每条诊断含 `kind=diagram_format_diagnostic`、contract_version、
diagram、field、code、classification、severity、message，以及 exact result_path/data_root/
run_id/action_id/task_id、record_id、record_revision。`revision` 是诊断产生时的记录修订，
result.revision 后续增长不会使未修正记录的诊断消失。

读写与完成响应提供 `diagram_diagnostics` 当前投影；compact 当前视图把这些诊断作为 warning
项目分页返回。报告/UI 用 `active_diagram_diagnostics` / `activeDiagramDiagnostics`，只显示
当前 binding 中尚未 supersede 的同修订记录；历史 warnings 和原始结果始终保留。
Graph 通过 `copy_result_for_closure` 显式复制原结果到 closure 的新 action/result_path，
保留同一个 task_id，并在 source_diagnostic 保留原诊断身份。未修正的有效诊断继续可见。

写入成功后的 repair_hint 直接回给当前 worker，要求同 task/result_path 的 supersedes 修正。
如果提示被忽略，诊断保留给既有 Review/closure；没有新自动续接，不以固定重试次数判失败。
等待修复沿用现有身份/路径/不可读取结果规则；未提交完成声明或声明未完成不会成为成功交付。
