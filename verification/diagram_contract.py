"""Shared fixtures + same-worker diagnostic lifecycle; no real model required.

Run with this worktree's src on PYTHONPATH. Optional --js-root points at the
companion plugin directory to compare complete Python/JS diagnostic payloads.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pangea_agent.diagram_contract import DIAGRAM_CONTRACT, diagram_diagnostics
from pangea_agent.flow_schema import uses_declared_main_paths
from pangea_agent.graph.result_store import (
    ResultStoreError, active_diagram_diagnostics, active_records, append_records,
    copy_result_for_closure, declare_completion, initialize_result, read_records,
    read_records_compact, read_result, repair_result_shell,
)
from pangea_agent.models.source_first import SourceBinding

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "diagram-contract-v1.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CONTRACTS = {"diagram": DIAGRAM_CONTRACT}


def candidate(case):
    body = deepcopy(FIXTURE["base"])
    for path, value in case["patch"]:
        keys = path.split(".")
        parent = body
        for key in keys[:-1]:
            parent = parent[int(key)] if isinstance(parent, list) else parent[key]
        if isinstance(parent, list):
            parent[int(keys[-1])] = deepcopy(value)
        else:
            parent[keys[-1]] = deepcopy(value)
    return body


class DiagramContractChecks(unittest.TestCase):
    def test_shared_fixtures(self):
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                body = candidate(case)
                original = deepcopy(body)
                diagnostics = diagram_diagnostics(body, strict=case["strict"])
                self.assertEqual([[item[key] for key in ("diagram", "field", "code", "classification")]
                                  for item in diagnostics], case["expected"])
                self.assertEqual(body, original)

    def test_write_preserves_all_facts_and_same_worker_repairs_only_own_result(self):
        with tempfile.TemporaryDirectory(prefix="pangea-diagram-") as folder:
            path = Path(folder) / "result.json"
            binding = SourceBinding(data_root=folder, run_id="run", action_id="run:analysis:mq", task_id="original-worker")
            initialize_result(path, binding)
            broken = deepcopy(FIXTURE["base"])
            broken["call_graph"]["variables"][0]["writes"] = ["result", "queue.count"]
            first = append_records(path, binding, 0, [{"kind": "flow", "body": broken}], record_contracts=CONTRACTS)
            saved = read_result(path)
            self.assertEqual(saved.records[0].body, broken)
            self.assertEqual(len(saved.records[0].body["sequence"]["messages"]), 4)
            self.assertEqual(saved.records[0].body["call_graph"]["variables"][0]["writes"], ["result", "queue.count"])
            self.assertIsNone(saved.completion)
            self.assertEqual(first["diagram_repair_hint"]["task_id"], binding.task_id)
            self.assertEqual(first["diagram_repair_hint"]["result_path"], str(path.resolve()))
            warning = first["diagram_diagnostics"][0]
            self.assertEqual((warning["record_id"], warning["record_revision"]), (first["record_ids"][0], 1))
            self.assertEqual(warning["field"], "call_graph.variables[0].writes")
            self.assertTrue(read_records(path, binding)["diagram_diagnostics"])
            self.assertTrue(any(item["item_type"] == "warning" for item in read_records_compact(path, binding)["items"]))
            other = binding.model_copy(update={"task_id": "replacement-worker"})
            with self.assertRaises(ResultStoreError):
                append_records(path, other, 1, [{"kind": "note", "body": "cannot write"}])
            self.assertEqual(read_result(path).revision, 1)
            second = append_records(path, binding, 1, [{"kind": "flow", "body": deepcopy(FIXTURE["base"]), "supersedes": first["record_ids"]}], record_contracts=CONTRACTS)
            self.assertEqual(second["diagram_diagnostics"], [])
            self.assertEqual(active_diagram_diagnostics(read_result(path)), [])
            self.assertTrue(read_result(path).warnings)  # historical evidence remains
            self.assertEqual(len(active_records(read_result(path))), 1)
            self.assertIsNone(read_result(path).completion)
            self.assertFalse(declare_completion(path, binding, 2, complete=False)["complete"])
            self.assertFalse(read_result(path).completion.complete)
            self.assertTrue(declare_completion(path, binding, 3, complete=True)["complete"])

    def test_ignored_hint_survives_exact_closure_copy_until_its_record_is_repaired(self):
        with tempfile.TemporaryDirectory(prefix="pangea-diagram-closure-") as folder:
            original_path, closure_path = Path(folder) / "analysis.json", Path(folder) / "closure.json"
            binding = SourceBinding(data_root=folder, run_id="run", action_id="run:analysis:mq", task_id="original-worker")
            initialize_result(original_path, binding)
            first_body = deepcopy(FIXTURE["base"])
            first_body["sequence"]["messages"][0]["kind"] = "unsupported"
            second_body = deepcopy(FIXTURE["base"])
            second_body["call_graph"]["variables"][0]["writes"] = 23
            append_records(original_path, binding, 0, [{"kind": "flow", "body": first_body}, {"kind": "flow", "body": second_body}], record_contracts=CONTRACTS)
            declare_completion(original_path, binding, 1, complete=True)
            original = read_result(original_path)
            closure_binding = binding.model_copy(update={"action_id": "run:closure:mq"})
            closure = copy_result_for_closure(original, closure_binding, closure_path)
            closure_path.write_text(json.dumps(closure.model_dump(mode="json")), encoding="utf-8")
            self.assertIsNone(closure.completion)
            self.assertEqual(len(active_diagram_diagnostics(closure)), 2)
            for warning in active_diagram_diagnostics(closure):
                self.assertEqual(warning["action_id"], closure_binding.action_id)
                self.assertEqual(warning["result_path"], str(closure_path.resolve()))
                self.assertEqual(warning["source_diagnostic"]["action_id"], binding.action_id)
            response = append_records(closure_path, closure_binding, 2,
                [{"kind": "flow", "body": deepcopy(FIXTURE["base"]), "supersedes": ["rec-000001"]}], record_contracts=CONTRACTS)
            self.assertEqual([item["field"] for item in response["diagram_diagnostics"]], ["call_graph.variables[0].writes"])
            self.assertEqual(response["diagram_diagnostics"][0]["record_id"], "rec-000002")
            self.assertEqual(len(active_diagram_diagnostics(read_result(original_path))), 2)
            self.assertEqual(len(active_records(read_result(closure_path))), 2)
            self.assertEqual(read_result(closure_path).binding.task_id, binding.task_id)
            with self.assertRaises(ResultStoreError):
                copy_result_for_closure(original, binding.model_copy(update={"task_id": "other"}), closure_path)

    def test_old_contract_has_no_retroactive_diagnostic_and_explicit_main_paths_are_respected(self):
        body = deepcopy(FIXTURE["base"])
        body["main_path_ids"] = []
        self.assertTrue(uses_declared_main_paths(body))
        del body["main_path_ids"]
        self.assertFalse(uses_declared_main_paths(body))
        with tempfile.TemporaryDirectory(prefix="pangea-diagram-old-") as folder:
            path = Path(folder) / "result.json"
            binding = SourceBinding(data_root=folder, run_id="run", action_id="analysis", task_id="worker")
            initialize_result(path, binding)
            body["call_graph"]["variables"][0]["writes"] = ["result"]
            response = append_records(path, binding, 0, [{"kind": "flow", "body": body}])
            self.assertEqual(response["warnings"], [])
            self.assertEqual(response["diagram_diagnostics"], [])
            path.write_text("broken JSON", encoding="utf-8")
            with self.assertRaises(ResultStoreError):
                read_result(path)
            with self.assertRaises(ResultStoreError):
                append_records(path, binding, 1, [{"kind": "note", "body": "not lost"}])
            self.assertEqual(path.read_text(encoding="utf-8"), "broken JSON")

    def test_real_graph_review_and_closure_preserve_ignored_diagnostics(self):
        import observability_recovery as recovery
        from pangea_agent.agent_io import write_json
        from pangea_agent.cli.adapter_api import settle_action
        from pangea_agent.cli.source_first_api import (
            comparison_finding_write, comparison_read, result_write, review_decide,
            validate_source_first_result, work_finish,
        )
        harness = recovery.RecoveryChecks(methodName="test_plan_counts_saved_progress_and_polling_are_facts")
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        first = harness.analysis[0]
        worker = harness.bind(first)
        task = harness.task(first)
        task["record_contracts"] = CONTRACTS
        write_json(Path(harness.progress()["actions"][first]["task_path"]), task)
        body = deepcopy(FIXTURE["base"])
        body["call_graph"]["variables"][0]["writes"] = 23
        response = result_write(str(harness.root), harness.run_id, first, worker, expected_revision=0,
                                records=[{"kind": "flow", "body": body}, {"kind": "flow", "body": body}])
        self.assertEqual(validate_source_first_result(str(harness.root), harness.run_id, first, worker)["status"], "incomplete")
        work_finish(str(harness.root), harness.run_id, first, worker, revision=response["revision"])
        settle_action(str(harness.root), harness.run_id, first)
        self.assertIn(harness.progress()["actions"][first]["status"], {"settled", "accepted"})
        second = harness.analysis[1]
        harness.write(second, harness.bind(second))
        review = next(action["action_id"] for action in harness.progress()["actions"].values() if action["role"] == "review")
        reviewer = harness.bind(review, "reviewer")
        comparison = comparison_read(str(harness.root), harness.run_id, review, reviewer,
                                     version_set_id=harness.task(review)["version_set_id"], view="compact", max_chars=24000)
        self.assertEqual(len([item for item in comparison["items"] if item["item_type"] == "warning"
                              and item["value"].get("kind") == "diagram_format_diagnostic"]), 2)
        legacy_comparison = comparison_read(str(harness.root), harness.run_id, review, reviewer,
                                            version_set_id=harness.task(review)["version_set_id"])
        self.assertEqual(len(legacy_comparison["entries"][0]["diagram_diagnostics"]), 2)
        finding = comparison_finding_write(str(harness.root), harness.run_id, review, reviewer, expected_revision=0,
            unit_ids=["unit-0001"], finding={"summary": "请确认格式字段", "body": "原 worker 修正 writes 字段，保留其他证据。"})
        decision = review_decide(str(harness.root), harness.run_id, review, reviewer, expected_revision=finding["revision"],
            decision={"disposition": "finding", "version_set_id": harness.task(review)["version_set_id"], "correction_record_ids": finding["record_ids"]})
        work_finish(str(harness.root), harness.run_id, review, reviewer, revision=decision["revision"])
        settle_action(str(harness.root), harness.run_id, review)
        closure = next(action for action in harness.progress()["actions"].values() if action["role"] == "closure")
        self.assertEqual(closure["task_id"], worker)
        task = harness.task(closure["action_id"])
        saved = read_result(task["result_path"])
        self.assertEqual(len(active_diagram_diagnostics(saved)), 2)
        self.assertIsNone(saved.completion)
        harness.bind(closure["action_id"], worker)
        fixed = result_write(str(harness.root), harness.run_id, closure["action_id"], worker,
            expected_revision=saved.revision, records=[{"kind": "flow", "body": deepcopy(FIXTURE["base"]), "supersedes": ["rec-000001"]}])
        self.assertEqual([item["record_id"] for item in fixed["diagram_diagnostics"]], ["rec-000002"])
        self.assertEqual(fixed["diagram_diagnostics"][0]["result_path"], str(Path(task["result_path"]).resolve()))
        current = validate_source_first_result(str(harness.root), harness.run_id, closure["action_id"], worker)
        self.assertEqual(current["status"], "incomplete")
        self.assertEqual([item["record_id"] for item in current["warnings"] if item.get("kind") == "diagram_format_diagnostic"], ["rec-000002"])
        from pangea_agent.report.source_first import diagram_diagnostic_markdown
        report = "\n".join(diagram_diagnostic_markdown([(dict(closure, task=task), read_result(task["result_path"]))]))
        self.assertIn('"record_id": "rec-000002"', report)
        self.assertNotIn('"record_id": "rec-000001"', report)
        self.assertIn("call_graph.variables[0].writes", report)


def compare_js(plugin_root):
    script = """import { readFileSync } from 'node:fs';
const { diagramDiagnostics } = await import(process.argv[1]);
const fixture = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const outputs = fixture.cases.map(c => {const body = structuredClone(fixture.base);
for (const [path,value] of c.patch) {const keys=path.split('.'); let row=body; for (const key of keys.slice(0,-1)) row=row[key]; row[keys.at(-1)]=value;}
return diagramDiagnostics(body,{strict:c.strict});}); process.stdout.write(JSON.stringify(outputs));"""
    output = subprocess.check_output(["node", "--input-type=module", "-e", script,
                                      (Path(plugin_root) / "src" / "diagram-contract.js").resolve().as_uri(), str(FIXTURE_PATH)], text=True, encoding="utf-8")
    expected = [diagram_diagnostics(candidate(case), strict=case["strict"]) for case in FIXTURE["cases"]]
    assert json.loads(output) == expected, "Python/JavaScript full diagnostic payloads differ"
    assert FIXTURE_PATH.read_bytes() == (Path(plugin_root) / "tests" / "fixtures" / FIXTURE_PATH.name).read_bytes()
    print(f"Python/JS payload parity: {len(expected)} fixtures; copied fixtures byte-identical")


if __name__ == "__main__":
    if "--js-root" in sys.argv:
        position = sys.argv.index("--js-root")
        compare_js(sys.argv[position + 1])
        del sys.argv[position:position + 2]
    unittest.main(verbosity=2)
