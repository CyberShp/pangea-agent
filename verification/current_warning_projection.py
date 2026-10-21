"""Current warnings retire only explicit result-local record identities.

Includes real Graph validation and current/history read modes, without a model.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from pangea_agent.graph.result_store import (
    current_result_warnings, current_warning_degradations, read_records,
    read_records_compact, read_result,
)
from pangea_agent.models.source_first import NotesResult


def result(action_id="action-a"):
    binding = dict(data_root="/data", run_id="run", action_id=action_id, task_id=f"worker-{action_id}")
    def diagram(record_id, revision, **changes):
        return dict(binding, kind="diagram_format_diagnostic", record_id=record_id,
                    record_revision=revision, message="diagram") | changes
    return NotesResult.model_validate(dict(binding=binding, revision=3,
        records=[dict(record_id="one", kind="flow", body={}, created_revision=1),
                 dict(record_id="two", kind="flow", body={}, created_revision=2, supersedes=["one"])],
        warnings=[dict(kind="flow_path_hint", record_id="one", message="retired exact warning"),
                  dict(kind="flow_path_hint", record_id="two", message="current warning"),
                  dict(kind="flow_path_hint", message="unbound old warning"),
                  dict(kind="custom_warning", record_id="one", message="unknown owner semantics"),
                  dict(kind="flow_path_hint", action_id="foreign", record_id="one", message="foreign warning"),
                  diagram("one", 1), diagram("two", 1), diagram("two", 2, action_id="foreign"), diagram("two", 2)]))


class CurrentWarningProjection(unittest.TestCase):
    def test_current_projection_preserves_unknown_identity_and_strict_diagram_checks(self):
        value = result()
        original = value.model_dump(mode="json")
        self.assertEqual(current_result_warnings(value), [value.warnings[i] for i in (1, 2, 3, 4, 8)])
        self.assertEqual(value.model_dump(mode="json"), original)

    def test_flattened_warning_bridge_is_exact_action_local_and_conservative(self):
        a, b = result(), result("action-b")
        b.records = b.records[:1]
        b.warnings = b.warnings[:1]
        pending = [dict(action_id="action-a", kind="agent_result_warning", message="retired exact warning"),
                   dict(action_id="action-b", kind="agent_result_warning", message="retired exact warning"),
                   dict(action_id="action-a", kind="agent_result_warning", message="current warning"),
                   dict(action_id="action-a", kind="agent_result_warning", message="unbound old warning"),
                   dict(action_id="action-a", kind="agent_result_warning", message="retired exact warning!"),
                   dict(action_id="action-a", kind="agent_result_warning", message="retired exact warning",
                        source_warning=dict(action_id="foreign", record_id="one"))]
        original = deepcopy(pending)
        self.assertEqual(current_warning_degradations(pending, {"action-a": a, "action-b": b}), pending[1:])
        a.warnings.append(dict(kind="flow_path_hint", record_id="two", message=pending[0]["message"]))
        self.assertEqual(current_warning_degradations(pending, {"action-a": a, "action-b": b}), pending)
        a.warnings[-1].pop("record_id")
        self.assertEqual(current_warning_degradations(pending, {"action-a": a, "action-b": b}), pending)
        self.assertEqual(pending, original)
        self.assertEqual(current_warning_degradations(pending, {"action-a": b}), pending)

    def test_report_filters_only_current_diagnostics_and_keeps_record_audit(self):
        from pangea_agent.report.source_first import _markdown
        a = result()
        progress = dict(run_id="run", degradations=[dict(action_id="action-a", kind="agent_result_warning", message="retired exact warning"),
                                      dict(action_id="action-a", kind="agent_result_warning", message="current warning")])
        text = _markdown({}, progress, [(dict(action_id="action-a", task_id="worker-action-a", stage="unit_analysis"), a)])
        self.assertNotIn("retired exact warning", text)
        self.assertIn("current warning", text)
        self.assertIn("Superseded records (audit only)", text)
        self.assertEqual(len(a.warnings), 9)

    def test_real_graph_validation_and_current_history_reads(self):
        import observability_recovery as recovery
        from flow_path_diagnostics import flow
        from pangea_agent.cli.adapter_api import _record_degradations, settle_action
        from pangea_agent.cli.source_first_api import comparison_read, result_write, validate_source_first_result, work_finish
        harness = recovery.RecoveryChecks(methodName="test_plan_counts_saved_progress_and_polling_are_facts")
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        action = harness.analysis[0]
        worker = harness.bind(action)
        path = harness.task(action)["result_path"]
        broken = flow()
        broken["paths"][0]["edge_ids"] = []
        first = result_write(str(harness.root), harness.run_id, action, worker, expected_revision=0,
                             records=[dict(kind="flow", body=broken)])
        old_warnings = deepcopy(read_result(path).warnings)
        self.assertTrue(old_warnings)
        second = result_write(str(harness.root), harness.run_id, action, worker, expected_revision=1,
                              records=[dict(kind="flow", body=flow(), supersedes=first["record_ids"])])
        binding = read_result(path).binding
        self.assertEqual(read_records(path, binding)["warnings"], old_warnings)
        self.assertEqual(read_records(path, binding)["current_warnings"], [])
        self.assertFalse(any(item["item_type"] == "warning" for item in read_records_compact(path, binding)["items"]))
        self.assertEqual(read_records_compact(path, binding)["warning_count"], 0)
        self.assertEqual(read_records_compact(path, binding)["historical_warning_count"], len(old_warnings))
        self.assertTrue(any(item["item_type"] == "warning" for item in read_records_compact(path, binding, include_history=True)["items"]))
        validation = validate_source_first_result(str(harness.root), harness.run_id, action, worker)
        self.assertEqual(validation["status"], "incomplete")
        self.assertEqual(validation["warnings"], [])
        work_finish(str(harness.root), harness.run_id, action, worker, revision=second["revision"])
        settle_action(str(harness.root), harness.run_id, action)
        self.assertFalse(any(item.get("action_id") == action for item in harness.progress()["degradations"]))
        self.assertEqual(read_result(path).warnings, old_warnings)
        other = harness.analysis[1]
        other_worker = harness.bind(other)
        written = result_write(str(harness.root), harness.run_id, other, other_worker, expected_revision=0,
                               records=[dict(kind="flow", body=broken)])
        work_finish(str(harness.root), harness.run_id, other, other_worker, revision=written["revision"])
        settle_action(str(harness.root), harness.run_id, other)
        review = next(a["action_id"] for a in harness.progress()["actions"].values() if a["role"] == "review")
        reviewer = harness.bind(review, "reviewer")
        version_set = harness.task(review)["version_set_id"]
        comparison = comparison_read(str(harness.root), harness.run_id, review, reviewer,
                                     version_set_id=version_set, view="compact", max_chars=24000)
        warning_items = [item for item in comparison["items"] if item["item_type"] == "warning"]
        self.assertTrue(warning_items)
        self.assertTrue(all(item["action_id"] == other and item["value"]["record_id"] == "rec-000001" for item in warning_items))
        legacy = comparison_read(str(harness.root), harness.run_id, review, reviewer, version_set_id=version_set)
        self.assertFalse(next(entry for entry in legacy["entries"] if entry["action_id"] == action)["warnings"])
        history = comparison_read(str(harness.root), harness.run_id, review, reviewer, version_set_id=version_set, include_history=True)
        self.assertEqual(next(entry for entry in history["entries"] if entry["action_id"] == action)["warnings"], old_warnings)
        # New validations retain warning identity rather than flattening it away.
        progress = SimpleNamespace(degradations=[])
        _record_degradations(progress, action, ["example"], [dict(kind="flow_path_hint", record_id="two", message="example")])
        self.assertEqual(progress.degradations[0]["source_warning"]["record_id"], "two")


if __name__ == "__main__":
    unittest.main(verbosity=2)
