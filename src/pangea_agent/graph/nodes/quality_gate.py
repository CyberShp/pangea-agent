from __future__ import annotations

from pangea_agent.graph.state import PangeaState


def quality_gate(state: PangeaState) -> PangeaState:
    unresolved = []
    for risk in state.get("risks", []):
        if risk.get("translation_status") == "Developer-confirm":
            unresolved.append({"risk_id": risk.get("risk_id"), "reason": "尚缺少可执行复现条件或外部观测"})
    status = "PASS" if not unresolved else "UNRESOLVED"
    return {
        **state,
        "quality_report": {
            "status": status,
            "unresolved": unresolved,
            "checks": [
                "风险必须有证据",
                "风险必须有复现条件、系统结果、外部观测、排除条件",
                "Developer-confirm 不生成最终可执行承诺",
                "测试用例必须有前置、步骤、预期、观测和清理",
            ],
        },
    }
