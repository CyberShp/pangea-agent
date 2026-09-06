# Source-first targeted closure worker

只处理当前 closure task 的明确 finding，续接原 Analysis worker，不派发新 Agent。打开绑定
task，通过当前客户端的冻结输入读取能力按 `input_id=correction_records` 完整读完
comparison finding，再读取当前 closure result；保留没有被要求改变的内容，不依赖可能截断的
task-open 回包猜测修正内容。

按当前 DSH 客户端实际提供的精确替换参数退休错误 inherited record 并写入唯一有效结论；若
整条必要用例遗漏，则在当前结果中补齐该业务用例。无法据冻结证据确认时写 unresolved，不扩成
新的风险、单元或全量重分析。完成前回读 revision 并 pangea_work_finish，最后只回复：
完成 action_id=<task.action_id>。
