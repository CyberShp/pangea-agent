"""Report assembly for source-first notes.

The renderer presents the Agent's original records and workflow facts.  It
does not turn prose into risks, tests, or a quality verdict.
"""

from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import read_json
from pangea_agent.graph.result_store import read_result, supersession_map
from pangea_agent.graph.workflow_store import run_directory


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _body_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)


def _action_records(state: dict, progress: dict) -> list[tuple[dict, Any]]:
    items: list[tuple[dict, Any]] = []
    for action_id, action in progress.get("actions", {}).items():
        task_path = action.get("task_path") if isinstance(action, dict) else None
        if not isinstance(task_path, str):
            raise ValueError(f"source-first action 缺少 task_path：{action_id}")
        try:
            task = read_json(Path(task_path))
            result_path = task.get("result_path")
            if not isinstance(result_path, str) or not Path(result_path).is_file():
                raise ValueError(f"source-first action 缺少可读取 result：{action_id}")
            items.append((
                {"action_id": action_id, **action, "task": task},
                read_result(Path(result_path)),
            ))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"source-first action 产物不可读取：{action_id}: {exc}") from exc
    return items


def _markdown(state: dict, progress: dict, records: list[tuple[dict, Any]]) -> str:
    lines = [
        "# PANGEA Source-First Report",
        "",
        f"- Run: `{progress.get('run_id', state.get('run_id', ''))}`",
        f"- Workflow: `{progress.get('workflow_version') or 'source-first-v1'}`",
        f"- Lifecycle: `{progress.get('lifecycle_status', 'running')}`",
        f"- Stage: `{progress.get('stage', 'preparing')}`",
        f"- Quality: `{progress.get('quality_status') or 'UNRESOLVED'}`",
        f"- Needs user: `{bool(progress.get('needs_user', False))}`",
        "",
        "## Revision ledger",
        "",
    ]
    first = progress.get("first_finish_revisions", {})
    accepted = progress.get("accepted_revisions", {})
    if first or accepted:
        for action_id in sorted(set(first) | set(accepted)):
            lines.append(
                f"- `{action_id}`: first finish `{first.get(action_id, 'pending')}`, "
                f"accepted `{accepted.get(action_id, 'pending')}`"
            )
    else:
        lines.append("- No accepted Agent revision recorded yet.")
    comparison_sets = [
        item[0].get("task", {}).get("version_set_id")
        for item in records
        if item[0].get("stage") == "comparison_review"
        and item[0].get("task", {}).get("version_set_id")
    ]
    if comparison_sets:
        lines.extend(["", "## Review binding", ""])
        lines.extend(f"- Comparison version set: `{value}`" for value in comparison_sets)
    lines.extend(["", "## Agent records", ""])
    for action, result in records:
        lines.extend([
            f"### `{action['action_id']}` ({action.get('stage', 'unknown')})",
            "",
            f"- Result revision: `{result.revision}`",
            f"- Completion: `{result.completion.complete if result.completion else 'not declared'}`",
            "",
        ])
        if action.get("stage") == "targeted_closure":
            lines.extend([
                "> This correction was written by the original worker after comparison and has not received an additional independent review.",
                "",
            ])
        superseded_by = supersession_map(result)
        active = [
            record for record in result.records
            if record.record_id not in superseded_by
        ]
        retired = [
            record for record in result.records
            if record.record_id in superseded_by
        ]
        lines.extend([
            f"- Active records: `{len(active)}`",
            f"- Superseded records: `{len(retired)}`",
            "",
            "#### Effective records",
            "",
        ])
        for record in active:
            lines.extend([
                f"##### `{record.record_id}` · `{record.kind}`",
                "",
                _body_text(record.body),
                "",
            ])
            if record.evidence:
                lines.extend(["Evidence:", "", _body_text(record.evidence), ""])
            if record.relates_to:
                lines.extend(["Relates to:", "", _body_text(record.relates_to), ""])
            if record.supersedes:
                lines.extend(["Supersedes:", "", _body_text(record.supersedes), ""])
        if retired:
            lines.extend(["#### Superseded records (audit only)", ""])
        for record in retired:
            lines.extend([
                f"##### `{record.record_id}` · `{record.kind}` · superseded",
                "",
                f"Superseded by: `{', '.join(superseded_by[record.record_id])}`",
                "",
                _body_text(record.body),
                "",
            ])
            if record.evidence:
                lines.extend(["Evidence:", "", _body_text(record.evidence), ""])
            if record.relates_to:
                lines.extend(["Relates to:", "", _body_text(record.relates_to), ""])
    degradations = progress.get("degradations", [])
    blocking_reason = progress.get("blocking_reason")
    if blocking_reason:
        lines.extend(["## Attention", "", _body_text(blocking_reason), ""])
    if degradations:
        lines.extend(["## Deterministic diagnostics", ""])
        lines.extend(f"- {item}" for item in degradations)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _behavior_markdown(state: dict, progress: dict, records: list[tuple[dict, Any]]) -> str:
    lines = [
        "# PANGEA 业务行为测试用例报告",
        "",
        f"- Run: `{progress.get('run_id', state.get('run_id', ''))}`",
        "- Analysis profile: `behavior-test-v1`",
        f"- Lifecycle: `{progress.get('lifecycle_status', 'running')}`",
        f"- Quality: `{progress.get('quality_status') or 'UNRESOLVED'}`",
        "- 质量范围：业务行为用例的生成与审核；未附实际执行证据时，不代表被测产品或 Coverage 已通过。",
        "",
    ]
    accepted_closure_units = {
        str(action.get("task", {}).get("unit_id"))
        for action, _result in records
        if action.get("stage") == "targeted_closure" and action.get("status") == "accepted"
    }
    delivery: list[tuple[dict, Any]] = []
    review: list[tuple[dict, Any]] = []
    audit: list[tuple[dict, Any]] = []
    for action, result in records:
        stage = action.get("stage")
        unit_id = str(action.get("task", {}).get("unit_id"))
        if stage == "targeted_closure" and action.get("status") == "accepted":
            delivery.append((action, result))
        elif stage == "unit_analysis" and unit_id not in accepted_closure_units:
            delivery.append((action, result))
        elif stage in {"unit_planning", "independent_review", "comparison_review"}:
            review.append((action, result))
        else:
            audit.append((action, result))

    def active_and_retired(result: Any) -> tuple[list[Any], list[Any], dict[str, list[str]]]:
        retired_by = supersession_map(result)
        active = [record for record in result.records if record.record_id not in retired_by]
        retired = [record for record in result.records if record.record_id in retired_by]
        return active, retired, retired_by

    def add_records(title: str, selected: list[tuple[dict, Any]], kinds: set[str] | None) -> None:
        lines.extend([f"## {title}", ""])
        found = False
        for action, result in selected:
            active, _retired, _retired_by = active_and_retired(result)
            chosen = [record for record in active if kinds is None or record.kind in kinds]
            for record in chosen:
                found = True
                lines.extend([
                    f"### `{action['action_id']}` · `{record.record_id}` · `{record.kind}`",
                    "",
                    _body_text(record.body),
                    "",
                ])
                if record.evidence:
                    lines.extend(["证据：", "", _body_text(record.evidence), ""])
                if record.relates_to:
                    lines.extend(["关联：", "", _body_text(record.relates_to), ""])
        if not found:
            lines.extend(["- 当前没有此类有效记录。", ""])

    add_records("当前有效测试用例", delivery, {"test_case", "test_case_group"})
    add_records(
        "业务流程与 Coverage 说明",
        delivery,
        {"flow", "branch", "scenario", "evidence", "blackbox_translation"},
    )
    add_records("待确认事项与已确认问题", delivery, {"unresolved", "risk"})
    add_records("其他交付说明", delivery, {"summary", "note"})
    add_records("Planning 与 Reviewer 记录", review, None)

    lines.extend(["## 修正和审计历史", ""])
    audit_found = False
    audit_action_ids = {action["action_id"] for action, _result in audit}
    for action, result in records:
        active, retired, retired_by = active_and_retired(result)
        historical = active if action["action_id"] in audit_action_ids else []
        for record in [*historical, *retired]:
            audit_found = True
            suffix = "superseded" if record in retired else "earlier accepted analysis"
            lines.extend([
                f"### `{action['action_id']}` · `{record.record_id}` · {suffix}",
                "",
            ])
            if record in retired:
                lines.extend([
                    f"Superseded by: `{', '.join(retired_by[record.record_id])}`",
                    "",
                ])
            lines.extend([_body_text(record.body), ""])
    if not audit_found:
        lines.extend(["- 当前没有修正历史。", ""])

    first = progress.get("first_finish_revisions", {})
    accepted = progress.get("accepted_revisions", {})
    lines.extend(["## Revision ledger", ""])
    for action_id in sorted(set(first) | set(accepted)):
        lines.append(
            f"- `{action_id}`: first finish `{first.get(action_id, 'pending')}`, "
            f"accepted `{accepted.get(action_id, 'pending')}`"
        )
    if not first and not accepted:
        lines.append("- No accepted Agent revision recorded yet.")
    lines.append("")
    if progress.get("blocking_reason"):
        lines.extend(["## Attention", "", _body_text(progress["blocking_reason"]), ""])
    if progress.get("degradations"):
        lines.extend(["## Deterministic diagnostics", ""])
        lines.extend(f"- {item}" for item in progress["degradations"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _html(markdown: str) -> str:
    # The Markdown remains the canonical text artifact.  This small renderer
    # keeps the desktop display dependency-free and escapes all Agent content.
    chunks = []
    for line in markdown.splitlines():
        escaped = html.escape(line)
        if line.startswith("# "):
            chunks.append(f"<h1>{escaped[2:]}</h1>")
        elif line.startswith("## "):
            chunks.append(f"<h2>{escaped[3:]}</h2>")
        elif line.startswith("### "):
            chunks.append(f"<h3>{escaped[4:]}</h3>")
        elif line.startswith("##### "):
            chunks.append(f"<h5>{escaped[6:]}</h5>")
        elif line.startswith("#### "):
            chunks.append(f"<h4>{escaped[5:]}</h4>")
        elif line.startswith("- "):
            chunks.append(f"<p class=\"item\">{escaped}</p>")
        elif not line:
            chunks.append("<div class=\"gap\"></div>")
        else:
            chunks.append(f"<pre>{escaped}</pre>")
    return "<!doctype html><meta charset=\"utf-8\"><title>PANGEA Source-First Report</title><style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 24px;line-height:1.5}pre{white-space:pre-wrap;background:#f6f7f9;padding:12px;border-radius:6px}.item{margin:4px 0}.gap{height:8px}</style>" + "".join(chunks)


def write_source_first_reports(state: dict, *, progress: dict | None = None) -> dict[str, str]:
    if progress is None:
        progress = read_json(run_directory(state) / "progress.json")
    records = _action_records(state, progress)
    markdown_path = run_directory(state) / "report.md"
    html_path = run_directory(state) / "report.html"
    contract = state.get("task_contract", {})
    if not isinstance(contract, dict):
        contract = {}
    profile = contract.get("analysis_profile")
    if profile is None:
        contract_path = run_directory(state) / "inputs" / "task-contract.json"
        if contract_path.is_file():
            frozen_contract = read_json(contract_path)
            if isinstance(frozen_contract, dict):
                profile = frozen_contract.get("analysis_profile")
    markdown = (
        _behavior_markdown(state, progress, records)
        if profile == "behavior-test-v1"
        else _markdown(state, progress, records)
    )
    _atomic_text(markdown_path, markdown)
    _atomic_text(html_path, _html(markdown))
    _atomic_text(
        run_directory(state) / "report-complete.json",
        json.dumps(
            {
                "format_version": "pangea-report-complete-v1",
                "run_id": progress.get("run_id", state.get("run_id")),
                "lifecycle_status": progress.get("lifecycle_status"),
                "quality_status": progress.get("quality_status"),
                "analysis_profile": profile,
                "first_finish_revisions": progress.get("first_finish_revisions", {}),
                "accepted_revisions": progress.get("accepted_revisions", {}),
                "files": ["report.md", "report.html"],
            },
            ensure_ascii=False,
        ) + "\n",
    )
    return {"report_path": str(markdown_path), "html_report_path": str(html_path)}
