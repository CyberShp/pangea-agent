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
from pangea_agent.graph.result_store import read_result
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
            continue
        try:
            task = read_json(Path(task_path))
            result_path = task.get("result_path")
            if not isinstance(result_path, str) or not Path(result_path).is_file():
                continue
            items.append((
                {"action_id": action_id, **action, "task": task},
                read_result(Path(result_path)),
            ))
        except (OSError, ValueError, TypeError):
            continue
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
    lines.extend(["", "## Agent records", ""])
    for action, result in records:
        lines.extend([
            f"### `{action['action_id']}` ({action.get('stage', 'unknown')})",
            "",
            f"- Result revision: `{result.revision}`",
            f"- Completion: `{result.completion.complete if result.completion else 'not declared'}`",
            "",
        ])
        for record in result.records:
            lines.extend([
                f"#### `{record.record_id}` · `{record.kind}`",
                "",
                _body_text(record.body),
                "",
            ])
    degradations = progress.get("degradations", [])
    blocking_reason = progress.get("blocking_reason")
    if blocking_reason:
        lines.extend(["## Attention", "", _body_text(blocking_reason), ""])
    if degradations:
        lines.extend(["## Deterministic diagnostics", ""])
        lines.extend(f"- {item}" for item in degradations)
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
        elif line.startswith("#### "):
            chunks.append(f"<h4>{escaped[5:]}</h4>")
        elif line.startswith("- "):
            chunks.append(f"<p class=\"item\">{escaped}</p>")
        elif not line:
            chunks.append("<div class=\"gap\"></div>")
        else:
            chunks.append(f"<pre>{escaped}</pre>")
    return "<!doctype html><meta charset=\"utf-8\"><title>PANGEA Source-First Report</title><style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 24px;line-height:1.5}pre{white-space:pre-wrap;background:#f6f7f9;padding:12px;border-radius:6px}.item{margin:4px 0}.gap{height:8px}</style>" + "".join(chunks)


def write_source_first_reports(state: dict) -> dict[str, str]:
    progress = read_json(run_directory(state) / "progress.json")
    records = _action_records(state, progress)
    markdown_path = run_directory(state) / "report.md"
    html_path = run_directory(state) / "report.html"
    markdown = _markdown(state, progress, records)
    _atomic_text(markdown_path, markdown)
    _atomic_text(html_path, _html(markdown))
    _atomic_text(
        run_directory(state) / "report-complete.json",
        json.dumps(
            {"files": ["report.md", "report.html"]},
            ensure_ascii=False,
        ) + "\n",
    )
    return {"report_path": str(markdown_path), "html_report_path": str(html_path)}
