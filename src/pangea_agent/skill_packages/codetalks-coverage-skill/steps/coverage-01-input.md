# 阶段 01：输入与定位
读取 references/coverage-contract.md 与 references/path-and-case-design.md 的流程及耦合规则。
在活文档/输入与范围.md 分别记录报告范围、目标行为边界、版本描述、用户源码范围与输入口径。target 是业务目标，目录只是源码定位约束；共享函数不等于全部调用者都在范围内。
按请求执行 coverage-prepare，用分页清单定位候选文件；识别目标入口、运行时接线和设计约束，定位必要依赖后冻结源码。
只列举必要路径，读取实际源码核对路径和标识符。记录未匹配和源码版本待确认，空范围不等于全仓库分析。补读依赖不扩大补测目标。
更新内部索引/输入材料索引.json、方法论选择.json 和运行计划.json；索引参考 references/evidence-consumption.md。方法论只选择与补测有关项。
方法论 ID 从 inputs/methodologies/catalog.json 原样读取，不把文件标题当 ID。方法论选择.json 使用 schema_version="1.0"、selected/excluded 数组；每项包含 methodology_id、reason、evidence（非空字符串数组），selected 保留基础 codetalks-skill。覆盖率工作流 ID 与基础方法论 ID 不可混用。输入材料索引使用 items 数组，逐份记录实际消费事实。
no_data/error 时保存输入获取事实和受阻原因，不能以无缺口完成分析；需要修正输入时明确缺少内容。
