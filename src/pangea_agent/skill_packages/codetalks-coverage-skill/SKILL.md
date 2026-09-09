---
name: codetalks-coverage-skill
description: 基于函数、行、分支覆盖输入和源码定位覆盖缺口、分析可达业务路径并设计补测；用于覆盖率分析，先盘点目标业务路径，再围绕相关缺口深入分析。
metadata:
  version: "1.1.0"
  derived_from: codetalks-skill-1.4.0 / codetalks-fused-v2.4
license: CC-BY-SA-4.0
---

# 覆盖率缺口分析

从 codetalks-skill 1.4.0 复制适配。Markdown 是分析事实载体，JSON 仅作输入、运行索引和工作台显示。
先建立目标范围内的轻量业务入口/路径目录，再沿相关缺口分析必要调用链；共用源码可合并阅读，但分析组不等于流程或用例。参考 references/path-and-case-design.md。不要求全模块架构、固定风险数量或必做 SFMEA。

读取 references/path-fidelity.md、references/evidence-consumption.md、references/markdown-narrative-first.md 后按当前 Run 请求调用 scripts/run_guard.py init，scenario 固定 coverage-analysis，mode 保留用户选择。
init 参数为 --skill-root、--workspace（Run 根目录）、--source-raw、--source-verified、--scenario coverage-analysis、--mode speed/depth；续跑追加 --resume。
运行环境提供的 Python 路径优先。执行 ack-core --workspace <Run> --all；此后通过 start-step/complete-step --workspace <Run> --step 01..05 顺序执行，每次只读取返回的当前步骤文件。

## 五阶段

1. steps/coverage-01-input.md：读取输入、查询或导入，定位源码。
2. steps/coverage-02-gaps.md：目标业务路径盘点、缺口范围判定、来源核对。
3. steps/coverage-03-analysis.md：定向分析与黑盒补测。
4. steps/coverage-04-review.md：独立复核和定向修订。
5. steps/coverage-05-delivery.md：交付及 finalize。

每完成一组缺口持久化台账、用例和游标，用 progress 记录真实进度，publish-stage 发布投影。compact 后读取当前运行状态、计划、缺口台账和当前组，复用已获取输入，禁止从头重复查询或重写已完成内容。工具用法按 run_guard.py --help 与子命令 --help。

## 覆盖事实

首次获取前读 references/coverage-contract.md。只使用当前 Run inputs/coverage 下的输入和 CLI 分页，不把全量 JSON 填进上下文。
未覆盖不等于缺陷。函数命中不证明具体分支覆盖；branch 0/1 不是条件真/假。缺失、未知计数和无数据不作为零命中。来源间不相加、平均或推测并集；没有完整分母时只报缺口数。
报告版本未给出源码提交。Agent 核对路径、函数与上下文，记录版本待确认；行号关联只能定位，不能证明条件方向。不能匹配、同名歧义和分支方向未知保留为未决项；有证据确认的范围外缺口保留排除依据，不计为已完成补测。

## 分析和交付

引用冻结源码 repo_id:path:line。补读依赖前用当前 Run prepare-source 入口复制，不重写已冻结文件；额外依赖不扩大用户补测范围。
以受支持的外部入口、可构造条件、内部保护/判断、后续状态、外部结果形成完整解释。主动寻找反证及不可达原因，只有源码支持时才能作不可达结论。
用例包含前置条件、外部操作/注入、独立 Oracle、预期结果、观测和清理恢复。函数和行号只作开发解释和追溯。存在真实风险时补充风险与 SFMEA；风险允许为空。
coverage_status（实测）、analysis_status（分析）和 disposition（补测处置）分开保存；用例已设计不等于执行成功或覆盖率提升。
深度型必须由真实独立 Reviewer 复核，读取 references/worker-judge-protocol.md 中 Judge 身份与反证要求；不得伪造独立审查或切到 speed 绕过。语义结论 PASS/UNRESOLVED 如实记录。

投影保留 business_flows、risks、test_cases、evidence、review_issues 五个数组，另加 coverage_gaps。每条保留 CLI gap_id、source、file_path、kind、raw 和 coverage_status；范围字段见 references/coverage-contract.md。Agent 填写 analysis_status、disposition、source_location、trigger_path、guard_conditions、external_result、uncertainties、linked_test_case_ids、evidence_ids。可带 linked_risk_ids。
阶段 02 先发布路径目录，阶段 03 为已分析路径按 references/business-flow-projection.md 填入真实主干/分支。只有标题的条目是待补齐草稿，不代表已分析。阶段 04 复核遗漏与独立用例边界，不以流程数量相同为验收标准。

可执行用例格式见 templates/黑盒测试用例Markdown模板.md；仅在生成测试时读取。识别 Lua 时读 references/language-lua.md，openUBMC 时另读 references/openubmc-lua.md。
