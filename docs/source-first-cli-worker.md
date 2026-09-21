# Desktop 外部执行器的 source-first CLI 合同

适用 CodeAgent、NGA、Claude Code 等能执行命令的 worker。宿主派发和绑定 action，并在回合结束后 settle；worker 不调用 adapter、不创建 Run、不创建其他 Agent，也不加载其他客户端的入口或插件。只使用宿主提供的 exact data_root/run_id/action_id/task_id。

## 调用

以宿主提供的 Python 可执行文件运行 `-m pangea_agent.cli.main <command> <绑定参数> <操作参数>`。优先使用 task-open 随附的 write_contract 和下列调用方式；仅在调用报参数错误时查询对应 command 的 `--help`。返回 JSON envelope 的 `result` 是结果，`ok=false` 时保留具体错误并修正本次调用。文件路径按字面值传入。

Windows 下复杂 JSON 不通过 PowerShell 拼接。使用 Python 临时脚本，通过 `subprocess.run([sys.executable, '-m', 'pangea_agent.cli.main', command, *binding, ...], check=True)` 传参数数组；JSON 参数使用 `json.dumps(value, ensure_ascii=False)`。脚本只传递由你作出的判断，不能让脚本代做语义分析。使用宿主指定 Python 运行脚本，临时文件不能覆盖 task、源码或结果文件。

## 现有命令

- `task-open`：读取当前任务。核对 role/stage、target、inputs、allowed_paths、owned_regions 与 result_path。宿主已交付的 task 和源码片段可复用。
- `input-read --input-id ID`：读冻结 rubric、用户附件、coverage、修正记录。按 next_cursor 分页。
- `source-index --view compact`：文件导航；文件内拆分才读取 region。`source-read --repo-id ID --path PATH --view text` 可加行号；`source-search --query TEXT --view compact` 搜索冻结范围。续读保留原筛选参数，只替换 page-token/cursor。
- `result-read --view compact`：获取 revision 和当前记录。`result-write --expected-revision N --records JSON数组` 增量保存，每条普通记录使用 kind、body；kind 使用 task/rubric 支持的 note、summary、flow、test_case、risk、unresolved 等，不另造格式。
- `plan-write --expected-revision N --unit JSON对象`：保存计划单元，整文件归属用 owned_files，文件内范围用实际 owned_regions；新建不自造 unit_id，更新带回真实 unit_id。
- `result-supersede --expected-revision N --target-record-ids JSON数组 --replacement JSON对象`：原 worker 定向更正原记录，保留无关正文。先回读真实 record_id，不从用例编号推测。
- `comparison-read --version-set-id ID --view compact`：只在 comparison 阶段读取 task 指定的锁定版本。
- `comparison-finding-write --expected-revision N --unit-ids JSON数组 --finding JSON对象`：保存有源码反证的具体修正建议。
- `review-decide --expected-revision N --decision JSON对象`：按当前冻结 review rubric 和 task 的 version_set_id 做决定；修正记录使用刚返回的 Comparison finding record_id。
- `work-finish --revision N`：完成语义工作后声明当前 revision 完成。diagnostics 未 ready 时在同一结果中修正；不凭回显或退出码宣称分析通过。

操作参数以 CLI --help 及当前 task 冻结契约为准。不要直接重写结果外壳；不可读外壳仅用诊断给出的 sha256 经 result-repair 由本 worker 修复。

## 分阶段工作

Planning：先读冻结附件理解产品功能，按 target 选择主责，再划分单元。source_scope 是候选集合，不是全部交付义务。context 只作必要参考，不把整个参考集合复制给每个单元。用范围 note 说明纳入、依赖、排除及依据。整文件归属无需通读函数体；同文件包含非目标功能时可按 region 划分。context_budget 是资源截断，不证明未冻结依赖不存在；证据缺口如实记录。

Analysis：只分析本单元，先读冻结 behavior_test_generation 等 rubric，按其顺序交付业务流程、短用例和证据。至少 70% 用例以黑盒或可实施灰盒方式设计；业务配置、触发、外部响应、观测和恢复构成正文，函数/变量仅在必要注入步骤及独立证据中出现。不同业务触发路径分开，不给无关依赖模块生成用例。缺设备不等于源码无法确定预期。

Independent review：你是独立于所有 Analysis 的唯一 Reviewer。只读本 task 开放的冻结输入，不能寻找 Analysis 结果。独立核实目标行为、错误传播、恢复和预期，保存原始审查依据。完成后等待宿主在原会话续接。

Comparison review：标准型复用本会话盲审依据，通过 comparison-read 对照锁定的首轮和盲审版本；task.review_mode=speed 时直接审核锁定首轮结果，没有盲审产物，不得声称已盲审。按冻结 review rubric 核对遗漏、错误预期、不可执行前置、内部实现冒充业务操作、范围偏移。每项建议给出原结论、源码反证、建议和未证实条件；不能把假设当事实。复用已读证据，只为具体疑点或未交付分页补读。

Comparison 交付顺序：保存实际审查记录和必要 finding → 调用 review-decide --expected-revision N --decision JSON对象 → 使用返回的当前 revision 调用 work-finish。decision 中的 version_set_id 原样使用 task.version_set_id，disposition 由你选择 pass/unresolved/finding；无需修正时 correction_record_ids=[]。没有 finding 或资料不足也须提交裁决，summary/finding 不能代替 review_decision。若诊断只缺 decision，保留有效正文、补该裁决后再声明完成；当前有效 decision 已保存时才可只补 completion。宿主只执行 Graph 返回的 action。

version_set_id 必须写进 decision JSON，不能仅在说明正文中提及。修复时先 result-read 获取当前 revision，不沿用旧脚本的 revision。review-decide 返回 ok=false 表示裁决未保存；保留已有审查正文，按具体错误修正参数，不能继续调用 work-finish。命令成功后才使用返回的 revision 声明完成。

Targeted closure：你是原 Analysis worker，读取 correction_records 及当前继承结果，逐项核实反证。证据支持才更正；驳回或资料不足需说明依据。用 result-supersede 替换真实目标记录，保留有效内容，不重做整个单元。最后 work-finish。
