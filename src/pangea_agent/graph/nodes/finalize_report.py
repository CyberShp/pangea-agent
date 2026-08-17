from __future__ import annotations

from pathlib import Path

from pangea_agent.graph.state import PangeaState
from pangea_agent.graph.run_store import load_progress, save_final_state, save_progress
from pangea_agent.report import write_reports


def finalize_report(state: PangeaState) -> PangeaState:
    run_dir = Path(state["data_root"]) / "runs" / state["run_id"]
    progress = load_progress(state)
    terminal_phase = "COMPLETE" if progress and progress.quality_status == "PASS" else "INCOMPLETE"
    final_state = {
        **state,
        "phase": terminal_phase,
        "run_status": terminal_phase,
    }
    markdown_path, html_path = write_reports(run_dir, final_state)
    if progress is not None:
        progress.phase = terminal_phase
        save_progress(state, progress)
    completed = {**final_state, "report_path": str(markdown_path), "html_report_path": str(html_path)}
    save_final_state(completed)
    return completed
