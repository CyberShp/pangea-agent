"""Advisory contradictions; classification and repair remain worker-owned."""
from __future__ import annotations
import re

READINESS_CONTRACT = "instrumentation-readiness-v1"
# Deliberately narrow triggers, not a classifier for arbitrary failure prose.
_EXPLICIT = re.compile(r"故障注入|(?:模拟|注入|强制)(?:内部)?(?:内存分配失败|握手超时|链路断开|连接中断|句柄耗尽|硬件故障|介质错误|磁盘\s*I/O\s*错误)|构造(?:并发竞态|内部锁竞争)窗口|inject(?:ed|ing)?\s+(?:ENOMEM|allocation failure|I/O error|handle exhaustion)|simulate\s+(?:handshake timeout|allocation failure|media failure|lock contention)", re.I)


def readiness_diagnostics(body):
    if not isinstance(body, dict) or body.get("execution_readiness") != "ready":
        return []
    fields = []
    pre = body.get("preconditions", [])
    if isinstance(pre, str):
        fields.append(("body.preconditions", pre))
    elif isinstance(pre, list):
        fields.extend((f"body.preconditions[{i}]", value) for i, value in enumerate(pre) if isinstance(value, str))
    steps = body.get("steps", [])
    if isinstance(steps, list):
        fields.extend((f"body.steps[{i}].action", step["action"]) for i, step in enumerate(steps)
                      if isinstance(step, dict) and isinstance(step.get("action"), str))
    return [{"kind": "readiness_hint", "code": "ready_with_explicit_injection", "field": field,
             "message": f"{field} 含显式内部故障构造措辞 {match.group(0)!r}，但 execution_readiness=ready。由原 worker 核查正常外部输入是否能稳定构造；需注入时标 needs_instrumentation，并在源码位置已知时补 instrumentation.points。否定句、已有设施或引用示例可能触发此提示；不自动改写或拒绝原文。"}
            for field, value in fields if (match := _EXPLICIT.search(value))]
