from __future__ import annotations

from pathlib import Path

from pangea_agent.graph.state import PangeaState
from pangea_agent.index.retriever import search_evidence


def analyze_unit(state: PangeaState) -> PangeaState:
    """Analyze each unit with retrieved evidence.

    Skeleton version emits Developer-confirm placeholder risks. Real LLM analysis
    should use rubrics and retrieved chunks, then write structured risk objects.
    """

    risks = []
    index_path = Path(state["index_path"])
    for unit in state.get("analysis_units", []):
        chunks = search_evidence(index_path, query=unit["title"], top_k=5)
        risks.append({
            "risk_id": f"R-{unit['unit_id']}-001",
            "title": f"{unit['title']} 待完成风险分析",
            "dfx": unit.get("dfx", ["功能与状态"]),
            "severity": "Medium",
            "confidence": "low",
            "trigger": "待根据源码和材料补充复现条件",
            "system_result": "待分析系统结果",
            "external_observation": "待补充测试侧观测",
            "exclusion_condition": "待补充排除条件",
            "translation_status": "Developer-confirm",
            "evidence": [
                {"chunk_id": c["chunk_id"], "location": c["location"], "observation": "检索命中证据片段"}
                for c in chunks
            ],
        })
    return {**state, "risks": risks}
