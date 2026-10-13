# PANGEA Claude 客户端入口

独立 Claude 主会话先读取本客户端 `.claude/protocols/action-lifecycle.md`，只执行 Graph 返回的 action；角色定义在 `.claude/agents/`，旧 Analysis/Closure 按角色指针读取本客户端旧协议。

Desktop 派发的外部 source-first worker 使用宿主提供的通用会话和 `docs/source-first-cli-worker.md`，只执行绑定 action，不读取本客户端主会话生命周期协议。开发与修改仓库遵循 `AGENTS.md`。
