from __future__ import annotations

from pathlib import Path

from pangea_agent.graph.state import PangeaState
from pangea_agent.report.markdown import render_report


def finalize_report(state: PangeaState) -> PangeaState:
    run_dir = Path(state["data_root"]) / "runs" / state["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.md"
    report_path.write_text(render_report(state), encoding="utf-8")
    return {**state, "report_path": str(report_path)}
