"""Advisory routing feedback and same-Reviewer correction; no semantic gate.

Run: python verification/review_routing_consistency.py
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import observability_recovery as recovery
from pangea_agent.cli.adapter_api import settle_action
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


class ReviewerIntegrationChecks(unittest.TestCase):
    def setUp(self):
        self.h = recovery.RecoveryChecks()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        for action in self.h.analysis:
            self.h.write(action, self.h.bind(action))
        self.review = next(a["action_id"] for a in self.h.progress()["actions"].values() if a["role"] == "review")
        self.worker = self.h.bind(self.review, "same-reviewer")
        self.args = (str(self.h.root), self.h.run_id, self.review, self.worker)
        self.finding = comparison_finding_write(*self.args, expected_revision=0, unit_ids=["unit-0001"],
            finding={"summary": "Correction required", "body": "Reviewer original semantic evidence"})
        self.body = {"disposition": "finding", "correction_record_ids": [],
                     "version_set_id": self.h.task(self.review)["version_set_id"], "summary": "Preserve original"}
        self.decision = review_decide(*self.args, expected_revision=self.finding["revision"], decision=self.body)

    def test_same_reviewer_can_correct_in_original_turn_and_dispatch_original_worker(self):
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
        settle_action(*self.args[:3])
        closures = [a for a in self.h.progress()["actions"].values() if a["role"] == "closure"]
        self.assertEqual(len(closures), 1)
        origin = self.h.progress()["actions"][self.h.analysis[0]]
        self.assertEqual(closures[0]["task_id"], origin["task_id"])
        self.assertEqual(closures[0]["action"], "continue_agent")

    def test_unhandled_warning_is_not_a_gate_and_cannot_be_reported_pass(self):
        final = work_finish(*self.args, revision=self.decision["revision"])
        self.assertTrue(final["warnings"])
        validation = validate_source_first_result(*self.args)
        self.assertEqual(validation["status"], "valid")
        self.assertTrue(any(w["kind"] == "source_first_correction_routing" for w in validation["warnings"]))
        settle_action(*self.args[:3])
        progress = self.h.progress()
        self.assertEqual(progress["quality_status"], "UNRESOLVED")
        self.assertTrue(any(d["kind"] == "source_first_correction_routing" for d in progress["degradations"]))
        self.assertFalse(any(a["role"] == "closure" for a in progress["actions"].values()))
        self.assertEqual(progress["actions"][self.review]["status"], "accepted")

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
        settle_action(*self.args[:3])
        progress = self.h.progress()
        self.assertEqual(progress["quality_status"], "PASS")
        self.assertFalse(any(a["role"] == "closure" for a in progress["actions"].values()))
        result = read_result(self.h.task(self.review)["result_path"])
        self.assertTrue(any(r.record_id == self.finding["record_ids"][0] for r in active_records(result)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
