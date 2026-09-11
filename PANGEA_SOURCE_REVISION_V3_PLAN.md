# 源码准备与局部修订 V3

2026-09-12，用户确认实施及验收。承接 V2，工作树 codex/pangea-semantic-analysis-rework。

1. OpenCode 分派首轮/盲审时，从当前 Graph task 明确的源码归属准备原文，在新增 60000 字符上限及 task 上下文预算三分之一内交付；未交付页列出准确后续请求。首轮和盲审只收到授权源码，盲审不读取首轮结果。
2. 现有 result-supersede 增加 edits 模式。Agent 提供唯一目标记录、正文字符串字段路径及 old/new；程序只执行唯一匹配替换，保留其他正文、证据、引用和原始版本。失配不写入，诊断交同一 worker；不改变 Run 的语义状态。
3. Closure 按当前 task 的 correction_records 中明确的 evidence 地址读取冻结原文，按 finding_record_id 标注；在总材料预算内提供原记录，未提供记录列出精确 ID。普通引用不增加硬门禁，未解析或无权限的引用记录 warning；不足的证据由 Agent 继续核实。

原文交付、文本替换和引用呈现属于确定性操作；Python 不判断建议真假、不生成语义、不自动选择补充证据、不扩展冻结范围。模型可以拒绝不成立的反证，或保留 unresolved。

验收顺序：本地源码重建/分页/局部改动/失败恢复/盲审隔离；M2.7 正误建议小样本重放；相同 SPDK commit、完整 nvme_auth.c、204800 预算、普通 M2.7 的完整 OpenCode 与 Desktop ACP 串行验收。记录各阶段耗时、交付与模型输出、返修保存和最终报告。20 分钟是实验目标，错误结论仍存在则不能判质量通过。

证据目录：/Users/shepard/.codex/outputs/pangea-source-revision-v3-20260912。

## 验收结果

实现与 24 项相关本地检查完成。两次有效普通 M2.7 完整运行：OpenCode 24 分 35 秒、Desktop ACP 20 分 25 秒，均 complete / UNRESOLVED。目标源码在分析和盲审中均完整交付；43/43 与 47/47 写入回执匹配，首轮原文完整保留。Desktop 已打开最终 HTML 报告核对。

稳定提速和返修质量未通过：模型没有稳定采用局部编辑；两轮规划漏选关键桩实现，错误的 feature-off 建议仍进入返修结果；独立复核仍大量重复输出。完整证据与限制见证据目录 ACCEPTANCE.md。
