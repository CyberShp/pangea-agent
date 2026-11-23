"""Regression checks for WP2.1 (analysis-io-v1) and WP2.4 (work-finish boundary_advisories)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pangea_agent.cli.source_first_api import task_open, work_finish
from pangea_agent.graph.result_store import append_records, initialize_result
from pangea_agent.graph.workflow_store import save_progress
from pangea_agent.models.analysis import ActionState, WorkflowProgress
from pangea_agent.models.source_first import SourceBinding


class AnalysisIoAndBoundaryAdvisoriesTest(unittest.TestCase):
    def test_analysis_startup_and_non_blocking_boundary_advisories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            run_id = "run-wp2"
            run_dir = data_root / "runs" / run_id
            (run_dir / "inputs").mkdir(parents=True)
            scene = run_dir / "inputs" / "analysis-scene.json"
            scene.write_text(
                json.dumps({"scene_id": "branch-analysis", "record_contracts": {"boundary_reconciliation": "interface-boundary-reconciliation-v1"}}),
                encoding="utf-8",
            )
            handoff = run_dir / "inputs" / "planning-handoff.json"
            handoff.write_text(json.dumps({"notes": ["handoff fact"]}), encoding="utf-8")
            rubric = run_dir / "inputs" / "scene_flow.md"
            rubric.write_text("# flow rubric", encoding="utf-8")

            def setup_unit(action_id: str, task_id: str, unit_id: str, iface: dict, *, io_policy: str | None = "analysis-io-v1", risk_refs: list[str] | None = None) -> ActionState:
                adir = run_dir / "actions" / unit_id
                adir.mkdir(parents=True)
                tpath = adir / "task.json"
                rpath = adir / "result.json"
                task = {
                    "format_version": "source-first-task-v1",
                    "task_type": "source_first_analysis",
                    "analysis_profile": "behavior-test-v2",
                    "action_id": action_id,
                    "run_id": run_id,
                    "unit_id": unit_id,
                    "record_contracts": {"boundary_reconciliation": "interface-boundary-reconciliation-v1"},
                    "result_path": str(rpath),
                    "inputs": [
                        {"input_id": "analysis_scene", "path": str(scene), "label": "scene"},
                        {"input_id": "planning_handoff", "path": str(handoff), "label": "handoff"},
                        {"input_id": "rubric_scene_flow", "path": str(rubric), "label": "flow"},
                    ],
                }
                if io_policy is not None:
                    task["analysis_io_policy"] = io_policy
                tpath.write_text(json.dumps(task), encoding="utf-8")
                binding = SourceBinding(
                    data_root=str(data_root),
                    run_id=run_id,
                    action_id=action_id,
                    task_id=task_id,
                )
                initialize_result(rpath, binding)
                append_records(
                    rpath,
                    binding,
                    0,
                    [{"kind": "note", "body": {"interface_assumptions": [iface], "risk_refs": risk_refs or []}}],
                )
                return ActionState(
                    action="dispatch_agent",
                    action_id=action_id,
                    task_id=task_id,
                    role="analysis",
                    stage="unit_analysis",
                    task_path=str(tpath),
                    status="dispatched",
                )

            a1 = setup_unit(
                "act-u1", "task-u1", "u1",
                {"repo_id": "r", "path": "a.c", "symbol": "foo", "role": "consumer", "error_codes": ["E1"]},
                risk_refs=["R-MISSING"],
            )
            a2 = setup_unit(
                "act-u2", "task-u2", "u2",
                {"repo_id": "r", "path": "a.c", "symbol": "foo", "role": "producer", "error_codes": ["E1", "E2"]},
                io_policy=None,
            )
            save_progress(
                {"data_root": str(data_root), "run_id": run_id},
                WorkflowProgress(run_id=run_id, actions={"act-u1": a1, "act-u2": a2}),
            )

            # WP2.1: analysis-io-v1 task gets analysis_startup + write_contract
            opened = task_open(str(data_root), run_id, "act-u1", "task-u1")
            self.assertIn("analysis_startup", opened)
            self.assertEqual([p["input_id"] for p in opened["analysis_startup"]["pages"]], ["analysis_scene", "planning_handoff", "rubric_scene_flow"])
            self.assertTrue(opened["analysis_startup"]["complete"])
            self.assertIn("analysis-startup-v1", opened["write_contract"]["capabilities"])
            self.assertEqual(opened["write_contract"]["result_write"][0], "result-write")
            self.assertIn("--records-file", opened["write_contract"]["result_write"])
            self.assertEqual(opened["write_contract"]["revision"], 1)

            # Legacy task without analysis_io_policy stays untouched
            legacy = task_open(str(data_root), run_id, "act-u2", "task-u2")
            self.assertNotIn("analysis_startup", legacy)

            # WP2.4: work_finish returns non-blocking boundary_advisories
            finished = work_finish(str(data_root), run_id, "act-u1", "task-u1", revision=1)
            self.assertTrue(finished.get("complete"))
            advisories = finished.get("boundary_advisories", [])
            kinds = {item.get("code") or item.get("kind") for item in advisories}
            self.assertIn("reference_missing", kinds)
            self.assertIn("declared_codes_differ", kinds)
            self.assertTrue(any(w.get("kind") == "boundary_reconciliation_advisory" for w in finished.get("warnings", [])))


if __name__ == "__main__":
    unittest.main()
