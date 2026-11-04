"""Real Graph regression checks for atomic, idempotent planning submission."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.plan_batch import plan_write_batch
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.cli.source_first_api import plan_write, result_supersede, result_write, work_finish
from pangea_agent.graph.result_store import ResultStoreError, read_result
from pangea_agent.inventory.source_access import SourceAccessError


class PlanningBatchWrite(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix="planning-batch-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo = root / "repositories/demo/src"
        repo.mkdir(parents=True)
        for name in ("one", "two", "three", "four", "five"):
            (repo / f"{name}.c").write_text(f"int {name}(void) {{ return 1; }}\n")
        request = root / "request.json"
        write_json(request, {"data_root": str(root), "repository": "demo", "target": "batch",
                             "source_scope": ["src"], "analysis_profile": "behavior-test-v2",
                             "analysis_settings": {"scenario": "module-analysis", "mode": "speed"}})
        created = run_module_analysis(str(request))
        run_id = created["run_id"]
        action = created["agent_actions"][0]["action_id"]
        run = root / "runs" / run_id
        progress = read_json(run / "progress.json")
        task_path = Path(progress["actions"][action]["task_path"])
        task = read_json(task_path)
        bind_action(str(root), run_id, action, "planner")
        return (str(root), run_id, action, "planner"), Path(task["result_path"]), task_path, run

    def setUp(self):
        self.args, self.result_path, self.task_path, self.run = self.fixture()

    def unit(self, name):
        return {"title": f"业务 {name}", "purpose": "主责与待核实边界；不是分析结论",
                "owned_files": [{"repo_id": "demo", "path": f"src/{name}.c"}],
                "context_files": [], "planner_detail": {"original": "中文及未知字段原样保存"}}

    def batch(self, units, **kwargs):
        return plan_write_batch(*self.args, units=units, **kwargs)

    def test_five_units_one_atomic_revision_and_original_bodies(self):
        units = [self.unit(name) for name in ("one", "two", "three", "four", "five")]
        incoming = deepcopy(units)
        response = self.batch(units, expected_revision=0, request_id="five")
        self.assertEqual(response["revision"], 1)
        self.assertEqual(response["current_revision"], 1)
        self.assertFalse(response["replayed"])
        self.assertTrue(response["diagnostics"]["ready"])
        result = read_result(self.result_path)
        self.assertEqual(len(result.records), 5)
        for number, (unit, record, saved) in enumerate(zip(units, result.records, response["units"]), 1):
            self.assertEqual(saved, {"unit_id": f"unit-{number:04d}", "plan_record_id": record.record_id,
                                     "operation": "created"})
            self.assertEqual(record.body, {**unit, "unit_id": saved["unit_id"]})
            self.assertEqual(record.created_revision, 1)
        self.assertEqual(units, incoming)

    def test_batch_and_single_submissions_keep_same_effective_units(self):
        units = [self.unit("one"), self.unit("two")]
        batch = self.batch(units, expected_revision=0)
        other_args, other_path, _, _ = self.fixture()
        revision = 0
        for unit in units:
            saved = plan_write(*other_args, expected_revision=revision, unit=unit)
            revision = saved["revision"]
        self.assertEqual([record.body for record in read_result(self.result_path).records],
                         [record.body for record in read_result(other_path).records])
        self.assertEqual(batch["revision"], 1)
        self.assertEqual(revision, 2)

    def test_retry_after_later_write_returns_original_ids_and_revision(self):
        units = [self.unit("one"), self.unit("two")]
        first = self.batch(units, expected_revision=0, request_id="batch-id")
        self.batch([self.unit("three")], expected_revision=1, request_id="later")
        before = self.result_path.read_bytes()
        retried = self.batch(units, expected_revision=0, request_id="batch-id")
        self.assertEqual(retried["units"], first["units"])
        self.assertEqual(retried["revision"], 1)
        self.assertEqual(retried["current_revision"], 2)
        self.assertEqual(retried["diagnostics"]["unit_count"], 2)
        self.assertTrue(retried["replayed"])
        self.assertEqual(before, self.result_path.read_bytes())

    def test_same_request_id_with_different_payload_or_operation_is_rejected(self):
        self.batch([self.unit("one")], expected_revision=0, request_id="same")
        before = self.result_path.read_bytes()
        with self.assertRaisesRegex(ResultStoreError, "request_id"):
            self.batch([self.unit("two")], expected_revision=0, request_id="same")
        with self.assertRaisesRegex(ResultStoreError, "request_id"):
            plan_write(*self.args, unit=self.unit("one"), expected_revision=0, request_id="same")
        with self.assertRaisesRegex(ResultStoreError, "request_id"):
            result_write(*self.args, records=[{"kind": "note", "body": "different operation"}],
                         expected_revision=0, request_id="same")
        self.assertEqual(before, self.result_path.read_bytes())

    def test_single_retry_keeps_original_machine_unit_id(self):
        first = plan_write(*self.args, unit=self.unit("one"), expected_revision=0, request_id="single")
        self.batch([self.unit("two")], expected_revision=1)
        before = self.result_path.read_bytes()
        retry = plan_write(*self.args, unit=self.unit("one"), expected_revision=0, request_id="single")
        self.assertEqual(retry["unit_id"], first["unit_id"])
        self.assertEqual(retry["plan_record_id"], first["plan_record_id"])
        self.assertEqual(retry["revision"], first["revision"])
        self.assertEqual(before, self.result_path.read_bytes())

    def test_plan_request_id_cannot_pretend_to_finish_or_replace(self):
        first = self.batch([self.unit("one")], expected_revision=0, request_id="plan-id")
        before = self.result_path.read_bytes()
        with self.assertRaisesRegex(ResultStoreError, "不能用于 work-finish"):
            work_finish(*self.args, revision=1, request_id="plan-id")
        self.assertEqual(before, self.result_path.read_bytes())
        self.assertIsNone(read_result(self.result_path).completion)
        with self.assertRaisesRegex(ResultStoreError, "不能用于 result-supersede"):
            result_supersede(*self.args, expected_revision=1,
                             target_record_ids=[first["units"][0]["plan_record_id"]],
                             replacement={"kind": "unit_plan", "body": {**self.unit("one"),
                                          "unit_id": first["units"][0]["unit_id"], "purpose": "不会保存"}},
                             request_id="plan-id")
        self.assertEqual(before, self.result_path.read_bytes())
        self.assertEqual(len(read_result(self.result_path).records), 1)
        finished = work_finish(*self.args, revision=1, request_id="finish-id")
        after_finish = self.result_path.read_bytes()
        self.assertEqual(work_finish(*self.args, revision=1, request_id="finish-id"), finished)
        self.assertEqual(after_finish, self.result_path.read_bytes())
        self.assertTrue(read_result(self.result_path).completion.complete)

    def test_legacy_single_receipt_is_read_without_rewriting(self):
        first = plan_write(*self.args, unit=self.unit("one"), expected_revision=0, request_id="legacy")
        payload = read_json(self.result_path)
        payload["receipts"]["legacy"].pop("request_fingerprint")
        write_json(self.result_path, payload)
        before = self.result_path.read_bytes()
        retry = plan_write(*self.args, unit=self.unit("one"), expected_revision=0, request_id="legacy")
        self.assertEqual(retry["unit_id"], first["unit_id"])
        self.assertEqual(before, self.result_path.read_bytes())
        with self.assertRaisesRegex(ResultStoreError, "request_id"):
            plan_write(*self.args, unit=self.unit("two"), expected_revision=0, request_id="legacy")

    def test_invalid_item_and_stale_revision_preserve_previous_saved_work(self):
        self.batch([self.unit("one")], expected_revision=0)
        before = self.result_path.read_bytes()
        with self.assertRaisesRegex(ResultStoreError, r"units\[1\].purpose"):
            self.batch([self.unit("two"), {**self.unit("three"), "purpose": ""}], expected_revision=1)
        self.assertEqual(before, self.result_path.read_bytes())
        with self.assertRaises(ResultStoreError):
            self.batch([self.unit("two")], expected_revision=0, request_id="stale")
        self.assertEqual(before, self.result_path.read_bytes())
        fixed = self.batch([self.unit("two"), self.unit("three")], expected_revision=1, request_id="fixed")
        self.assertEqual(fixed["revision"], 2)
        self.assertEqual(len(read_result(self.result_path).records), 3)

    def test_path_and_task_boundaries_checked_before_retry(self):
        self.batch([self.unit("one")], expected_revision=0, request_id="owner")
        before = self.result_path.read_bytes()
        with self.assertRaises(SourceAccessError):
            plan_write_batch(*self.args[:3], "another-worker", units=[self.unit("one")],
                             expected_revision=0, request_id="owner")
        with self.assertRaises(SourceAccessError):
            self.batch([self.unit("two"), {**self.unit("three"),
                       "owned_files": [{"repo_id": "demo", "path": "../outside.c"}]}], expected_revision=1)
        self.assertEqual(before, self.result_path.read_bytes())

    def test_nonfatal_reference_issues_remain_saved_and_correctable(self):
        task = read_json(self.task_path)
        task.setdefault("record_contracts", {})["coverage_reference"] = "coverage-reference-v1"
        write_json(self.task_path, task)
        response = self.batch([{**self.unit("one"), "coverage_ids": ["missing-gap"]}], expected_revision=0)
        self.assertTrue(any(item.get("code") == "unknown_coverage_reference" for item in response["warnings"]))
        result = read_result(self.result_path)
        self.assertEqual(result.records[0].body["coverage_ids"], ["missing-gap"])
        self.assertIsNone(result.completion)
        self.assertTrue(response["diagnostics"]["ready"])

    def test_result_binding_is_checked_before_receipt_replay(self):
        self.batch([self.unit("one")], expected_revision=0, request_id="owner")
        payload = read_json(self.result_path)
        payload["binding"]["task_id"] = "another-worker"
        write_json(self.result_path, payload)
        before = self.result_path.read_bytes()
        with self.assertRaisesRegex(ResultStoreError, "binding task_id"):
            self.batch([self.unit("one")], expected_revision=0, request_id="owner")
        self.assertEqual(before, self.result_path.read_bytes())

    def test_create_update_and_handoff_retain_notes_and_unresolved(self):
        first = self.batch([self.unit("one"), self.unit("two")], expected_revision=0)
        updated = self.batch([{**self.unit("one"), "unit_id": first["units"][0]["unit_id"],
                               "purpose": "已修订主责"}, self.unit("three")], expected_revision=1, request_id="update")
        self.assertEqual(updated["units"][0]["operation"], "updated")
        self.assertEqual(updated["units"][1]["unit_id"], "unit-0003")
        notes = result_write(*self.args, expected_revision=2, records=[
            {"kind": "note", "body": "共享边界", "relates_to": [], "evidence": [{"literal": "keep"}]},
            {"kind": "unresolved", "body": "入口尚待确认", "relates_to": [first["units"][0]["plan_record_id"]]},
        ])
        work_finish(*self.args, revision=notes["revision"])
        settle_action(*self.args[:3])
        handoffs = [read_json(path) for path in (self.run / "inputs/planning-handoff").glob("*.json")]
        self.assertEqual(len(handoffs), 3)
        owner = next(item for item in handoffs if item["unit_id"] == first["units"][0]["unit_id"])
        records = [item["record"] for item in owner["records"]]
        self.assertTrue(any(item["kind"] == "unresolved" and item["body"] == "入口尚待确认" for item in records))
        self.assertTrue(any(item["kind"] == "unit_plan" and item["body"]["purpose"] == "已修订主责" for item in records))
        self.assertTrue(any(item["evidence"] == [{"literal": "keep"}] for item in records))

    def test_duplicate_concurrent_request_creates_one_batch(self):
        units = [self.unit("one"), self.unit("two")]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.batch(units, expected_revision=0, request_id="concurrent"), range(2)))
        self.assertEqual(results[0]["units"], results[1]["units"])
        self.assertEqual(sum(result["replayed"] for result in results), 1)
        self.assertEqual(read_result(self.result_path).revision, 1)
        self.assertEqual(len(read_result(self.result_path).records), 2)

    def test_same_unit_cannot_be_updated_twice_in_one_batch(self):
        first = self.batch([self.unit("one")], expected_revision=0)
        update = {**self.unit("one"), "unit_id": first["units"][0]["unit_id"]}
        before = self.result_path.read_bytes()
        with self.assertRaisesRegex(ResultStoreError, "重复更新"):
            self.batch([update, update], expected_revision=1)
        self.assertEqual(before, self.result_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
