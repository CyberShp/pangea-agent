# 输入材料真实消费规则

## 目的

防止 Agent 只确认文件存在，却声称已使用其中证据。

## 每份材料必须记录

- id
- type
- raw_path
- verified_path
- parser
- consumed_ranges
- sheets/pages/sections
- records_parsed
- extracted_facts
- used_by_passes
- unread_ranges
- status
- limitations

每份资产使用 inputs/assets/manifest.json 的 asset_id 作为 id（或显式 asset_id），引用冻结文本和附件路径。实际用于结论时记录 linked_flow_ids、linked_risk_ids、linked_test_case_ids 与 consumed_ranges，未采用说明原因。规范化成功仅说明材料可读，不代表已经消费；没有读取的材料不能填已使用。重新解析后的资产只影响新任务，当前任务以冻结版本为准。

## 状态

- parsed
- partially_parsed
- blocked
- out_of_scope
- unreadable

禁止 `exists_only`。

## XLSX

必须记录：

- 工作表名；
- 表头；
- 实际解析行数；
- 公式值或显示值的口径；
- 覆盖率类型；
- 未覆盖数据；
- 与源码路径的匹配规则；
- 映射失败项。

## 材料冲突

设计表示“应该如何”；源码表示“当前如何”；日志表示“当时实际发生什么”。

冲突必须并列展示，不得静默选一个。
