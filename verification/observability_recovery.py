"""Real Graph fault/retry checks: python verification/observability_recovery.py."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.public_api import deliver_current, execution_event, resume_run, run_detail, stop_run
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.cli.source_first_api import comparison_finding_write, plan_write, result_read, result_write, review_decide, work_finish
from pangea_agent.execution_view import execution_view
from pangea_agent.graph.result_store import read_result


class RecoveryChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pangea-recovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repo = self.root / "repositories/demo"
        repo.mkdir(parents=True)
        for name in ("one", "two"):
            (repo / f"{name}.c").write_text(f"int {name}(void) {{ return 1; }}\n")
        contract = self.root / "request.json"
        write_json(contract, {"data_root": str(self.root), "repository": "demo", "target": "recovery check",
                              "source_scope": ["."], "analysis_profile": "behavior-test-v1",
                              "analysis_settings": {"scenario": "module-analysis", "mode": "speed"}})
        created = run_module_analysis(str(contract))
        self.run_id = created["run_id"]
        self.run = self.root / "runs" / self.run_id
        planning = created["agent_actions"][0]["action_id"]
        bind_action(str(self.root), self.run_id, planning, "planner")
        revision = 0
        for name in ("one", "two"):
            output = plan_write(str(self.root), self.run_id, planning, "planner", expected_revision=revision,
                                unit={"title": name, "purpose": "fault regression fixture",
                                      "owned_files": [{"repo_id": "demo", "path": f"{name}.c"}]})
            revision = output["revision"]
        work_finish(str(self.root), self.run_id, planning, "planner", revision=revision)
        settle_action(str(self.root), self.run_id, planning)
        self.analysis = [a["action_id"] for a in self.progress()["actions"].values() if a["role"] == "analysis"]

    def progress(self):
        return read_json(self.run / "progress.json")

    def task(self, action_id):
        return read_json(Path(self.progress()["actions"][action_id]["task_path"]))

    def bind(self, action_id, task_id=None):
        worker = task_id or f"worker-{action_id}"
        bind_action(str(self.root), self.run_id, action_id, worker)
        return worker

    def write(self, action_id, worker, *, finish=True, settle=True):
        result = read_result(self.task(action_id)["result_path"])
        output = result_write(str(self.root), self.run_id, action_id, worker, expected_revision=result.revision,
                              records=[{"kind": "note", "body": "Saved worker evidence; semantic review remains independent."}])
        if finish:
            work_finish(str(self.root), self.run_id, action_id, worker, revision=output["revision"])
        if settle:
            settle_action(str(self.root), self.run_id, action_id)

    def closure(self):
        for action in self.analysis:
            self.write(action, self.bind(action))
        review = next(a["action_id"] for a in self.progress()["actions"].values() if a["role"] == "review")
        worker = self.bind(review, "reviewer")
        finding = comparison_finding_write(str(self.root), self.run_id, review, worker, expected_revision=0,
                                          unit_ids=["unit-0001"], finding={"summary": "Need correction", "body": "Keep unresolved until worker completes."})
        decision = review_decide(str(self.root), self.run_id, review, worker, expected_revision=finding["revision"],
                                 decision={"disposition": "finding", "version_set_id": self.task(review)["version_set_id"],
                                           "correction_record_ids": finding["record_ids"]})
        work_finish(str(self.root), self.run_id, review, worker, revision=decision["revision"])
        settle_action(str(self.root), self.run_id, review)
        closure = next(a for a in self.progress()["actions"].values() if a["role"] == "closure")
        self.bind(closure["action_id"], closure["task_id"])
        return closure["action_id"], closure["task_id"]

    def test_plan_counts_saved_progress_and_polling_are_facts(self):
        detail = run_detail(str(self.root), self.run_id)
        self.assertEqual(detail["unit_count"], 2)
        first = self.analysis[0]
        self.write(first, self.bind(first))
        detail = run_detail(str(self.root), self.run_id)
        self.assertEqual(detail["completed_unit_count"], 1)
        self.assertEqual(detail["execution_view"]["unit_counts"]["completed"], 1)
        before = detail["execution_view"]["last_effective_progress"]
        after = execution_view(self.run, self.progress(), now_ms=9999999999999)["last_effective_progress"]
        self.assertEqual(before, after)
        self.assertEqual(before["kind"], "records_saved")
        self.assertIsNone(detail["quality_status"])

    def test_stop_preserves_settled_and_blocks_every_result_write(self):
        first = self.analysis[0]
        worker = self.bind(first)
        self.write(first, worker)
        revision = read_result(self.task(first)["result_path"]).revision
        stop_run(str(self.root), self.run_id)
        self.assertEqual(self.progress()["actions"][first]["status"], "settled")
        for operation in (
            lambda: result_write(str(self.root), self.run_id, first, worker, expected_revision=revision, records=[{"body": "late"}]),
            lambda: work_finish(str(self.root), self.run_id, first, worker, revision=revision),
        ):
            with self.assertRaisesRegex(ValueError, "停止|冻结交付"):
                operation()
        resumed = resume_run(str(self.root), self.run_id, host_quiescent=True)
        self.assertEqual([a["action_id"] for a in resumed["agent_actions"]], [self.analysis[1]])
        self.assertEqual(read_result(self.task(first)["result_path"]).revision, revision)
        with self.assertRaisesRegex(ValueError, "已提交"):
            result_write(str(self.root), self.run_id, first, worker, expected_revision=revision, records=[{"body": "late old turn"}])

    def test_recovery_settles_saved_completion_and_never_requeues_live_binding(self):
        first, second = self.analysis
        self.write(first, self.bind(first), settle=False)
        second_worker = self.bind(second)
        result = resume_run(str(self.root), self.run_id)
        self.assertTrue(result["requires_host_quiescence"])
        self.assertTrue(all(self.progress()["actions"][a]["status"] == "dispatched" for a in self.analysis))
        resumed = resume_run(str(self.root), self.run_id, host_quiescent=True)
        self.assertEqual(self.progress()["actions"][first]["status"], "settled")
        self.assertEqual(self.progress()["actions"][second]["task_id"], second_worker)
        self.assertEqual([a["action_id"] for a in resumed["agent_actions"]], [second])
        before = self.progress()
        resume_run(str(self.root), self.run_id)
        self.assertEqual(before, self.progress())

    def test_duplicate_and_stale_execution_events_do_not_advance_turns(self):
        action = self.analysis[0]
        worker = self.bind(action)
        with patch("time.time", return_value=10):
            execution_event(str(self.root), self.run_id, action, worker, "started", execution_id="turn-1", budget_ms=900000)
        with patch("time.time", return_value=12):
            duplicate = execution_event(str(self.root), self.run_id, action, worker, "started", execution_id="turn-1")
            self.assertTrue(duplicate["event_ignored"])
            execution_event(str(self.root), self.run_id, action, worker, "finished", execution_id="turn-1")
        with patch("time.time", return_value=13):
            execution_event(str(self.root), self.run_id, action, worker, "started", execution_id="turn-2")
            stale = execution_event(str(self.root), self.run_id, action, worker, "paused", execution_id="turn-1")
            self.assertTrue(stale["event_ignored"])
            old_start = execution_event(str(self.root), self.run_id, action, worker, "started", execution_id="turn-1")
            self.assertTrue(old_start["event_ignored"])
        with patch("time.time", return_value=14):
            resume_run(str(self.root), self.run_id, host_quiescent=True)
        saved = self.progress()["actions"][action]
        self.assertEqual(saved["worker_turns"], 2)
        self.assertEqual(saved["execution_elapsed_ms"], 3000)
        self.assertEqual(saved["execution_finished_at_ms"], 14000)

    def test_stop_flag_does_not_prove_provider_cancellation(self):
        action = self.analysis[0]
        self.bind(action)
        stop_run(str(self.root), self.run_id)
        before = (self.run / "progress.json").read_bytes()
        response = resume_run(str(self.root), self.run_id)
        self.assertTrue(response["requires_host_quiescence"])
        self.assertEqual(response["agent_actions"], [])
        self.assertEqual(before, (self.run / "progress.json").read_bytes())
        view = run_detail(str(self.root), self.run_id)["execution_view"]
        self.assertTrue(view["recovery"]["requires_host_quiescence"])
        resumed = resume_run(str(self.root), self.run_id, host_quiescent=True)
        self.assertEqual(resumed["lifecycle_status"], "running")
        self.assertEqual(self.progress()["actions"][action]["task_id"], f"worker-{action}")

    def test_partial_delivery_crash_recovers_report_without_restarting_workers(self):
        action, worker = self.closure()
        execution_event(str(self.root), self.run_id, action, worker, "started", budget_ms=900000, execution_id="closure-turn")
        self.write(action, worker, finish=False, settle=False)
        execution_event(str(self.root), self.run_id, action, worker, "paused", reason="Host execution window exhausted", execution_id="closure-turn")
        paused = run_detail(str(self.root), self.run_id)["execution_view"]
        self.assertEqual(paused["unit_counts"]["paused"], 1)
        self.assertEqual(paused["unit_counts"]["completed"], 1)
        self.assertTrue(any(p["acceptance"] == "accepted" for p in paused["preserved"]))
        self.assertTrue(paused["unresolved"])
        stop_run(str(self.root), self.run_id)
        from pangea_agent.report.source_first import write_source_first_reports
        def fail_after_report(*args, **kwargs):
            write_source_first_reports(*args, **kwargs)
            raise OSError("simulated crash after report marker")
        with patch("pangea_agent.report.source_first.write_source_first_reports", side_effect=fail_after_report):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                deliver_current(str(self.root), self.run_id)
        checkpoint = self.progress()
        self.assertEqual(checkpoint["stage"], "reporting")
        self.assertTrue(checkpoint["partial_delivery"])
        self.assertEqual(checkpoint["quality_status"], "UNRESOLVED")
        action_ids = set(checkpoint["actions"])
        resumed = resume_run(str(self.root), self.run_id, host_quiescent=True)
        self.assertEqual(resumed["lifecycle_status"], "complete")
        self.assertEqual(action_ids, set(self.progress()["actions"]))
        saved = (self.run / "progress.json").read_bytes()
        repeated = deliver_current(str(self.root), self.run_id)
        self.assertEqual(repeated["report_path"], resumed["report_path"])
        self.assertEqual(saved, (self.run / "progress.json").read_bytes())

    def test_unreadable_result_is_diagnostic_and_missing_time_stays_unknown(self):
        action = self.analysis[0]
        task = self.task(action)
        Path(task["result_path"]).write_text("invalid result")
        view = run_detail(str(self.root), self.run_id)["execution_view"]
        row = next(a for a in view["actions"] if a["action_id"] == action)
        self.assertIsNone(row["saved_record_count"])
        self.assertIsNone(row["elapsed_ms"])
        self.assertTrue(view["diagnostics"])
        self.assertEqual(self.progress()["lifecycle_status"], "running")

    def test_original_worker_can_repair_incomplete_and_nonfatal_notes_still_settle(self):
        action = self.analysis[0]
        worker = self.bind(action)
        self.write(action, worker, finish=False, settle=False)
        incomplete = settle_action(str(self.root), self.run_id, action)
        self.assertEqual(incomplete["validation"]["status"], "incomplete")
        self.assertEqual(self.progress()["actions"][action]["task_id"], worker)
        self.bind(action, worker)
        revision = read_result(self.task(action)["result_path"]).revision
        saved = result_write(str(self.root), self.run_id, action, worker, expected_revision=revision,
                             records=[{"kind": "future_record_kind", "body": {"note": "preserve unknown semantic content"}}])
        work_finish(str(self.root), self.run_id, action, worker, revision=saved["revision"])
        settle_action(str(self.root), self.run_id, action)
        self.assertEqual(self.progress()["actions"][action]["status"], "settled")
        visible = result_read(str(self.root), self.run_id, action, worker)
        self.assertEqual(visible["active_record_count"], 2)
        self.assertTrue(visible["warnings"])
        self.assertIsNone(self.progress()["quality_status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
