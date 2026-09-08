# PANGEA Codetalks Skill 直启规则

PANGEA 新模块分析使用 `codetalks-skill 1.4.0` 五阶段（来源 `codetalks-fused-v2.4`），其他场景以冻结 manifest 为准，不再存在旧 Graph、Planning/Analysis/Review/Closure/Reporting action、bind、schema repair 或 settle 生命周期。语言 Profile 由已验证源码范围自动识别；检测到 Lua 或 openUBMC Lua 时读取冻结 Skill 中的对应参考文件。

收到 Desktop 创建分析后：

1. Desktop 在启动本会话前已创建唯一 Skill Run；不得再创建第二个 Run；
2. 读取启动消息中给出的 `request_path`；
3. 完整读取请求中指定的 `SKILL.md`；
4. 使用 Skill 的 `run_guard.py` 按当前 Run 冻结 manifest 执行，模块分析为五阶段；
5. Producer 完成按流程分析后，按 Skill 规则创建独立 Judge 完成复核阶段；
6. `finalize` 成功后结束。

Run 的唯一生命周期文件是 `<run_root>/内部索引/运行状态.json`。不得创建 `progress.json`、`final-state.json`、`agent-results/`、action JSON 或旧式报告文件。

任何步骤门禁失败时，保留当前步骤和工件，向用户报告 `run_guard.py` 的原始错误；不得跳步或伪装完成。
