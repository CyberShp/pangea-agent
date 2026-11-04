"""Real Graph/CLI regressions for bounded Planning transport (no model calls)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import bind_action
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.cli.source_first_api import plan_write, plan_candidates, task_open
from pangea_agent.inventory.planning_io import read_batch
from pangea_agent.inventory.source_access import SourceAccessError, input_read, source_read


class PlanningIO(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="planning-io-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        repo = self.root / "repositories/demo/src"
        repo.mkdir(parents=True)
        for name in ("one", "two", "three", "four", "five"):
            (repo / f"{name}.c").write_text(f"// {name} 中文\nint {name}(void) {{ return 1; }}\n", encoding="utf-8")
        (repo / "large.c").write_text("\n".join(f"int large_{i}(void) {{ return {i}; }}" for i in range(400)), encoding="utf-8")
        request = self.root / "request.json"
        write_json(request, {"data_root": str(self.root), "repository": "demo", "target": "planning-io",
                             "source_scope": ["src"], "analysis_profile": "behavior-test-v2",
                             "analysis_settings": {"scenario": "module-analysis", "mode": "speed"}})
        created = run_module_analysis(str(request))
        self.run_id = created["run_id"]
        self.action = created["agent_actions"][0]["action_id"]
        self.run = self.root / "runs" / self.run_id
        progress = read_json(self.run / "progress.json")
        self.task_path = Path(progress["actions"][self.action]["task_path"])
        self.task = read_json(self.task_path)
        bind_action(str(self.root), self.run_id, self.action, "planner")
        self.binding = (str(self.root), self.run_id, self.action, "planner")

    def cli(self, command, *arguments):
        args = [sys.executable, "-m", "pangea_agent.cli.main", command,
                "--data-root", str(self.root), "--run-id", self.run_id,
                "--action-id", self.action, "--task-id", "planner", *arguments]
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        output = subprocess.run(args, text=True, capture_output=True, env=env, timeout=45)
        self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
        return json.loads(output.stdout)["result"]

    def unit(self, name):
        return {"title": name, "purpose": "主责与待核实边界", "owned_files": [{"repo_id": "demo", "path": f"src/{name}.c"}]}

    def test_startup_delivers_literal_rules_and_revision_without_changing_task(self):
        before = self.task_path.read_bytes()
        opened = task_open(*self.binding)
        self.assertEqual(opened["write_contract"]["revision"], 0)
        self.assertIn("plan-write-batch", opened["write_contract"]["capabilities"])
        startup = opened["planning_startup"]
        self.assertLessEqual(len(json.dumps(startup, ensure_ascii=False, indent=2)), 18000)
        pages = {page["input_id"]: page for page in startup["pages"]}
        for name in ("analysis_scene", "unit_planning_rubric", "scene_planning_rubric", "planning_metadata"):
            self.assertIn(name, pages)
            page = pages[name]
            original = input_read(*self.binding, input_id=name, max_chars=24000)
            self.assertTrue(original["text"].startswith(page["text"]))
        self.assertNotIn("coverage_gaps", pages)
        self.assertEqual(before, self.task_path.read_bytes())

    def test_large_startup_rule_can_be_restored_without_silent_truncation(self):
        item = next(i for i in self.task["inputs"] if i["input_id"] == "unit_planning_rubric")
        content = "规划证据\n\t\\引用\"" * 8000
        Path(item["path"]).write_text(content, encoding="utf-8")
        startup = task_open(*self.binding)["planning_startup"]
        self.assertFalse(startup["complete"])
        self.assertLessEqual(len(json.dumps(startup, ensure_ascii=False, indent=2)), 18000)
        page = next(p for p in startup["pages"] if p["input_id"] == "unit_planning_rubric")
        restored, cursor = page["text"], page["next_cursor"]
        self.assertIsNotNone(cursor)
        while cursor is not None:
            page = input_read(*self.binding, input_id="unit_planning_rubric", cursor=cursor)
            restored += page["text"]
            cursor = page["next_cursor"]
        self.assertEqual(restored, content)

    def test_legacy_contract_stays_legacy(self):
        self.task.pop("planning_io_policy")
        write_json(self.task_path, self.task)
        before = self.task_path.read_bytes()
        opened = task_open(*self.binding)
        self.assertNotIn("planning_startup", opened)
        self.assertNotIn("capabilities", opened["write_contract"])
        response = plan_write(*self.binding, expected_revision=0, unit=self.unit("one"))
        self.assertIn("unassigned_owned_regions", response["diagnostics"])
        self.assertEqual(before, self.task_path.read_bytes())

    def test_batch_equals_independent_source_reads(self):
        requests = [{"operation": "source-read", "repo_id": "demo", "path": f"src/{name}.c"}
                    for name in ("one", "two", "three")]
        response = read_batch(*self.binding, requests=requests)
        self.assertIsNone(response["next_request_index"])
        self.assertTrue(response["all_succeeded"])
        for item, request in zip(response["items"], requests):
            original = source_read(*self.binding, repo_id="demo", path=request["path"], view="text")
            self.assertEqual(item["result"], original)
        self.assertLessEqual(len(json.dumps(response, ensure_ascii=False, indent=2)), 24000)

    def test_batch_budget_preserves_unexecuted_requests_and_input_cursors(self):
        requests = [{"operation": "input-read", "input_id": "unit_planning_rubric"} for _ in range(8)]
        start, visited = 0, []
        while start is not None:
            response = read_batch(*self.binding, requests=requests, start_index=start, max_chars=4000)
            self.assertLessEqual(len(json.dumps(response, ensure_ascii=False, indent=2)), 4000)
            self.assertTrue(response["items"])
            for item in response["items"]:
                self.assertTrue(item["ok"], item)
                visited.append(item["request_index"])
                page = item["result"]
                text, cursor = page["text"], page["next_cursor"]
                while cursor is not None:
                    page = input_read(*self.binding, input_id="unit_planning_rubric", cursor=cursor)
                    text += page["text"]
                    cursor = page["next_cursor"]
                original = next(i for i in self.task["inputs"] if i["input_id"] == "unit_planning_rubric")
                self.assertEqual(text, Path(original["path"]).read_text(encoding="utf-8"))
            start = response["next_request_index"]
        self.assertEqual(visited, list(range(8)))

    def test_batch_rejects_budget_too_small_for_long_binding_instead_of_empty_page(self):
        request = read_json(self.root / "request.json")
        request.pop("run_id")
        request["target"] = "long-binding"
        request_path = self.root / "long-binding.json"
        write_json(request_path, request)
        created = run_module_analysis(str(request_path))
        action = created["agent_actions"][0]["action_id"]
        task_id = "planner-" + "x" * 1600
        bind_action(str(self.root), created["run_id"], action, task_id)
        binding = (str(self.root), created["run_id"], action, task_id)
        requests = [{"operation": "input-read", "input_id": "analysis_scene"}]
        with self.assertRaisesRegex(SourceAccessError, "绑定元数据.*max_chars"):
            read_batch(*binding, requests=requests, max_chars=3000)
        response = read_batch(*binding, requests=requests, max_chars=12000)
        self.assertTrue(response["all_succeeded"])
        self.assertEqual([item["request_index"] for item in response["items"]], [0])
        self.assertIsNone(response["next_request_index"])

    def test_errors_and_authority_cannot_be_hidden_by_batch(self):
        with self.assertRaises(SourceAccessError):
            read_batch(*self.binding, requests=[{"operation": "plan-write"}])
        with self.assertRaises(SourceAccessError):
            read_batch(*self.binding, requests=[{"operation": "input-read", "input_id": "analysis_scene", "task_id": "other"}])
        with self.assertRaises(SourceAccessError):
            read_batch(*self.binding[:3], "other", requests=[{"operation": "input-read", "input_id": "analysis_scene"}])
        response = read_batch(*self.binding, requests=[
            {"operation": "source-read", "repo_id": "demo", "path": "../secret.c"},
            {"operation": "input-read", "input_id": "analysis_scene"},
        ])
        self.assertFalse(response["all_succeeded"])
        self.assertFalse(response["items"][0]["ok"])
        self.assertTrue(response["items"][1]["ok"])

    def test_nested_source_pages_respect_whole_readable_output_budget(self):
        requests = [{"operation": "source-read", "repo_id": "demo", "path": "src/large.c", "view": "compact"}
                    for _ in range(3)]
        result = read_batch(*self.binding, requests=requests, max_chars=12000)
        self.assertTrue(result["items"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, indent=2)), 12000)
        for item in result["items"]:
            self.assertTrue(item["ok"], item)
            self.assertIsNotNone(item["result"]["next_page_token"])

    def test_candidates_are_optional_and_paged_without_changing_ready(self):
        response = plan_write(*self.binding, expected_revision=0, unit=self.unit("one"))
        diagnostics = response["diagnostics"]
        self.assertTrue(diagnostics["ready"])
        self.assertNotIn("unassigned_owned_regions", diagnostics)
        self.assertGreater(diagnostics["unassigned_owned_region_count"], 0)
        candidates = plan_candidates(*self.binding)
        self.assertEqual(candidates["candidate_count"], diagnostics["unassigned_owned_region_count"])
        self.assertEqual(len(candidates["items"]), candidates["candidate_count"])

    def test_candidate_cursor_rejects_inventory_changes_without_result_revision(self):
        from pangea_agent.inventory.source_access import source_index

        root = self.root / "candidate-pagination"
        repo = root / "repositories/demo/src"
        repo.mkdir(parents=True)
        for index in range(30):
            (repo / f"file{index}.c").write_text("int entry(void) { return 1; }\n", encoding="utf-8")
        write_json(root / "request.json", {
            "data_root": str(root), "repository": "demo", "target": "candidate-pagination",
            "source_scope": ["src"], "analysis_profile": "behavior-test-v2",
            "analysis_settings": {"scenario": "module-analysis", "mode": "speed"},
        })
        created = run_module_analysis(str(root / "request.json"))
        action = created["agent_actions"][0]["action_id"]
        bind_action(str(root), created["run_id"], action, "planner")
        binding = (str(root), created["run_id"], action, "planner")
        first = plan_candidates(*binding, max_chars=1000)
        self.assertIsNotNone(first["next_page_token"])
        for index in range(20):
            source_index(*binding, repo_id="demo", path=f"src/file{index}.c", view="compact")
        refreshed = plan_candidates(*binding, max_chars=1000)
        self.assertEqual(first["revision"], refreshed["revision"])
        self.assertNotEqual(first["candidate_count"], refreshed["candidate_count"])
        with self.assertRaisesRegex(ValueError, "读取条件"):
            plan_candidates(*binding, page_token=first["next_page_token"], max_chars=1000)
        values, page = [], refreshed
        while True:
            values.extend(item["region_id"] for item in page["items"])
            if page["next_page_token"] is None:
                break
            page = plan_candidates(*binding, page_token=page["next_page_token"], max_chars=1000)
        self.assertEqual(len(set(values)), refreshed["candidate_count"])

    def test_cli_batch_five_units_and_retry(self):
        units = self.root / "units.json"
        write_json(units, [self.unit(name) for name in ("one", "two", "three", "four", "five")])
        args = ("--expected-revision", "0", "--units-file", str(units), "--request-id", "five-units")
        saved = self.cli("plan-write-batch", *args)
        self.assertEqual(len(saved["units"]), 5)
        self.assertEqual(saved["revision"], 1)
        replayed = self.cli("plan-write-batch", *args)
        self.assertEqual(replayed["units"], saved["units"])
        self.assertEqual(replayed["revision"], 1)
        self.assertEqual(self.cli("task-open")["write_contract"]["revision"], 1)
        reads = self.root / "reads.json"
        write_json(reads, [{"operation": "source-read", "repo_id": "demo", "path": "src/one.c"}])
        response = self.cli("read-batch", "--requests-file", str(reads))
        self.assertTrue(response["items"][0]["ok"])
        coverage = self.cli("coverage-read", "--view", "summary")
        self.assertIn("items", coverage)
        self.assertIn("candidate_count", self.cli("plan-candidates"))


if __name__ == "__main__":
    unittest.main()
