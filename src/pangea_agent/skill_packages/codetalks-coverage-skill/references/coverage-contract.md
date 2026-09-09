# 覆盖输入契约

新建 coverage_input 为 {kind: query, query: {product,c_version,module,b_version?}} 或 {kind: file,path}。版本精确保留空格。代码范围可选，但仓库必须已关联。
查询 Skill 位于当前 Run inputs/coverage-query-skill，先读其 SKILL.md，再用运行请求给出的 runs coverage-prepare 命令发起只读 combined 查询。不要自行调用多条旧查询命令，不读取秘密配置。
coverage-prepare 返回摘要和前 50 个缺口；coverage-page 接受 --cursor/--limit（最多 200）、--source、--file-path、--kind。遍历 next_cursor 到 null，按业务路径分组后记录当前组与已完成 gap_id。

combined 的 sources 按 source×指标返回汇总，不是每条一种来源。未覆盖函数/行/分支分别在 uncovered_functions/uncovered_lines/uncovered_branches，每个外层项包含 source,file_path 与同名数组；分支项含 line,block,branch,count。
success 可分析；partial 分析有效来源并保留 missing/warnings；no_data 没有可用数据，error 为查询失败。后两者不能宣称缺口清零，保存受阻原因并等待修正输入。再次运行 prepare 可重试失败查询，已成功/partial/no_data 的结果不重取；需要新报告时新建 Run。
count='-' 是未知，既不算命中也不算缺口。CLI 的记录数和模块分母不同口径，不要求相等。

CSV/XLSX 使用明确列 source,file_path,kind,count,function,line,block,branch。kind 为 function/line/branch，count 为非负整数或 '-'，不接受百分比。function 必填 function；line 必填 line；branch 必填 line/block/branch。表格覆盖范围可能不完整，不生成比例或推断缺失项已覆盖。JSON 支持 combined，不支持无文件归属的旧扁平输出。

文件路径是候选：范围为空可列举原仓库路径定位，再用 runs prepare-source --data-root ... --run-id ... --scope <path> 冻结所需文件后读源码。不要根据行号重叠自动解释分支真/假。

## 目标范围和显式处置
原始报告、目标行为、读取的依赖分别记录；Python 不根据 target 文字自动判范围。
在现有投影 coverage_gaps 逐项加入 scope_status（in_scope/out_of_scope/unresolved）、scope_reason、scope_evidence_ids、linked_flow_ids、linked_branch_ids；未提供状态显示 unclassified。
范围依据写明目标入口、调用/共享状态/资源和受影响结果，不能只用同目录/同函数作理由。无法判断时明确缺证据，不能静默剔除。
coverage-page 可加 --scope-status、--flow-id、--query；这些只筛选已记录字段/原始标识，未筛选分页用于核对全部输入。筛选后 gap_id 不变。
scope_summary 统计全输入；total 是当前筛选数量。designed_in_scope 仅表示范围内缺口关联了已发布 Case ID，不证明测试已执行、路径充分或覆盖提升。
traceability_warnings 是缺字段/引用/记录遗漏的只读提示，Agent 按原文件修正；不改变语义结论或生命周期。重复 gap_id 的归属不作猜测，保留待核对。
