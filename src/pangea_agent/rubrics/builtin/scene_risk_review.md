# 风险场景复核（risk-method-v1）

与 scene_common_review、scene_risk 和语言风险方法一起使用；本文件与 scene_common_review 对同一事项规定不同时，以本文件为准。复核的对象是风险证据和检查范围，不是重做分析。

## 独立盲审（标准型 independent_review）

你看不到任何 Analysis 结果。本阶段的任务是独立探测高价值对象，为对照阶段的漏报对账提供探针。

1. 读 unit_plan 和各单元的风险信号，按以下优先级为每个单元选择检查对象：外部输入解析 → 跨单元资源契约 → 带锁、引用计数或取消状态的共享对象 → 由外部输入决定长度的定长缓冲区 → 其他退出点。每个单元至少选 3 个对象，不足 3 个时全部检查。
2. 按语言风险方法检查每个对象，写入 kind=check（behavior-check-v1），并填写 unit_id。盲审的检查是探针，不是正式风险。
3. 按 scene_common_review 简要核对主干流程。
4. work-finish 后等待宿主续接。

## 对照复核（comparison_review）

按 version set 中的单元逐个进行：

1. 逐条证伪正式风险：寻找能阻止触发的代码（释放点、guard、调用方检查、锁、契约）；检查 trigger、system_result、exclusion_condition 是否自洽；判断引文与结论是否相关；确认关联用例的步骤确实制造了 trigger。给出 verdict：confirmed、refuted 或 uncertain。refuted 必须有源码反证。
2. 漏报对账（仅标准型）：盲审中结论为 confirmed 或 unresolved 的每个探针，都要在 Analysis 的账本或风险中找对应记录。有对应的写 covered 并列出记录；没有对应、且你确认是问题的写 missed，并提交 finding。
3. 账本抽查：每个单元至少抽查 2 条 excluded 检查，优先选理由最短或涉及外部输入的，核对理由是否成立。
4. 账本提示："主责文件未出现在账本""confirmed 无对应风险"等诊断提示只是线索，由你判断是否需要 finding。
5. 速度型没有盲审探针：recall_items 只引用你在本阶段自己写的检查（kind=check，填写 unit_id）。从风险信号中自选至少 3 个高优先级对象检查，发现漏报时提交 finding。
6. 交付顺序：需要原 worker 修改的问题先用 comparison-finding-write 提交，finding 中用 affected_records 指向原记录（unit_id、record_id）→ 每个单元写一条复核记录（下方合同），引用 finding 的 record_id → review-decide → work-finish。
7. 复核记录引用的 record_id、risk_id、check_id、finding record_id 必须真实存在，写入时会核对。任一单元缺少复核记录时，work-finish 会提示 warning 并保留原始结果。

<!-- REVIEW-CHECK-CONTRACT:BEGIN -->
### 复核记录标准样例（kind=note，behavior-review-check-v1）

示例只说明结构，编号均为虚构。

```json
{
  "format_version": "behavior-review-check-v1",
  "unit_id": "unit-0001",
  "version_set_id": "vs-0123456789abcdef01234567",
  "risk_verdicts": [
    {
      "record_id": "rec-000004",
      "risk_id": "R-001",
      "verdict": "refuted",
      "basis": "第 64 行在 write 失败时已直接返回 rc，close 的返回值只在全部 write 成功后才赋给 rc",
      "evidence_quotes": [{"ref": "示例仓库:src/store.c:64", "snippet": "if (rc < 0) goto out;"}],
      "finding_record_ids": ["rec-000002"]
    }
  ],
  "recall_items": [
    {
      "probe_check_id": "CHK-R-002",
      "status": "missed",
      "analysis_record_ids": [],
      "finding_record_ids": ["rec-000003"],
      "basis": "Analysis 账本未检查重连失败后重试定时器是否注销"
    }
  ],
  "ledger_spot_checks": [
    {"record_id": "rec-000007", "check_id": "CHK-004", "verdict": "agree", "basis": "已代入长度上限验算"}
  ],
  "summary": "1 条风险被反证，1 个漏报探针已提交 finding"
}
```

格式规则（本版本 G1–G4 只提示，不阻断）：
- 只在 comparison_review 阶段写入；每个单元至少一条。
- version_set_id 原样使用 task.version_set_id。
- verdict 取值：confirmed | refuted | uncertain；status 取值：covered | missed | not_applicable；抽查的 verdict 取值：agree | disagree；basis 非空。
- 所有 record_id、risk_id、check_id、probe_check_id、finding_record_ids 必须真实存在且对应一致；写入时会核对，不合格逐条提示 warning，由你核对并修正。
- evidence_quotes 可选，填写时规则同 scene_risk 的引文规则。
- 写入时只核对结构和引用；不判断你的复核结论是否正确。
<!-- REVIEW-CHECK-CONTRACT:END -->
