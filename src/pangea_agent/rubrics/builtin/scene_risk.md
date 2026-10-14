# 风险分析职责（risk-method-v1）

本场景优先于 scene_common_generation；具体检查程序读取 c_cpp_risk_methods 或 lua_risk_methods。G1–G4 当前均为 warning，不阻断写入或完成；由原 worker 核对并修正。

## 交付与顺序

1. 读 purpose、risk_lenses、unit_risk_signals 与源码，列出资源、缓冲区、计数上限、共享状态、输入解析、状态修改后退出点和公开接口。信号是线索，可能遗漏宏、内联、函数指针和条件编译对象。
2. 按镜头逐对象检查并保存 kind=check 账本。优先级：外部输入解析 → 跨文件/单元资源契约 → 锁、引用计数、取消状态 → 外部长度决定的定长缓冲区 → 状态修改后退出点 → 其余接口契约。
3. confirmed 检查与对应 kind=risk（behavior-risk-v2）同批提交；一条风险只写一个失效机制。
4. 保存 kind=flow 的结构化业务流程，映射业务入口与风险路径。流程用于表达，不代替缺陷发现。
5. 设计 kind=test_case、purpose=risk 的验证用例，risk_refs 指向真实风险。
6. summary 汇总账本统计、未完成范围及原因、验证缺口和主要结论。

可分批保存，逐次使用返回的 revision。时间不足时，先给每个主责文件、高优先级对象留下检查；未完成写 unresolved，说明原因。

## 对象检查与结论

- 主责文件至少出现在一条检查里；没有检查对象的文件用 lens=scope、conclusion=not_applicable 说明依据。同质对象可合并，sites 列出全部实际读过的位置。对应信号时填 signal_ids，优先覆盖输入、计数、锁、取消状态。
- 每条检查按“对象 → 具体假设 → 主动反证 → 结论”执行。假设写明位置、输入/时序、后果，不能只说“可能泄漏/越界”。
- 反证检查释放点、guard、调用方、类型宽度、锁和成文契约；向上追到公开入口或冻结边界，向下读允许的被调实现。reasoning 写查过的位置及为何能/不能推翻；excluded 附反证；unresolved 写缺失证据；其余为 confirmed 或 not_applicable。
- 找不到公开入口到达位置的调用序列，记 unresolved，不写正式风险。只有调用方违反公开契约才触发时记 excluded（API 误用），附契约依据；合法调用中实现自行破坏契约才构成风险。

## 正式风险判据

- 依据须可追溯：违反冻结需求、设计或公开规范；或源码证明未定义行为、越界、破坏/丢失数据、泄漏、竞态、安全边界破坏。仅猜测正确预期的记 unresolved。
- 参数拒绝、预期错误码、异步受理中间态属于正常行为；若同时破坏状态、传播错误或恢复失败，按该失效记录。
- 已证实的失效不因业务入口未核实、缺观测/注入设施而消失，验证用例如实声明 unknown/needs_instrumentation；可达调用链仍须有依据。
- system_result 按“触发前状态 → 副作用 → 失败点 → 调用方处理 → 最终状态”推导。confirmed 的 reasoning 列出已尝试的反证及为何不能阻止触发。
- exclusion_condition 只写能阻止完整 trigger、证明不可达或使操作有定义的条件，须与 trigger 自洽；sanitizer 是观测设施。历史缺陷核实当前版本；Coverage 只辅助排序/关联。

## 引文与验证

risk 的每个 source_evidence、check 的 confirmed/excluded 依据附 evidence_quotes：ref 与引用完全一致；snippet 从该行段逐字摘录最短充分片段。忽略空白差异；每条跨度 ≤40 行，snippet ≤400 字符。不能改写、拼接不连续片段或引用无关 include。Python 只核对文字是否在所引位置，相关性和充分性由 Agent/Reviewer 判断。不符时提示 warning 并返回实际片段。

- 不设 70% 黑盒比例目标，层级如实声明。库/API 默认 interface_contract，有真实产品入口才用 business_blackbox，入口必须可执行。
- 用例步骤实际制造 trigger，正常路径不能替代风险验证。风险允许暂无用例，external_observation 与 summary 说明缺口，不硬造配对。
- sanitizer、故障注入、驱动不足时写 needs_instrumentation，说明所需设施。

## 零风险与修正

账本覆盖主责文件、高优先级对象有结论时，零正式风险合法。summary 写范围与 unresolved，不宣称安全；Reviewer 不因零风险要求补造。定向修正确认风险不成立时，由原 worker 用 result-supersede 显式替换为 kind=check、conclusion=excluded 并写反证，保留审计历史及其他单元内容。

<!-- CHECK-CONTRACT:BEGIN -->
### 检查记录标准样例（behavior-check-v1）

示例只说明结构，内容是虚构代码。

```json
{
  "format_version": "behavior-check-v1",
  "check_id": "CHK-003",
  "lens": "boundary",
  "object": "日志前缀格式化到 prefix[128] 后按返回值追加正文",
  "sites": ["示例仓库:src/log.c:30-41"],
  "signal_ids": [],
  "hypothesis": "前缀被截断时，用 snprintf 返回值作为偏移追加正文会越过 prefix 末尾",
  "conclusion": "excluded",
  "reasoning": "第 33 行在 n < 0 或 n >= sizeof prefix 时直接返回 -1，后续偏移 n 最大为 127；追加使用 sizeof prefix - n 作为剩余长度",
  "evidence_quotes": [
    {"ref": "示例仓库:src/log.c:30-41", "snippet": "if (n < 0 || (size_t)n >= sizeof prefix) return -1;"}
  ],
  "risk_ids": []
}
```

格式规则（本版本 G1–G4 只提示，不阻断）：
- body 直接是 JSON 对象，字段名与样例一致。
- 必填：format_version、check_id、lens、object、sites、hypothesis、conclusion、reasoning。
- lens 取值：error_path | resource_lifecycle | boundary | concurrency | untrusted_input | contract | scope（scope 只用于说明文件或区域没有检查对象）。
- conclusion 取值：confirmed | excluded | unresolved | not_applicable。
- sites 非空，元素为 repo_id:path:line 或 repo_id:path:line-line。
- conclusion 为 confirmed 或 excluded 时 evidence_quotes 必填，规则同风险引文；引文的 ref 不要求与 sites 一一对应。
- signal_ids 可省略；填写时必须是本 Run 风险信号中真实存在的 signal_id。
- 关联风险写 risk_ids（数组）；不要在顶层写 risk_id、case_id、test_case_id、flow_id、coverage_id。
- 复核阶段写入的检查还必须填写 unit_id（计划中的真实单元）。
- 写入时只检查结构、取值、引文和 ID 是否存在；不判断检查是否充分、结论是否正确。不合格会逐条提示 warning，由同一 worker 核对并修正。
<!-- CHECK-CONTRACT:END -->

<!-- RISK-CONTRACT-V2:BEGIN -->
### 正式风险标准样例（behavior-risk-v2）

示例只说明结构，内容是虚构代码。

```json
{
  "format_version": "behavior-risk-v2",
  "risk_id": "R-001",
  "title": "配置写回失败的错误码被关闭操作的返回值覆盖",
  "trigger": "保存配置时 write 返回错误，随后 close 成功",
  "system_result": "函数用 close 的返回值覆盖 rc，向调用方返回 0；调用方认为保存成功，配置文件实际未完整写入",
  "external_observation": "在磁盘写满或注入 write 失败的条件下保存配置，接口返回成功，但重新加载后配置为旧值或被截断；需要写失败注入设施",
  "exclusion_condition": "全部 write 成功；或调用方在返回后自行校验文件内容（当前已读范围未发现该校验）",
  "source_evidence": ["示例仓库:src/store.c:61-70"],
  "evidence_quotes": [
    {"ref": "示例仓库:src/store.c:61-70", "snippet": "rc = close(fd);"}
  ],
  "linked_test_case_ids": ["TC-007"]
}
```

格式规则（本版本 G1–G4 只提示，不阻断）：
- body 直接是 JSON 对象，不外套 risk，不用内层 body 包正文。
- 必填：format_version=behavior-risk-v2、risk_id、title、trigger、system_result、external_observation、exclusion_condition（非空字符串）；source_evidence（非空数组，元素为 repo_id:path:line 或 repo_id:path:line-line）；evidence_quotes（非空数组）。
- evidence_quotes 的 ref 集合必须与 source_evidence 完全一致；每个 snippet 是所引行段中逐字存在的片段（忽略空白差异），不超过 400 字符；单个引用跨度不超过 40 行。
- linked_test_case_ids 是数组，可以为空；flow_refs 写在测试用例中。
- 使用 external_observation、system_result、linked_test_case_ids，不使用 observed、result、test_case_refs。
- 不包含 remediation_hint、fix、patch、suggested_patch。
- 写入时只检查上述结构和引文是否逐字存在；不判断风险是否真实、引文是否相关。不合格会逐条提示 warning，由同一 worker 核对并修正。
<!-- RISK-CONTRACT-V2:END -->
