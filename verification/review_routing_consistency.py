"""Historical routing APIs and preserved records; no production review dispatch.

Run: python verification/review_routing_consistency.py
"""
import sys
from pathlib import Path

# Embedded Python does not add the script directory to sys.path.
# Keep sibling verification helpers local to this acceptance process.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from copy import deepcopy
from types import SimpleNamespace
import unittest

import observability_recovery as recovery
from pangea_agent.cli.source_first_api import (
    comparison_finding_write, result_read, review_decide,
    validate_source_first_result, work_finish,
)
from pangea_agent.graph.nodes.source_first import review_correction_routes
from pangea_agent.graph.result_store import active_records, read_result
from pangea_agent.models.source_first import NoteRecord


def record(id, kind, body, **kwargs):
    return NoteRecord(record_id=id, kind=kind, body=body, created_revision=1, **kwargs)


class RoutingChecks(unittest.TestCase):
    def route(self, decision, findings=None):
        records = findings if findings is not None else [record("F1", "finding", "Keep evidence", relates_to=["U1"])]
        result = SimpleNamespace(records=[*records, record("D1", "review_decision", decision)])
        before = deepcopy(result.records)
        output = review_correction_routes(result, {"U1"})
        self.assertEqual(result.records, before)
        return output

    def test_finding_empty_missing_unknown_retired_and_invalid_shape(self):
        for selection in ({}, {"correction_record_ids": []}, {"correction_record_ids": ["unknown"]},
                          {"correction_record_ids": "F1"}, {"correction_record_ids": [{}]}):
            with self.subTest(selection=selection):
                disposition, routes, warnings = self.route({"disposition": "finding", **selection})
                self.assertEqual(disposition, "finding")
                self.assertEqual(routes, {})
                self.assertTrue(warnings)
        findings = [record("F1", "finding", "original", relates_to=["U1"]),
                    record("F2", "finding", "replacement", relates_to=["U1"], supersedes=["F1"])]
        self.assertTrue(self.route({"disposition": "finding", "correction_record_ids": ["F1"]}, findings)[2])

    def test_valid_partial_and_legacy_routes_remain_explicit(self):
        for selection in ({"correction_record_ids": ["F1"]}, {"closure_units": ["U1"]}):
            _, routes, warnings = self.route({"disposition": "finding", **selection})
            self.assertEqual(list(routes), ["U1"])
            self.assertEqual([r.record_id for r in routes["U1"]], ["F1"])
            self.assertEqual(warnings, [])
        _, routes, warnings = self.route({"disposition": "finding", "correction_record_ids": ["F1", "unknown"]})
        self.assertEqual(list(routes), ["U1"])
        self.assertTrue(warnings)
        # Explicit empty record selection must not fall back to old units.
        self.assertEqual(self.route({"disposition": "finding", "correction_record_ids": [], "closure_units": ["U1"]})[1], {})

    def test_pass_and_unresolved_empty_selection_allow_deliberate_withdrawal(self):
        for disposition in ("pass", "unresolved"):
            for selection in ({}, {"correction_record_ids": []}, {"closure_units": []}):
                self.assertEqual(self.route({"disposition": disposition, **selection}), (disposition, {}, []))

    def test_invalid_unit_bindings_are_not_repaired_by_python(self):
        for units in ([], ["unknown"], "U1", [{}]):
            with self.subTest(units=units):
                _, routes, warnings = self.route({"disposition": "finding", "correction_record_ids": ["F1"]},
                    [record("F1", "finding", "unchanged", relates_to=units)])
                self.assertEqual(routes, {})
                self.assertTrue(warnings)


class HistoricalReviewerReadChecks(unittest.TestCase):
    def test_saved_routing_warning_remains_readable_without_dispatch(self):
        from history_review_fixture import saved_comparison
        from pangea_agent.agent_io import write_json
        from pangea_agent.cli.public_api import resume_run
        h = recovery.RecoveryChecks()
        h.setUp()
        self.addCleanup(h.doCleanups)
        for action in h.analysis:
            h.write(action, h.bind(action))
        review, worker = saved_comparison(h)
        path = h.task(review)["result_path"]
        saved = read_result(path)
        saved.records = [record("F1", "finding", {"summary": "Historical correction", "body": "Saved evidence"}, relates_to=["unit-0001"]),
                         record("D1", "review_decision", {"disposition": "finding", "correction_record_ids": []})]
        saved.revision = 1
        write_json(Path(path), saved.model_dump(mode="json"))
        before = Path(path).read_bytes()
        for view in ("legacy", "compact"):
            self.assertTrue(result_read(str(h.root), h.run_id, review, worker, view=view)["routing_warnings"])
        resume_run(str(h.root), h.run_id, host_quiescent=True)
        self.assertFalse(any(a["role"] == "closure" for a in h.progress()["actions"].values()))
        self.assertEqual(h.progress()["quality_status"], "UNRESOLVED")
        self.assertEqual(Path(path).read_bytes(), before)


class HistoricalReviewerApiChecks(unittest.TestCase):
    def setUp(self):
        self.h = recovery.RecoveryChecks()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        for action in self.h.analysis:
            self.h.write(action, self.h.bind(action))
        from history_review_fixture import saved_comparison
        self.review, self.worker = saved_comparison(self.h, "same-reviewer")
        self.args = (str(self.h.root), self.h.run_id, self.review, self.worker)
        self.finding = comparison_finding_write(*self.args, expected_revision=0, unit_ids=["unit-0001"],
            finding={"summary": "Correction required", "body": "Reviewer original semantic evidence"})
        self.body = {"disposition": "finding", "correction_record_ids": [],
                     "version_set_id": self.h.task(self.review)["version_set_id"], "summary": "Preserve original"}
        self.decision = review_decide(*self.args, expected_revision=self.finding["revision"], decision=self.body)

    def test_historical_reviewer_correction_preserves_original_records(self):
        self.assertTrue(self.decision["warnings"])
        hint = self.decision["routing_feedback"]
        self.assertTrue(hint["saved"])
        self.assertEqual(hint["task_id"], self.worker)
        for view in ("legacy", "compact"):
            self.assertTrue(result_read(*self.args, view=view)["routing_warnings"])
        corrected = review_decide(*self.args, expected_revision=hint["expected_revision"],
            replace_decision_record_ids=hint["replace_decision_record_ids"],
            decision={**self.body, "correction_record_ids": self.finding["record_ids"]})
        self.assertFalse(corrected["warnings"])
        self.assertFalse(result_read(*self.args)["routing_warnings"])
        final = work_finish(*self.args, revision=corrected["revision"])
        self.assertFalse(final["warnings"])
        validation = validate_source_first_result(*self.args)
        self.assertEqual(validation["status"], "valid")
        self.assertFalse([w for w in validation["warnings"] if w["kind"] == "source_first_correction_routing"])
        result = read_result(self.h.task(self.review)["result_path"])
        self.assertEqual(result.binding.task_id, self.worker)
        self.assertEqual(result.records[1].body, self.body)
        self.assertTrue(any(r.record_id == self.finding["record_ids"][0] for r in active_records(result)))
        self.assertFalse(any(a["role"] == "closure" for a in self.h.progress()["actions"].values()))

    def test_unhandled_warning_is_not_a_gate_and_cannot_be_reported_pass(self):
        final = work_finish(*self.args, revision=self.decision["revision"])
        self.assertTrue(final["warnings"])
        validation = validate_source_first_result(*self.args)
        self.assertEqual(validation["status"], "valid")
        self.assertTrue(any(w["kind"] == "source_first_correction_routing" for w in validation["warnings"]))
        self.assertTrue(result_read(*self.args)["routing_warnings"])
        self.assertFalse(any(a["role"] == "closure" for a in self.h.progress()["actions"].values()))

    def test_validation_keeps_routing_warning_before_completion_and_when_incomplete(self):
        validation = validate_source_first_result(*self.args)
        self.assertEqual(validation["status"], "incomplete")
        self.assertTrue(any(w["kind"] == "source_first_correction_routing" for w in validation["warnings"]))
        work_finish(*self.args, revision=self.decision["revision"], complete=False)
        validation = validate_source_first_result(*self.args)
        self.assertEqual(validation["status"], "incomplete")
        self.assertTrue(any(w["kind"] == "source_first_correction_routing" for w in validation["warnings"]))

    def test_reviewer_may_withdraw_without_retiring_prior_finding(self):
        corrected = review_decide(*self.args, expected_revision=self.decision["revision"],
            replace_decision_record_ids=self.decision["record_ids"],
            decision={**self.body, "disposition": "pass"})
        self.assertEqual(corrected["warnings"], [])
        self.assertNotIn("routing_feedback", corrected)
        work_finish(*self.args, revision=corrected["revision"])
        self.assertFalse(result_read(*self.args)["routing_warnings"])
        result = read_result(self.h.task(self.review)["result_path"])
        self.assertTrue(any(r.record_id == self.finding["record_ids"][0] for r in active_records(result)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
