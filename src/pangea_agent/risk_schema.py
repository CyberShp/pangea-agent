"""behavior-risk-v1 写入格式契约；不判断风险真实性，也不修改记录。"""

from __future__ import annotations

import re
from typing import Any

RISK_FORMAT = "behavior-risk-v1"
RISK_FORMAT_V2 = "behavior-risk-v2"
REQUIRED_FIELDS = (
    "risk_id", "title", "trigger", "system_result",
    "external_observation", "exclusion_condition", "source_evidence",
)
REFERENCE_FIELDS = ("linked_test_case_ids",)
_SOURCE_REFERENCE = re.compile(r"^[^:\s]+:[^\r\n]+:[1-9]\d*(?:-[1-9]\d*)?$")
_FORBIDDEN = {
    "risk": "直接写风险对象，不要外套 risk",
    "body": "把正文分别写入 trigger、system_result、external_observation、exclusion_condition",
    "observed": "external_observation",
    "result": "system_result",
    "test_case_refs": "linked_test_case_ids",
    "flow_refs": "将流程关联写入相关测试用例的 flow_refs；风险通过 linked_test_case_ids 关联用例",
}
_SOURCE_CHANGE_FIELDS = ("remediation_hint", "fix", "patch", "suggested_patch")


def check_risk_structure(body: Any, version: str = RISK_FORMAT) -> list[str]:
    """只检查字段、类型及引用形态，空列表表示结构合格。"""
    if not isinstance(body, dict):
        return ["body 必须是 JSON 对象，不能是字符串、数组或代码块"]
    errors: list[str] = []
    if body.get("format_version") != version:
        errors.append(f"format_version 缺失或不是 {version}")
    if version == RISK_FORMAT_V2:
        quotes = body.get("evidence_quotes")
        if not isinstance(quotes, list) or not quotes:
            errors.append("evidence_quotes 必须是非空数组")
        else:
            for i, quote in enumerate(quotes):
                if not isinstance(quote, dict) or any(not isinstance(quote.get(k), str) or not quote[k].strip() for k in ("ref", "snippet")):
                    errors.append(f"evidence_quotes[{i}] 必须含非空字符串 ref、snippet")
    for field in REQUIRED_FIELDS:
        if field not in body:
            errors.append(f"缺少必填字段 {field}")
        elif field != "source_evidence" and (
            not isinstance(body[field], str) or not body[field].strip()
        ):
            errors.append(f"{field} 必须是非空字符串")
    if "source_evidence" in body:
        evidence = body["source_evidence"]
        if not isinstance(evidence, list) or not evidence:
            errors.append("source_evidence 必须是非空数组")
        else:
            for index, reference in enumerate(evidence):
                if not isinstance(reference, str) or not _SOURCE_REFERENCE.fullmatch(reference):
                    errors.append(
                        f"source_evidence[{index}] 必须使用 repo_id:path:line 或 repo_id:path:line-line；"
                        "执行记录等其他证据请保留在独立 evidence/note 记录中"
                    )
    for field in REFERENCE_FIELDS:
        if field in body and not isinstance(body[field], list):
            errors.append(f"{field} 必须是数组，尚无关联时可省略或填写 []")
    for field, hint in _FORBIDDEN.items():
        if field in body:
            errors.append(f"检测到顶层键 {field}，请改为：{hint}")
    for field in _SOURCE_CHANGE_FIELDS:
        if field in body:
            errors.append(
                f"检测到顶层键 {field}：本 Run 只交付证据，不交付源码修改建议；"
                "请把该内容移出正式风险正文。"
            )
    return errors


def format_risk_errors(offset: int, errors: list[str], version: str = RISK_FORMAT) -> str:
    lines = [f"record[{offset}] risk 结构不合格，共 {len(errors)} 处，请修正后在本轮重写："]
    lines.extend(f"- {error}" for error in errors)
    lines.append("请参照当前 task 冻结的 scene_risk.md 中 RISK-CONTRACT-V2 标准样例。" if version == RISK_FORMAT_V2 else "请参照当前 task 冻结的 scene_common_generation.md 中 RISK-CONTRACT 标准样例。")
    return "\n".join(lines)
