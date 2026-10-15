"""Frozen case-write contract regressions: python verification/test_case_contract.py."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import tempfile
import unittest
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.analysis_scenarios import SCENES, freeze_scene, scene_spec
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.cli.source_first_api import (
    plan_write, result_read, result_repair, result_supersede, result_write,
    validate_source_first_result, work_finish,
)
from pangea_agent.graph.result_store import (
    ResultStoreError, active_records, append_records, initialize_result, read_result, repair_result_shell,
)
from pangea_agent.inventory.source_access import compact_task_view
from pangea_agent.models.source_first import SourceBinding
from pangea_agent.test_case_schema import TEST_CASE_FORMAT, check_test_case_structure


CONTRACTS = {"test_case": TEST_CASE_FORMAT}


def case_body():
    return {
        "format_version": TEST_CASE_FORMAT, "case_id": "TC-001", "title": "连接拒绝及恢复",
        "steps": [
            {"action": "使用错误密钥连接", "expected": "连接被拒绝且无新增设备"},
            {"action": "换回正确密钥重连", "expected": "连接成功且设备可见"},
        ],
        "external_observations": [],
        "variants": [{"input": "错误密钥 B", "expected": "连接被拒绝"}],
    }


def malformed_body():
    body = case_body()
    body.update(steps=["建立 TCP 连接", "发送非法 ICReq"], external_observations="连接被拒绝")
    return body


def case_record(body=None):
    return {"kind": "test_case", "body": case_body() if body is None else body}


class CaseStructureChecks(unittest.TestCase):
    def test_exact_field_errors_without_semantic_decisions(self):
        checks = [
            (None, "body 必须"), ("{}", "body 必须"), ([], "body 必须"),
            ({**case_body(), "format_version": None}, "body.format_version"),
            ({**case_body(), "format_version": "custom"}, "body.format_version"),
            ({**case_body(), "steps": None}, "body.steps"),
            ({**case_body(), "steps": []}, "body.steps"),
            ({**case_body(), "steps": ["发送请求"]}, "body.steps[0]"),
            ({**case_body(), "steps": [{}]}, "body.steps[0].action"),
            ({**case_body(), "steps": [{"action": "发送请求"}]}, "body.steps[0].expected"),
            ({**case_body(), "steps": [{"action": 42, "expected": "拒绝"}]}, "body.steps[0].action"),
            ({**case_body(), "steps": [{"action": "发送请求", "expected": False}]}, "body.steps[0].expected"),
            ({**case_body(), "steps": [{"action": "发送请求", "expected": " \t\n　"}]}, "body.steps[0].expected"),
            ({**case_body(), "external_observations": "被拒绝"}, "body.external_observations"),
            ({**case_body(), "variants": None}, "body.variants"),
            ({**case_body(), "variants": ["非法类型"]}, "body.variants[0]"),
            ({**case_body(), "variants": [{"input": "非法类型"}]}, "body.variants[0].expected"),
            ({**case_body(), "variants": [{"input": " ", "expected": "拒绝"}]}, "body.variants[0].input"),
        ]
        for body, path in checks:
            with self.subTest(path=path, body=body):
                self.assertTrue(any(path in error for error in check_test_case_structure(body)))
        for missing in ("format_version", "steps", "external_observations"):
            body = case_body()
            del body[missing]
            self.assertTrue(any(f"body.{missing}" in error for error in check_test_case_structure(body)))

        valid = case_body()
        valid.update(purpose="worker-choice", risk_refs=["unconfirmed"], extra="原文保持")
        self.assertEqual(check_test_case_structure(valid), [])
        valid["variants"] = []
        self.assertEqual(check_test_case_structure(valid), [])
        del valid["variants"]
        self.assertEqual(check_test_case_structure(valid), [])

    def test_new_scenes_declare_contract_and_existing_freeze_is_unchanged(self):
        for scenario in SCENES:
            self.assertEqual(scene_spec(scenario)["record_contracts"]["test_case"], TEST_CASE_FORMAT)
        risk_contracts = scene_spec("risk-analysis")["record_contracts"]
        self.assertEqual(risk_contracts["risk"], "behavior-risk-v2")
        self.assertEqual(risk_contracts["check"], "behavior-check-v1")
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            contract = {"analysis_profile": "behavior-test-v2", "analysis_settings": {"scenario": "module-analysis"}}
            self.assertEqual(freeze_scene(run, contract)["record_contracts"]["test_case"], TEST_CASE_FORMAT)
            legacy = scene_spec("module-analysis")
            del legacy["record_contracts"]["test_case"]
            path = run / "inputs/analysis-scene.json"
            write_json(path, legacy)
            before = path.read_bytes()
            self.assertEqual(freeze_scene(run, contract), legacy)
            self.assertEqual(path.read_bytes(), before)
            self.assertIsNone(freeze_scene(run, {"analysis_profile": "behavior-test-v1"}))

    def test_contract_is_visible_in_compact_task_even_after_large_metadata(self):
        task = {f"metadata-{i}": "x" * 1500 for i in range(20)}
        task["record_contracts"] = scene_spec("risk-analysis")["record_contracts"]
        self.assertEqual(compact_task_view(task)["record_contracts"], task["record_contracts"])

    def test_legacy_records_remain_writable_readable_and_unmodified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            binding = SourceBinding(data_root=directory, run_id="legacy", action_id="analysis", task_id="same-worker")
            initialize_result(path, binding)
            malformed = malformed_body()
            append_records(path, binding, 0, [case_record(malformed)])
            self.assertEqual(read_result(path).records[0].body, malformed)
            before = path.read_bytes()
            # Declaring a contract on a later call does not rescan saved history.
            append_records(path, binding, 1, [{"kind": "note", "body": "后续记录"}], record_contracts=CONTRACTS)
            current = read_result(path)
            self.assertEqual(current.records[0].body, malformed)
            self.assertEqual(current.revision, 2)
            self.assertNotEqual(before, path.read_bytes())

    def test_legacy_repair_preserves_original_case_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            binding = SourceBinding(data_root=directory, run_id="legacy", action_id="analysis", task_id="same-worker")
            corrupt = b'{"binding":'
            path.write_bytes(corrupt)
            malformed = malformed_body()
            repaired = repair_result_shell(path, binding, [case_record(malformed)],
                                           expected_sha256=hashlib.sha256(corrupt).hexdigest())
            self.assertEqual(repaired["revision"], 1)
            self.assertEqual(read_result(path).records[0].body, malformed)
            self.assertEqual(Path(repaired["backup_path"]).read_bytes(), corrupt)


class CaseWriteGraphChecks(unittest.TestCase):
    def setUp(self):
        self.prepare_graph()

    def prepare_graph(self, scenario="module-analysis"):
        temporary = tempfile.TemporaryDirectory(prefix="pangea-case-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repo = self.root / "repositories/demo"
        repo.mkdir(parents=True)
        (repo / "one.c").write_text("int one(void) { return 1; }\n", encoding="utf-8")
        request = self.root / "request.json"
        write_json(request, {
            "data_root": str(self.root), "repository": "demo", "target": "用例结构验收",
            "source_scope": ["."], "analysis_profile": "behavior-test-v2",
            "analysis_settings": {"scenario": scenario, "mode": "speed"},
        })
        created = run_module_analysis(str(request))
        self.run_id = created["run_id"]
        self.run = self.root / "runs" / self.run_id
        planning = created["agent_actions"][0]["action_id"]
        bind_action(str(self.root), self.run_id, planning, "planner")
        written = plan_write(str(self.root), self.run_id, planning, "planner", expected_revision=0,
                             unit={"title": "one", "purpose": "结构格式回归", "owned_files": [{"repo_id": "demo", "path": "one.c"}]})
        work_finish(str(self.root), self.run_id, planning, "planner", revision=written["revision"])
        settle_action(str(self.root), self.run_id, planning)
        action = next(value for value in self.progress()["actions"].values() if value["role"] == "analysis")
        self.action_id = action["action_id"]
        self.task = read_json(Path(action["task_path"]))
        self.path = Path(self.task["result_path"])
        self.worker = "original-case-worker"
        bind_action(str(self.root), self.run_id, self.action_id, self.worker)
        self.args = (str(self.root), self.run_id, self.action_id, self.worker)
        self.assertEqual(self.task["record_contracts"]["test_case"], TEST_CASE_FORMAT)

    def progress(self):
        return read_json(self.run / "progress.json")

    def test_rejected_batch_is_atomic_and_same_worker_can_correct_and_replay(self):
        first = result_write(*self.args, expected_revision=0, records=[{"kind": "note", "body": "已完成的有效分析"}])
        before = self.path.read_bytes()
        progress_before = self.progress()
        missing_expected = case_body()
        del missing_expected["steps"][1]["expected"]
        records = [case_record(), case_record(malformed_body()), case_record(missing_expected)]
        for _ in range(2):
            with self.assertRaises(ResultStoreError) as caught:
                result_write(*self.args, expected_revision=first["revision"], records=records, request_id="cases-batch")
            message = str(caught.exception)
            self.assertIn("record[1].body.steps[0]", message)
            self.assertIn("record[1].body.external_observations", message)
            self.assertIn("record[2].body.steps[1].expected", message)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(self.progress(), progress_before)
        view = result_read(*self.args)
        self.assertEqual(view["revision"], 1)
        self.assertEqual(len(view["records"]), 1)
        self.assertIsNone(view["completion"])
        self.assertEqual(validate_source_first_result(*self.args)["status"], "incomplete")
        with self.assertRaises(ResultStoreError):
            work_finish(*self.args, revision=first["revision"] + 1)
        self.assertEqual(self.path.read_bytes(), before)
        corrected = result_write(*self.args, expected_revision=first["revision"], records=[case_record()], request_id="cases-batch")
        self.assertEqual(corrected["revision"], 2)
        self.assertEqual(corrected["record_ids"], ["rec-000002"])
        self.assertEqual(result_write(*self.args, expected_revision=first["revision"], records=[case_record()], request_id="cases-batch"), corrected)
        self.assertEqual(read_result(self.path).binding.task_id, self.worker)
        self.assertEqual(read_result(self.path).records[0].body, "已完成的有效分析")
        work_finish(*self.args, revision=corrected["revision"])
        self.assertEqual(validate_source_first_result(*self.args)["status"], "valid")
        settle_action(str(self.root), self.run_id, self.action_id)
        self.assertEqual(self.progress()["actions"][self.action_id]["status"], "accepted")

    def test_supersede_and_exact_edits_cannot_remove_expected_result(self):
        written = result_write(*self.args, expected_revision=0, records=[case_record()])
        before = self.path.read_bytes()
        target = written["record_ids"]
        with self.assertRaisesRegex(ResultStoreError, r"record\[0\]\.body.steps\[0\]"):
            result_supersede(*self.args, expected_revision=1, target_record_ids=target,
                             replacement={"body": malformed_body()})
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(ResultStoreError, r"record\[0\]\.body.steps\[0\].expected"):
            result_supersede(*self.args, expected_revision=1, target_record_ids=target,
                             edits=[{"path": ["steps", 0, "expected"], "old": case_body()["steps"][0]["expected"], "new": " "}])
        self.assertEqual(self.path.read_bytes(), before)
        fixed = case_body()
        fixed["steps"][0]["expected"] = "拒绝连接，设备数量不增加"
        replaced = result_supersede(*self.args, expected_revision=1, target_record_ids=target,
                                    replacement={"body": fixed}, request_id="replacement")
        self.assertEqual(replaced["revision"], 2)
        self.assertEqual([record.record_id for record in active_records(read_result(self.path))], replaced["record_ids"])
        self.assertEqual(read_result(self.path).records[0].body, case_body())
        self.assertEqual(result_supersede(*self.args, expected_revision=1, target_record_ids=target,
                                         replacement={"body": fixed}, request_id="replacement"), replaced)

    def test_repair_rejects_bad_cases_before_replacing_corrupt_shell(self):
        corrupt = b'{"binding":'
        self.path.write_bytes(corrupt)
        digest = hashlib.sha256(corrupt).hexdigest()
        with self.assertRaisesRegex(ResultStoreError, r"record\[0\]\.body.steps\[0\]"):
            result_repair(*self.args, records=[case_record(malformed_body())], expected_sha256=digest)
        self.assertEqual(self.path.read_bytes(), corrupt)
        self.assertFalse(list(self.path.parent.glob(f"{self.path.name}.corrupt-*")))
        repaired = result_repair(*self.args, records=[case_record()], expected_sha256=digest)
        self.assertEqual(repaired["revision"], 1)
        self.assertEqual(Path(repaired["backup_path"]).read_bytes(), corrupt)
        self.assertEqual(read_result(self.path).binding.task_id, self.worker)
        work_finish(*self.args, revision=1)
        self.assertEqual(validate_source_first_result(*self.args)["status"], "valid")

    def test_unrelated_inconsistencies_remain_warnings_and_can_finish(self):
        record = case_record()
        record.update(evidence="unconfirmed", relates_to="unconfirmed", supersedes=["not-a-real-record"])
        record["body"].update(purpose="worker-choice", coverage_refs=["unconfirmed"], execution_readiness="worker-choice")
        original = deepcopy(record)
        written = result_write(*self.args, expected_revision=0, records=[record])
        self.assertTrue({"invalid_evidence_shape", "invalid_relation_shape", "unknown_supersedes_record_id"}
                        <= {item["kind"] for item in written["warnings"]})
        self.assertEqual(read_result(self.path).records[0].body, original["body"])
        work_finish(*self.args, revision=written["revision"])
        self.assertEqual(validate_source_first_result(*self.args)["status"], "valid")

    def test_empty_result_never_becomes_valid_after_rejected_write(self):
        with self.assertRaises(ResultStoreError):
            result_write(*self.args, expected_revision=0, records=[case_record(malformed_body())])
        work_finish(*self.args, revision=0)
        self.assertEqual(validate_source_first_result(*self.args)["status"], "incomplete")
        self.assertNotEqual(self.progress()["actions"][self.action_id]["status"], "settled")

    def test_risk_v2_diagnostics_stay_hints_with_the_case_contract(self):
        self.prepare_graph("risk-analysis")
        written = result_write(*self.args, expected_revision=0, records=[
            case_record(),
            {"kind": "risk", "body": {"format_version": "behavior-risk-v2", "title": "原 worker 待核实的风险"}},
            {"kind": "check", "body": {"signal_ids": ["unconfirmed-signal"]}},
        ])
        self.assertTrue({"risk_contract_hint", "check_contract_hint", "evidence_quote_mismatch"}
                        <= {item["kind"] for item in written["warnings"]})
        finished = work_finish(*self.args, revision=written["revision"])
        self.assertEqual(finished["status"], "valid")
        self.assertEqual(len(read_result(self.path).records), 3)
        self.assertEqual(read_result(self.path).records[1].body["title"], "原 worker 待核实的风险")


if __name__ == "__main__":
    unittest.main(verbosity=2)
