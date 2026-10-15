"""Frozen behavior-test-case-v1 write structure; never judge or fill case content."""

from __future__ import annotations

from typing import Any

TEST_CASE_FORMAT = "behavior-test-case-v1"


def check_test_case_structure(body: Any) -> list[str]:
    """Return exact field errors for the explicitly contracted case body only.

    Extra fields and all semantic choices remain worker-owned. In particular,
    supplementary observations are not interpreted as expected step results.
    """
    if not isinstance(body, dict):
        return ["body 必须是 JSON 对象，不能是字符串、数组或代码块"]
    errors: list[str] = []
    if body.get("format_version") != TEST_CASE_FORMAT:
        errors.append(f"body.format_version 必须为 {TEST_CASE_FORMAT}")

    def check_pairs(field: str, fields: tuple[str, str], *, required: bool) -> None:
        if not required and field not in body:
            return
        values = body.get(field)
        if not isinstance(values, list) or (required and not values):
            errors.append(f"body.{field} 必须是{'非空' if required else ''}数组")
            return
        for index, item in enumerate(values):
            path = f"body.{field}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{path} 必须是包含 {fields[0]}、{fields[1]} 的 JSON 对象")
                continue
            for key in fields:
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{path}.{key} 必须是非空白字符串")

    check_pairs("steps", ("action", "expected"), required=True)
    if not isinstance(body.get("external_observations"), list):
        errors.append("body.external_observations 必须是数组，无补充观测时填写 []；逐步预期写入 steps[*].expected")
    check_pairs("variants", ("input", "expected"), required=False)
    return errors


def format_test_case_errors(errors: list[tuple[int, str]]) -> str:
    lines = [f"test_case 结构不合格，共 {len(errors)} 处，本批次未写入："]
    lines.extend(f"- record[{offset}].{error}" for offset, error in errors)
    lines.append(
        "由同一 worker 按当前 task 冻结的 behavior-test-case-v1 契约修正后，"
        "使用原 result_path 和未变化的 revision 重试；不得把 external_observations 当作逐步预期。"
    )
    return "\n".join(lines)
