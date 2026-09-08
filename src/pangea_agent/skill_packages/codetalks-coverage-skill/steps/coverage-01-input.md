# 阶段 01：输入与定位
读取 references/coverage-contract.md，记录分析对象、用户范围、版本描述与输入口径。按请求执行 coverage-prepare，用分页清单定位候选文件。
只列举必要路径，冻结最小源码范围；读取实际源码核对路径和标识符。记录未匹配和源码版本待确认，空范围不等于全仓库分析。
写活文档/输入与范围.md，内部索引/输入材料索引.json、方法论选择.json 和运行计划.json；索引格式参考原 Skill references/evidence-consumption.md 与 run_guard 校验所要求的字段。方法论只选择与补测有关项。
no_data/error 时保存输入获取事实和受阻原因，不能以无缺口完成分析；需要用户修正输入时明确所缺内容。
