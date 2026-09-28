# Desktop 外部执行器的 source-first CLI 合同

适用 CodeAgent、NGA、Claude Code 等能执行命令的 worker。宿主派发和绑定 action，并在回合结束后 settle；worker 不调用 adapter、不创建 Run、不创建其他 Agent，也不加载其他客户端的入口或插件。只使用宿主提供的 exact data_root/run_id/action_id/task_id。

## 调用

直接使用宿主提供的 Python 可执行文件运行 CLI。`--output readable` 是全局参数，放在命令名前：它以 UTF-8 输出完整 envelope 元数据，保留 ok、binding、revision、paging、header 等字段，再把 result.text 按原换行显示。其余对象缩进显示；可读输出不是单个 JSON 文档。省略该参数或使用 `--output json` 时保持原 ASCII JSON envelope，供程序解析。业务错误仍返回 `ok=false` 和非零退出码，参数错误仍由 argparse 报错；不要忽略 `$LASTEXITCODE`。

以下变量均使用宿主提供的真实路径和绑定值，不自行推断：

```powershell
$pangeaPython = '<宿主提供的Python绝对路径>'
$binding = @('--data-root', '<data_root>', '--run-id', '<run_id>', '--action-id', '<action_id>', '--task-id', '<task_id>')
& $pangeaPython -m pangea_agent.cli.main --output readable task-open @binding
& $pangeaPython -m pangea_agent.cli.main --output readable input-read @binding --input-id '<input_id>'
```

优先使用 task-open 随附的 write_contract 和下列调用方式；仅在调用报参数错误时查询对应 command 的 `--help`。禁止 `stdout[:N]`、截取正文前几行或按字符裁剪输出。按返回的 cursor/page-token 分页，每次展示完整一页。

Windows 下复杂 JSON 保存为 UTF-8 数据文件（接受 BOM），通过显式 `*-file` 参数提交，不需要为每次 CLI 调用生成 Python 包装脚本。载荷只保存你已作出的判断；逐次调用 CLI 并使用刚返回的 revision，不生成循环提交多个单元的业务脚本。`--unit-file`、`--records-file` 分别与原 `--unit`、`--records` 互斥；其余现有 JSON 参数也有对应的 `-file` 形式：target-record-ids、replacement、edits、unit-ids、finding、replace-finding-record-ids、decision、replace-decision-record-ids。内联参数只解析 JSON，不把非法 JSON 当作路径。

宿主设置 `PANGEA_WORKER_SCRATCH` 时，载荷文件必须放在该 worker 的 scratch 内，CLI 会在解析符号链接后的路径上检查归属。没有该环境变量的人类 CLI 调用可指定任意显式文件。此限制只约束 CLI 的 JSON 载荷文件读取，不是操作系统文件隔离；不能覆盖 task、冻结源码、结果文件或其他 worker 的文件。例中内容、路径及 revision 应替换为本次分析作出的实际判断和刚返回的值：

```powershell
$unitFile = Join-Path $env:PANGEA_WORKER_SCRATCH 'unit.json'
@'
{"title":"目标功能","purpose":"主责行为及必要依赖说明","owned_files":[{"repo_id":"真实仓库ID","path":"真实冻结路径"}],"context_files":[]}
'@ | Set-Content -LiteralPath $unitFile -Encoding UTF8
& $pangeaPython -m pangea_agent.cli.main --output readable plan-write @binding --expected-revision 0 --unit-file $unitFile
# 普通记录同样使用 UTF-8 JSON 数组文件：
& $pangeaPython -m pangea_agent.cli.main --output readable result-write @binding --expected-revision '<刚返回的revision>' --records-file '<scratch内records.json>'
```

输出被宿主截断时，用原绑定和 CLI 的分页参数补读缺失部分；不能把截断页视作已完整阅读。共同规则、场景规则及选定方法论按当前 task.rubric_paths 与 inputs 的 input_id 对应读取，续接复用同一会话已完整读取的冻结版本。

## 现有命令

- `task-open`：读取当前任务的精简视图。核对 role/stage、target、inputs、owned_regions 与 result_path。大字段和全范围清单不内联；`task.deferred_fields` 给出其 input_id。先按需读取当前单元归属、目标与冻结 rubric，不为获取依赖权限而遍历全范围。
- `input-read --input-id ID`：读冻结 rubric、用户附件、coverage、修正记录，也可用 `task:字段名` 分页读取当前绑定任务的原始字段。按 next_cursor 续读，默认每页 12000 字符；JSON 字段须拼接所需完整分页后解析，不把分页片段当完整 JSON。源码通过 source-index/search/read 按疑点读取，不一次性回灌整个清单或全文。宿主未预读源码不表示源码缺失。
- `source-index --view compact`：文件导航；文件内拆分才读取 region。`source-read --repo-id ID --path PATH --view text` 可加行号；`source-search --query TEXT --view compact` 搜索冻结范围。续读保留原筛选参数，只替换 page-token/cursor。
- `result-read --view compact`：获取 revision 和当前记录。`result-write --expected-revision N --records JSON数组` 增量保存，每条普通记录使用 kind、body；kind 使用 task/rubric 支持的 note、summary、flow、test_case、risk、unresolved 等，不另造格式。
- `plan-write --expected-revision N --unit-file JSON文件`（或 `--unit JSON对象`）：每次提交一个单元对象，根字段为 title、purpose 及归属，不包 units 数组或整份 plan。整文件归属 owned_files 使用 `{repo_id,path}` 对象数组；可选 context_files 使用字符串数组，格式为 `仓库ID:冻结相对路径`，无依赖用 `[]`。文件内范围用 source-index 实际返回的 owned_regions；新建不自造 unit_id，更新带回真实 unit_id；逐次使用刚返回的 revision。
- `result-supersede --expected-revision N --target-record-ids JSON数组 --replacement JSON对象`：原 worker 定向更正原记录，保留无关正文。先回读真实 record_id，不从用例编号推测。仅修改文本时优先用 `--edits [{"path":[],"old":"唯一原文","new":"更正文字"}]`，不同时传 replacement。完整 replacement 省略 kind 时继承同类型目标；混合类型目标必须显式指定 kind，失败不会退休原记录。
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

已确认 expected、步骤或变体与源码/可复现执行相矛盾时，必须提交 comparison-finding-write；“核心覆盖目标仍成立”不能使错误预期合格，不能仅写 note 后 unresolved。finding 指明原记录、反例输入、实际结果和需修改文字；设施不足的其他事实可另外 unresolved。

Comparison 交付顺序：保存实际审查记录和必要 finding → 调用 review-decide --expected-revision N --decision JSON对象 → 使用返回的当前 revision 调用 work-finish。decision 中的 version_set_id 原样使用 task.version_set_id，disposition 由你选择 pass/unresolved/finding；无需修正时 correction_record_ids=[]。没有 finding 或资料不足也须提交裁决，summary/finding 不能代替 review_decision。若诊断只缺 decision，保留有效正文、补该裁决后再声明完成；当前有效 decision 已保存时才可只补 completion。宿主只执行 Graph 返回的 action。

version_set_id 必须写进 decision JSON，不能仅在说明正文中提及。修复时先 result-read 获取当前 revision，不沿用旧脚本的 revision。review-decide 返回 ok=false 表示裁决未保存；保留已有审查正文，按具体错误修正参数，不能继续调用 work-finish。命令成功后才使用返回的 revision 声明完成。

Targeted closure：你是原 Analysis worker，读取 correction_records 及当前继承结果，逐项核实反证。证据支持才更正；驳回或资料不足需说明依据。用 result-supersede 替换真实目标记录，保留有效内容，不重做整个单元。最后 work-finish。

## 当前回合的可执行证据

当任务提供现成 C 编译器且范围是小型算术/宏代码时，在当前 Run 的独立证据目录创建最小驱动，编译冻结源码的副本并执行。不得改冻结源码；缺失外部依赖只可用明确标注的桩隔离，并说明与真实产品环境的差异。保存驱动、编译命令、输入、stdout/stderr、退出码及与原 expected 的对照，通过现有 note/evidence 引用这些文件。除零等未定义行为使用独立子进程并保留实际退出，不把崩溃后的状态写成保证。没有编译器/隔离设施时明确标注静态推导和缺口，不能宣称实测。Reviewer在当前审查回合复核具体执行证据；closure由原Worker按finding修正，不新增整轮审查。

执行证据不能只写“driver.exe stdout”作为假想路径。每个命令使用参数数组和明确 cwd；将真实输出与退出码一起写成当前 Run 内的文件，再在 note/evidence 引用该文件。例如（变量由当前任务实际路径填写）：

```python
from pathlib import Path
import json, subprocess

def capture(argv, cwd, evidence_file):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=60)
    evidence = {
        "argv": argv, "cwd": str(cwd), "exit_code": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
    }
    Path(evidence_file).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return result.returncode
```

先记录编译命令，退出码为 0 才执行生成的程序；可执行文件使用绝对路径。每个正常/异常样例单独执行，除零退出不覆盖其他样例日志。驱动里的预期若失败，应把实际输出用于复核并保留失败证据；只编译成功、命令找不到或日志为空不能写“实测通过”。报告中的 C 调用若要作为可执行步骤，直接复用已编译驱动中的变量声明、赋值和调用语句；宏的可写参数不能写成 `work=1` 作为实参，须先 `int work=1;` 再传 `work`。当前 Run 新写的驱动只证明局部代码行为，不把直接内部函数调用归类为产品业务黑盒。
