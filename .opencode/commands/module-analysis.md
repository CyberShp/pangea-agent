---
agent: pangea-agent
description: 按用户当前自然语言要求执行模块分析
---
# module-analysis

这是用户在 OpenCode 中启动 PANGEA 模块分析的主要入口。**自然语言分析目标是唯一必需的用户输入**。用户不需要准备、查看或维护 task contract，不需要预先提供 `data_root`、`repository`、`source_scope`、设计文档路径或 Coverage 文件路径，也不需要执行任何 CLI 命令。

收到用户的模块分析要求后：

1. 若用户未指定 `data_root`，固定使用默认 `pangea-data`，不得为此向用户确认。
2. 若用户未指定 `repository`，先查看 `pangea-data/repositories/` 的一级仓库目录，并根据用户目标自动定位最相关的源码仓：
   - 只有一个仓库时直接选择。
   - 多个仓库时，优先根据模块名、协议名、目录名、入口/接口符号和核心实现文件匹配。
   - 只有当两个或更多仓库都存在同等可信、无法自动消歧的目标实现时，才向用户询问选择哪个仓库；不得把仓库列表和内部参数表整体抛给用户填写。
3. 在选中的仓库中自动定位目标模块的入口文件、接口文件和对应核心实现，形成最小 `source_scope`。`source_scope` 只填写模块明确的核心源码文件或最小实现目录；不得使用仓库根目录、明显过大的父目录，也不得因为目标关键词在其他源码中出现就扩大范围。直接调用者、配置、规格和已有测试属于后续上下文，不作为核心 `source_scope`。
4. `target` 直接从用户自然语言目标生成；例如“分析 NVMe TCP TLS”可直接使用 `NVMe TCP TLS` 作为 target，不要求用户再给内部标识符。
5. `pangea-data/inbox/` 中的需求、设计、已有用例等资料和 `pangea-data/coverage/` 中的 Coverage 由 PANGEA 现有准备/索引流程关联。不得要求用户先提供“设计文档路径”或“Coverage 文件路径”；只有现有目录中存在多个内容冲突且无法由目标/仓库自动判定关联时，才允许做最小化消歧。
6. 为自动定位 repository / source_scope，可以且必须读取或搜索 `pangea-data/repositories/` 下的用户业务源码。这里的自动发现不等于研究 `pangea-agent` 自身实现：首次 `module-analysis` 前仍不得为了理解流程去读取项目 `README`、`src/pangea_agent/`、`schemas/`、worker prompt、旧 Run 或 CLI help。
7. 新 OpenCode 主会话首次执行模块分析时，由主 Agent 根据上述自动发现结果在 PANGEA 内部生成任务契约并使用 `module-analysis` 创建新 Run；不要要求用户提供 contract 文件，不要在项目根目录或 `pangea-data/` 一级目录创建 task contract，也不要扫描历史 Run 决定恢复目标。
8. `module-analysis` 或 `resume-run` 返回的每条 `action=<JSON>` 是唯一的派发依据。按 action 执行 `dispatch_agent` 或 `continue_agent`，消息只包含 `task_path`，不根据 `phase` 或 Agent 回复文本推测下一步。
9. 当前子 Agent 的提交检查 `PASS` 会完成 action 绑定的会话；该子 Agent 回合正常返回后，主 Agent 按 `after_completion=resume_run` 只恢复同一 `run_id` 和 `data_root`，不得自行记录完成、轮询 Agent 或读取结果。下一阶段只由 Graph 返回的新 action 决定，直到生成 `report.md` 和 `report.html`。
10. OpenCode 恢复原主会话时沿用该会话已经持有的 `run_id`；只有用户在新会话明确指定历史 Run 时才恢复历史 Run。不得读取或执行 `.agents/pangea/dsh.md`，DSH 会话行为不属于本 command。

### 允许向用户提问的唯一原则

只有“自动发现无法得到唯一可信结论”时才提问，而且只问造成歧义的那一个事实。例如两个仓库都存在独立且完整的 NVMe/TCP TLS 实现时，只问用户要分析哪个仓库。不得因为内部 contract 字段尚未显式给出，就要求用户填写 `data_root`、`repository`、`target`、`source_scope`、资料路径或 Coverage 路径。

面向用户只报告分析阶段、自动识别到的范围和结果，不展示内部 CLI 或 task contract 细节。
