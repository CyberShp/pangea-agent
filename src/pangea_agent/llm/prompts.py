from __future__ import annotations


def build_system_prompt(rubrics: list[str]) -> str:
    return "\n\n".join([
        "你是 PANGEA 测试分析 Agent。所有结论必须面向测试人员可复现、可观测、可排除。",
        *rubrics,
    ])
