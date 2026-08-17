---
description: 按用户当前自然语言要求执行 PANGEA 模块分析
---
# module_analysis

这是用户启动 PANGEA 模块分析的主要入口。用户不需要准备、查看或维护 task contract，也不需要执行任何 PANGEA CLI 命令。

收到用户的模块分析要求后：

1. 先确定目标源码仓和模块核心实现范围。`source_scope` 只填写该模块明确的核心源码文件或最小实现目录；不得使用仓库根目录、明显过大的父目录，也不得因为目标关键词在其他源码中出现就扩大 `source_scope`。
2. 如果用户只给模块名，先定位该模块的入口文件、接口文件和对应实现文件，再形成最小 `source_scope`。直接调用者、配置、规格和已有测试属于上下文，不作为核心源码范围。
3. 首次创建 Run 时，由主 Agent 在 PANGEA 内部生成任务契约并启动分析；不要要求用户提供 contract 文件，不要在项目根目录或 `pangea-data/` 一级目录创建 task contract。
4. Run 已存在时直接恢复该 Run，继续当前 phase；不得重新创建 task contract 或换新的 run_id。
5. `WAITING_ANALYSIS` 中每个 analysis-worker 必须在自己的 task 上执行 `validate-worker-result` 并得到 `PASS` 后才算完成。JSON/schema 失败由同一个 Worker 在同一 result/attempt 内修正，不属于正式 rework，也不得由主 Agent 用临时脚本代修。
6. 只有当前阶段所有必需 Worker 都通过提交校验后，才执行 `resume-run` 推进 graph；不得把 schema FAIL 的结果留给后续 review 处理。
7. 按 graph 返回的 phase 派发对应 worker，直到生成 `report.md` 和 `report.html`。

面向用户只报告分析阶段、范围和结果，不展示内部 CLI、digest/hash 或 task contract 细节。
