"""Run: python verification/coverage_reference_diagnostics.py."""
from pathlib import Path
import tempfile
import unittest

from pangea_agent.agent_io import write_json
from pangea_agent.analysis_scenarios import scene_spec, freeze_scene
from pangea_agent.contract_context import ContractContext
from pangea_agent.coverage_diagnostics import COVERAGE_REFERENCE_CONTRACT, coverage_reference_diagnostics
from pangea_agent.documents.coverage import relevant_zero_coverage
from pangea_agent.graph.result_store import (
    initialize_result, append_records, read_result, current_result_warnings,
    declare_completion, copy_result_for_closure, read_records,
)
from pangea_agent.models.source_first import SourceBinding


def case(refs, purpose="coverage"):
    return {"format_version": "behavior-test-case-v1", "purpose": purpose,
            "coverage_refs": refs, "steps": [{"action": "操作", "expected": "结果"}],
            "external_observations": []}


class CoverageReferenceChecks(unittest.TestCase):
    def test_reference_facts_only(self):
        gaps = [{"coverage_id": "zero"}]
        check = lambda body: coverage_reference_diagnostics("test_case", body, gaps)
        self.assertEqual(check(case([]))[0]["code"], "missing_coverage_reference")
        self.assertEqual(check(case(["positive"]))[0]["code"], "unknown_coverage_reference")
        self.assertEqual(check(case([{}]))[0]["code"], "invalid_reference_shape")
        self.assertEqual(check(case("zero"))[0]["code"], "invalid_reference_shape")
        self.assertEqual(check(case(["zero"])), [])
        self.assertEqual(check(case([{"coverage_id": "zero"}])), [])
        self.assertEqual(check(case([], "branch")), [])
        self.assertEqual(coverage_reference_diagnostics("unit_plan", {"coverage_ids": []}, gaps), [])
        self.assertEqual(coverage_reference_diagnostics("unit_plan", {"coverage_ids": ["unknown"]}, gaps)[0]["code"], "unknown_coverage_reference")

    def test_unavailable_and_ambiguous_context(self):
        for gaps in (None, {}, "bad", [{}], [{"coverage_id": 1}]):
            with self.subTest(gaps=gaps):
                self.assertEqual(coverage_reference_diagnostics("test_case", case(["zero"]), gaps)[0]["code"], "coverage_context_unavailable")
        duplicated = [{"coverage_id": "zero"}, {"coverage_id": "zero"}]
        self.assertEqual(coverage_reference_diagnostics("test_case", case(["zero"]), duplicated)[0]["code"], "duplicate_reference_target")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "runs/run/result.json"
            binding = SourceBinding(data_root=directory, run_id="run", action_id="analysis", task_id="worker")
            initialize_result(path, binding)
            context = ContractContext(directory, "run", {})
            contracts = {"coverage_reference": COVERAGE_REFERENCE_CONTRACT}
            for revision, content in enumerate((None, "not JSON", "{}")):
                if content is not None:
                    (root / "runs/run/inputs").mkdir(exist_ok=True)
                    (root / "runs/run/inputs/coverage-gaps.json").write_text(content)
                response = append_records(path, binding, revision, [{"kind": "test_case", "body": case(["zero"])}], record_contracts=contracts, context=context)
                self.assertEqual(response["warnings"][0]["code"], "coverage_context_unavailable")

    def test_zero_gap_and_zero_branch_in_executed_function(self):
        self.assertEqual(coverage_reference_diagnostics("test_case", case(["zero"]), [])[0]["code"], "unknown_coverage_reference")
        records = [{"coverage_id": "function", "coverage_type": "function", "count": 12},
                   {"coverage_id": "branch", "coverage_type": "branch", "count": 12,
                    "true_count": 12, "false_count": 0}]
        gaps = relevant_zero_coverage({"matched": records})
        self.assertEqual(coverage_reference_diagnostics("test_case", case(["branch"]), gaps), [])
        self.assertEqual(coverage_reference_diagnostics("test_case", case(["function"]), gaps)[0]["code"], "unknown_coverage_reference")

    def test_new_freeze_only(self):
        spec = scene_spec("coverage-analysis")
        self.assertEqual(spec["record_contracts"]["coverage_reference"], COVERAGE_REFERENCE_CONTRACT)
        self.assertNotIn("coverage_reference", scene_spec("branch-analysis")["record_contracts"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = dict(spec)
            old["record_contracts"] = {k: v for k, v in spec["record_contracts"].items() if k != "coverage_reference"}
            write_json(root / "inputs/analysis-scene.json", old)
            frozen = freeze_scene(root, {"analysis_profile": "behavior-test-v2", "analysis_settings": {"scenario": "coverage-analysis"}})
            self.assertNotIn("coverage_reference", frozen["record_contracts"])

    def test_nonblocking_same_worker_correction_and_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "runs/run/result.json"
            write_json(root / "runs/run/inputs/coverage-gaps.json", [{"coverage_id": "zero"}])
            binding = SourceBinding(data_root=str(root), run_id="run", action_id="analysis", task_id="worker")
            initialize_result(path, binding)
            contracts = scene_spec("coverage-analysis")["record_contracts"]
            context = ContractContext(str(root), "run", {"scenario": "coverage-analysis"})
            first = append_records(path, binding, 0, [{"kind": "test_case", "body": case(["unknown"])}], record_contracts=contracts, context=context)
            self.assertEqual(first["warnings"][0]["kind"], "coverage_reference_hint")
            self.assertEqual(read_result(path).records[0].body, case(["unknown"]))
            self.assertEqual(read_records(path, binding)["current_warnings"][0]["code"], "unknown_coverage_reference")
            pending_closure = copy_result_for_closure(read_result(path), binding.model_copy(update={"action_id": "closure"}), path)
            self.assertEqual(len(current_result_warnings(pending_closure)), 1)
            completion = declare_completion(path, binding, first["revision"], complete=True)
            self.assertTrue(completion["complete"])
            corrected = append_records(path, binding, completion["revision"], [{"kind": "test_case", "body": case(["zero"]), "supersedes": first["record_ids"]}], record_contracts=contracts, context=context)
            self.assertEqual(corrected["warnings"], [])
            result = read_result(path)
            self.assertEqual(current_result_warnings(result), [])
            self.assertEqual(read_records(path, binding)["current_warnings"], [])
            self.assertEqual(len(result.warnings), 1)  # historical audit retained
            closed = copy_result_for_closure(result, binding.model_copy(update={"action_id": "closure"}), path)
            self.assertEqual(current_result_warnings(closed), [])
            self.assertTrue(declare_completion(path, binding, corrected["revision"], complete=True)["complete"])

    def test_old_contract_does_not_acquire_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            binding = SourceBinding(data_root=directory, run_id="run", action_id="analysis", task_id="worker")
            initialize_result(path, binding)
            response = append_records(path, binding, 0, [{"kind": "test_case", "body": case([])}], record_contracts={"test_case": "behavior-test-case-v1"})
            self.assertEqual(response["warnings"], [])


if __name__ == "__main__":
    unittest.main()
