"""Read-only tool selection, preview parity, isolation and frozen Worker access."""
from pathlib import Path
import tempfile
import shutil
from unittest.mock import patch
import unittest

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.run_module_analysis import derive_run, run_module_analysis, resume_module_analysis
from pangea_agent.cli.public_api import stop_run
from pangea_agent.cli.source_first_api import plan_write, source_read, work_finish
from pangea_agent.inventory.source_access import expand_owned_files
from pangea_agent.inventory.test_tool_sources import preview_test_tools


class TestToolSources(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pangea-tool-source-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.put("product", "src/main.c", "int main(void) { return 0; }\n")
        self.put("tools", "src/main.c", "int TOOL_ONLY(void) { return 9; }\n")
        self.put("tools", "scripts/probe.py", "print('actual probe command --port 1234')\n")
        self.put("tools", "scripts/run.sh", "python probe.py --timeout 2\n")
        self.put("tools", "scripts/README.md", "Observation: exit 0 means reachable\n")

    def put(self, repo, name, content):
        path = self.root / "repositories" / repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def create(self, sources=None, revision=None):
        contract = {"data_root": str(self.root), "repository": "product", "target": "Product behavior",
                    "source_scope": ["src"], "analysis_profile": "behavior-test-v2",
                    "analysis_settings": {"scenario": "module-analysis", "mode": "speed"}}
        if sources is not None:
            contract["test_tool_sources"] = sources
        if revision:
            contract["expected_test_tool_revision"] = revision
        request = self.root / "request.json"
        write_json(request, contract)
        created = run_module_analysis(str(request))
        run = self.root / "runs" / created["run_id"]
        task = read_json(Path(created["agent_actions"][0]["task_path"]))
        return created, run, task

    def test_cross_repository_frozen_reference_and_original_worker(self):
        sources = [{"repo_id": "tools", "paths": ["src/main.c", "scripts"]}]
        preview = preview_test_tools(str(self.root), sources)
        created, run, task = self.create(sources, preview["scope_revision"])
        self.assertEqual(task["owned_scope_paths"], [{"repo_id": "product", "path": "src/main.c"}])
        self.assertEqual(len(task["test_tool_reference_paths"]), 4)
        self.assertEqual({(f["repo_id"], f["relative_path"]) for f in preview["files"]},
                         {(f["repo_id"], f["path"]) for f in task["test_tool_reference_paths"]})
        manifest = read_json(run / "inputs/source-manifest.json")
        self.assertEqual(manifest["source_scope"], ["src/main.c"])
        frozen_contract = read_json(run / "inputs/task-contract.json")
        self.assertEqual(frozen_contract["repositories"], [])
        index = read_json(run / "inputs/source-index.json")
        _, issues = expand_owned_files({"owned_files": [{"repo_id": "tools", "path": "src/main.c"}]}, index, task["owned_scope_paths"])
        self.assertEqual(issues[0]["reason"], "file_not_in_frozen_owned_scope")
        binding = (str(self.root), created["run_id"], task["action_id"], "planner")
        bind_action(*binding)
        planned = plan_write(*binding, expected_revision=0, unit={"title": "Product", "purpose": "Check product", "owned_files": task["owned_scope_paths"]})
        work_finish(*binding, revision=planned["revision"])
        settle_action(*binding[:3])
        action = next(a for a in read_json(run / "progress.json")["actions"].values() if a["role"] == "analysis")
        worker = read_json(Path(action["task_path"]))
        self.assertEqual(worker["test_tool_reference_paths"], task["test_tool_reference_paths"])
        self.put("tools", "scripts/probe.py", "CHANGED AFTER FREEZE")
        binding = (str(self.root), created["run_id"], action["action_id"], "worker")
        bind_action(*binding)
        self.assertIn("actual probe command", str(source_read(*binding, repo_id="tools", path="scripts/probe.py")))
        self.assertNotIn("CHANGED AFTER FREEZE", str(source_read(*binding, repo_id="tools", path="scripts/probe.py")))

    def test_same_repository_and_empty_legacy_selection(self):
        self.put("product", "tools/probe.sh", "echo ready\n")
        _, _, task = self.create([{"repo_id": "product", "paths": ["tools"]}])
        self.assertEqual(task["test_tool_reference_paths"], [{"repo_id": "product", "path": "tools/probe.sh"}])
        for sources in (None, []):
            _, _, old = self.create(sources)
            self.assertNotIn("test_tool_reference_paths", old)
            self.assertEqual(old["reference_scope_paths"], [])
        self.assertEqual(preview_test_tools(str(self.root), [])["files"], [])

    def test_preview_exclusions_exact_files_and_revision(self):
        file = self.put("tools", "scripts/blob.bin", "")
        file.write_bytes(b"\x00\xff")
        self.put("tools", "scripts/.git/config", "git metadata")
        sources = [{"repo_id": "tools", "paths": ["scripts"]}]
        preview = preview_test_tools(str(self.root), sources)
        self.assertEqual(preview["matched_file_count"], 3)
        self.assertEqual(preview["excluded_file_count"], 2)
        for relative in ("scripts/blob.bin", "missing.py"):
            with self.assertRaisesRegex(ValueError, "TEST_TOOL_(NOT_TEXT|FILE_MISSING)"):
                preview_test_tools(str(self.root), [{"repo_id": "tools", "paths": [relative]}])
        self.put("tools", "scripts/new.py", "new")
        with self.assertRaisesRegex(ValueError, "TEST_TOOL_SCOPE_CHANGED"):
            self.create(sources, preview["scope_revision"])

    def test_resume_and_supplement_never_refresh_tools(self):
        sources = [{"repo_id": "tools", "paths": ["scripts"]}]
        created, run, task = self.create(sources)
        self.put("tools", "scripts/probe.py", "LIVE CHANGED")
        with patch("pangea_agent.inventory.test_tool_sources.preview_test_tools", side_effect=AssertionError("must not refresh tools")):
            resume_module_analysis(created["run_id"], str(self.root))
            stop_run(str(self.root), created["run_id"])
            request = self.root / "derive.json"
            write_json(request, {"mode": "supplement", "instruction": "Check tool observations"})
            child = derive_run(str(self.root), created["run_id"], str(request))
        child_task = read_json(Path(child["agent_actions"][0]["task_path"]))
        self.assertEqual(child_task["test_tool_reference_paths"], task["test_tool_reference_paths"])
        frozen = self.root / "runs" / child["run_id"] / "inputs/source/tools/scripts/probe.py"
        self.assertIn("actual probe command", frozen.read_text())

    def test_changed_files_inherits_frozen_tools_not_live_selection(self):
        self.put("product", "tools/local.py", "original local tool")
        sources = [{"repo_id": "tools", "paths": ["scripts"]},
                   {"repo_id": "product", "paths": ["tools"]}]
        preview = preview_test_tools(str(self.root), sources)
        created, run, task = self.create(sources, preview["scope_revision"])
        stop_run(str(self.root), created["run_id"])
        self.put("product", "src/main.c", "int changed_product(void) { return 7; }")
        self.put("tools", "scripts/probe.py", "modified live tool")
        self.put("tools", "scripts/new.py", "new live tool")
        (self.root / "repositories/product/tools/local.py").unlink()
        request = self.root / "derive.json"
        write_json(request, {"mode": "changed-files", "changed_paths": ["src/main.c"]})
        for remove_repository in (False, True):
            if remove_repository:
                shutil.rmtree(self.root / "repositories/tools")
            with patch("pangea_agent.inventory.test_tool_sources.preview_test_tools", side_effect=AssertionError("must not refresh tools")):
                child = derive_run(str(self.root), created["run_id"], str(request))
            child_task = read_json(Path(child["agent_actions"][0]["task_path"]))
            self.assertEqual(child_task["test_tool_reference_paths"], task["test_tool_reference_paths"])
            frozen = self.root / "runs" / child["run_id"] / "inputs/source"
            self.assertIn("actual probe command", (frozen / "tools/scripts/probe.py").read_text())
            self.assertFalse((frozen / "tools/scripts/new.py").exists())
            self.assertEqual((frozen / "product/tools/local.py").read_text(), "original local tool")
            self.assertIn("changed_product", (frozen / "product/src/main.c").read_text())

    def test_boundaries_and_overlap(self):
        for repo, path in [("../tools", "scripts"), ("missing", "."), ("tools", "../product/src"), ("tools", "/tmp"), ("tools", "C:\\temp"), ("tools", " ")]:
            with self.assertRaises(ValueError):
                preview_test_tools(str(self.root), [{"repo_id": repo, "paths": [path]}])
        link = self.root / "repositories/tools/scripts/escape.py"
        link.symlink_to(self.root / "repositories/product/src/main.c")
        with self.assertRaisesRegex(ValueError, "TEST_TOOL_PATH_INVALID"):
            preview_test_tools(str(self.root), [{"repo_id": "tools", "paths": ["scripts"]}])
        with self.assertRaisesRegex(ValueError, "TEST_TOOL_SOURCE_OVERLAP"):
            self.create([{"repo_id": "product", "paths": ["src/main.c"]}])
        with self.assertRaises(ValueError):
            preview_test_tools(str(self.root), [{"repo_id": "tools", "paths": []}])


if __name__ == "__main__":
    unittest.main()
