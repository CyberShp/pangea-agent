from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from pangea_agent.agent_io import write_json
from pangea_agent.models.analysis import (
    StrictModel,
    ValidationErrorGroup,
    ValidationErrorSample,
)
from pangea_agent.graph.result_contract import ResultContractIssue


MAX_INLINE_DIGEST_BYTES = 12 * 1024
MAX_GROUP_SAMPLE_PATHS = 2
_INDEX = re.compile(r"^\d+$")


class ValidationDiagnostic(StrictModel):
    total_error_count: int
    group_count: int
    family_fingerprint: str
    groups: list[ValidationErrorGroup]
    groups_truncated: bool = False
    representative_details: list[ValidationErrorSample] = Field(default_factory=list)
    full_report_path: str = ""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _display_path(loc: tuple[Any, ...]) -> str:
    path = "$"
    for part in loc:
        if isinstance(part, int) or _INDEX.match(str(part)):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def _path_pattern(loc: tuple[Any, ...], error_type: str) -> str:
    path = "$"
    for index, part in enumerate(loc):
        if isinstance(part, int) or _INDEX.match(str(part)):
            path += "[*]"
        elif error_type == "extra_forbidden" and index == len(loc) - 1:
            path += ".<extra>"
        else:
            path += f".{part}"
    return path


def _expected(ctx: dict[str, Any]) -> str | list[str] | None:
    expected = ctx.get("expected")
    if expected is None:
        expected = ctx.get("allowed")
    if expected is None:
        return None
    if isinstance(expected, (str, int, float, bool)):
        return str(expected)
    if isinstance(expected, list) and all(isinstance(item, str) for item in expected):
        return expected
    return str(expected)


def _group_error_key(error_type: str, path_pattern: str) -> str:
    return f"{error_type}|{path_pattern}"


def _all_details(exc: ValidationError) -> list[ValidationErrorSample]:
    details: list[ValidationErrorSample] = []
    for error in exc.errors(include_url=False):
        loc = tuple(error.get("loc", ()))
        error_type = str(error.get("type", "validation_error"))
        details.append(
            ValidationErrorSample(
                path=_display_path(loc),
                error_type=error_type,
                message=str(error.get("msg", "validation error")),
                context=_json_safe(error.get("ctx") or {}),
            )
        )
    return details


def diagnostics_from_validation_error(
    exc: ValidationError,
    *,
    full_report_path: str = "",
) -> tuple[ValidationDiagnostic, list[ValidationErrorSample]]:
    details = _all_details(exc)
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for error, detail in zip(exc.errors(include_url=False), details):
        loc = tuple(error.get("loc", ()))
        error_type = detail.error_type
        pattern = _path_pattern(loc, error_type)
        group_key = _group_error_key(error_type, pattern)
        group = groups.setdefault(
            group_key,
            {
                "group_key": group_key,
                "path_pattern": pattern,
                "error_type": error_type,
                "count": 0,
                "sample_paths": [],
                "expected": _expected(detail.context),
                "unexpected_fields": [],
            },
        )
        group["count"] += 1
        if detail.path not in group["sample_paths"] and len(group["sample_paths"]) < MAX_GROUP_SAMPLE_PATHS:
            group["sample_paths"].append(detail.path)
        if error_type == "extra_forbidden" and loc:
            field = str(loc[-1])
            if field not in group["unexpected_fields"]:
                group["unexpected_fields"].append(field)

    group_models = [ValidationErrorGroup.model_validate(group) for group in groups.values()]
    fingerprint = hashlib.sha256(
        "\n".join(sorted(group.group_key for group in group_models)).encode("utf-8")
    ).hexdigest()
    diagnostic = ValidationDiagnostic(
        total_error_count=len(details),
        group_count=len(group_models),
        family_fingerprint=fingerprint,
        groups=group_models,
        representative_details=details[: min(len(details), 24)],
        full_report_path=full_report_path,
    )
    return diagnostic, details


def diagnostics_from_contract_issues(
    issues: list[ResultContractIssue] | tuple[ResultContractIssue, ...],
    *,
    full_report_path: str = "",
) -> tuple[ValidationDiagnostic, list[ValidationErrorSample]]:
    """Build the same complete diagnostic shape for structural contract errors."""

    details = [
        ValidationErrorSample(
            path=issue.path,
            error_type=issue.family,
            message=issue.message,
            context=_json_safe(issue.context),
        )
        for issue in issues
    ]
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for detail in details:
        group_key = _group_error_key(detail.error_type, detail.path)
        group = groups.setdefault(
            group_key,
            {
                "group_key": group_key,
                "path_pattern": detail.path,
                "error_type": detail.error_type,
                "count": 0,
                "sample_paths": [],
                "expected": None,
                "unexpected_fields": [],
            },
        )
        group["count"] += 1
        if detail.path not in group["sample_paths"] and len(group["sample_paths"]) < MAX_GROUP_SAMPLE_PATHS:
            group["sample_paths"].append(detail.path)
    group_models = [ValidationErrorGroup.model_validate(group) for group in groups.values()]
    fingerprint = hashlib.sha256(
        "\n".join(sorted(group.group_key for group in group_models)).encode("utf-8")
    ).hexdigest()
    diagnostic = ValidationDiagnostic(
        total_error_count=len(details),
        group_count=len(group_models),
        family_fingerprint=fingerprint,
        groups=group_models,
        representative_details=details[: min(len(details), 24)],
        full_report_path=full_report_path,
    )
    return diagnostic, details


def compact_diagnostic(diagnostic: ValidationDiagnostic) -> dict[str, Any]:
    """Return an inline digest bounded independently from the full report."""
    groups = [group.model_copy(deep=True) for group in diagnostic.groups]
    truncated = False

    def payload(current: list[ValidationErrorGroup], is_truncated: bool) -> dict[str, Any]:
        return {
            "code": "ValidationError",
            "message": (
                f"{diagnostic.total_error_count} schema errors in "
                f"{diagnostic.group_count} error groups"
            ),
            "detail_count": diagnostic.total_error_count,
            "group_count": diagnostic.group_count,
            "family_fingerprint": diagnostic.family_fingerprint,
            "groups": [item.model_dump(mode="json") for item in current],
            "groups_truncated": is_truncated,
            "full_report_path": diagnostic.full_report_path,
            "repair_rule": (
                "Each group applies to every matching object. "
                "Sample paths are not exhaustive."
            ),
        }

    while groups and len(json.dumps(payload(groups, truncated), ensure_ascii=False).encode("utf-8")) > MAX_INLINE_DIGEST_BYTES:
        truncated = True
        if any(len(group.sample_paths) > 1 for group in groups):
            largest = max(groups, key=lambda item: len(item.sample_paths))
            largest.sample_paths = largest.sample_paths[:1]
            continue
        if any(group.sample_paths for group in groups):
            largest = max(groups, key=lambda item: len(item.sample_paths))
            largest.sample_paths = []
            continue
        groups.pop()
    if len(json.dumps(payload(groups, truncated), ensure_ascii=False).encode("utf-8")) > MAX_INLINE_DIGEST_BYTES:
        groups = []
        truncated = True
    return payload(groups, truncated)


def validation_report(
    diagnostic: ValidationDiagnostic,
    details: list[ValidationErrorSample],
    *,
    action_id: str,
    task_id: str,
    attempt: int,
    task_path: str,
    result_path: str,
    result_sha256: str | None,
    contract_manifest_path: str | None,
    contract_card_sha256: str | None,
    validation_kind: str = "schema_validation",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action_id": action_id,
        "task_id": task_id,
        "attempt": attempt,
        "task_path": task_path,
        "result_path": result_path,
        "result_sha256": result_sha256,
        "contract_manifest_path": contract_manifest_path,
        "contract_card_sha256": contract_card_sha256,
        "validation_kind": validation_kind,
        "total_error_count": diagnostic.total_error_count,
        "group_count": diagnostic.group_count,
        "family_fingerprint": diagnostic.family_fingerprint,
        "groups": [group.model_dump(mode="json") for group in diagnostic.groups],
        "details": [detail.model_dump(mode="json") for detail in details],
    }


def write_validation_report(path: str | Path, report: dict[str, Any]) -> None:
    write_json(Path(path), report)
