from __future__ import annotations

from pangea_agent.graph.state import PangeaState


def render_report(state: PangeaState) -> str:
    lines = [f"# PANGEA Agent Report", "", f"Run ID: `{state['run_id']}`", ""]
    lines.append("## 风险")
    for risk in state.get("risks", []):
        lines.append(f"- **{risk.get('risk_id')}** {risk.get('title')} [{risk.get('translation_status')}]")
    lines.append("")
    lines.append("## 测试用例")
    for case in state.get("test_cases", []):
        lines.append(f"- **{case.get('test_case_id')}** {case.get('title')}")
    lines.append("")
    lines.append("## 质量门禁")
    lines.append(f"状态：`{state.get('quality_report', {}).get('status', 'UNKNOWN')}`")
    return "\n".join(lines) + "\n"
