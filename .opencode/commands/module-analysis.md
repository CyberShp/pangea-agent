---
description: 启动或继续一次 PANGEA C/C++ 或 Lua 模块分析
---
# module-analysis

读取当前 Codetalks Skill Run 的 `request.md`，按其冻结 `SKILL.md` 和 `run_guard.py` 完整执行 Step 01–09。不创建 Graph、Action、Planning/Review/Closure/Reporting Worker，不调用 bind、validate 或 settle。生命周期只写入 `内部索引/运行状态.json`，分析产物使用 Markdown。
