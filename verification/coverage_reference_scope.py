"""Real Run/asset/source API contracts: python verification/coverage_reference_scope.py."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.assets import import_asset, prepare_asset_extraction
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.public_api import stop_run
from pangea_agent.cli.run_module_analysis import derive_run, resume_module_analysis, run_module_analysis
from pangea_agent.cli.source_first_api import plan_write, source_read, source_search, work_finish
from pangea_agent.inventory.source_access import SourceAccessError, expand_owned_files


def paths(items):
    return {item["path"] for item in items}


def fingerprint(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class CoverageReferenceScope(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pangea-coverage-reference-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repositories/demo"
        self.sources = {"module/core.c": "int core(void) { return 1; }\n",
                        "api/entry.c": "int entry(void) { return core(); }\n",
                        "unrelated/other.c": "int other(void) { return 2; }\n"}
        for name, content in self.sources.items():
            file = self.repo / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding="utf-8")
        (self.repo / "notes.txt").write_text("Explicit interface notes", encoding="utf-8")

    def create(self, *, scope=None, context=None, query=True, scenario="coverage-analysis",
               profile="behavior-test-v2"):
        combined = self.root / "coverage.json"
        # The legacy combined shape deliberately carries an out-of-query record:
        # source matching may retain it, but ownership must exclude its gap.
        write_json(combined, {"status": "success", "scope": {"requested": "module", "recursive": True} if query else {},
                             "uncovered_functions": [
                                 {"source": "summary", "file_path": "module/core.c", "uncovered_functions": ["core"]},
                                 {"source": "summary", "file_path": "api/entry.c", "uncovered_functions": ["entry"]}]})
        asset = import_asset(str(self.root), str(combined), "coverage")
        prepare_asset_extraction(str(self.root), asset.asset_id)
        request = self.root / "request.json"
        write_json(request, {"data_root": str(self.root), "repository": "demo", "target": "module behavior",
                             "source_scope": scope or ["module", "api", "unrelated"], "context_scope": context or [],
                             "analysis_profile": profile, "analysis_settings": {"scenario": scenario, "mode": "speed"},
                             "asset_ids": [asset.asset_id]})
        created = run_module_analysis(str(request))
        run = self.root / "runs" / created["run_id"]
        task = read_json(Path(created["agent_actions"][0]["task_path"]))
        return created["run_id"], run, task

    def test_query_preserves_authorized_entry_as_readable_reference_not_owned_gap(self):
        run_id, run, task = self.create()
        self.assertEqual(paths(task["owned_scope_paths"]), {"module/core.c"})
        self.assertEqual(paths(task["reference_scope_paths"]), {"api/entry.c", "unrelated/other.c"})
        self.assertEqual(paths(task["allowed_paths"]), set(self.sources))
        self.assertEqual(task["context_budget"], {"policy": "on-demand-v1"})
        manifest = read_json(run / "inputs/source-manifest.json")
        self.assertEqual(manifest["source_scope"], ["module/core.c"])
        self.assertEqual({g["path"] for g in read_json(run / "inputs/coverage-gaps.json")}, {"module/core.c"})
        self.assertEqual({g["path"] for g in read_json(run / "inputs/coverage-match-summary.json")["matched"]},
                         {"module/core.c", "api/entry.c"})
        for relative in self.sources:
            self.assertEqual((run / "inputs/source/demo" / relative).read_text(), self.sources[relative])
        index = read_json(run / "inputs/source-index.json")
        _, issues = expand_owned_files({"owned_files": [{"repo_id": "demo", "path": "api/entry.c"}]},
                                       index, task["owned_scope_paths"])
        self.assertEqual(issues[0]["reason"], "file_not_in_frozen_owned_scope")
        binding = (str(self.root), run_id, task["action_id"], "planner")
        bind_action(*binding)
        self.assertIn("return core()", str(source_read(*binding, repo_id="demo", path="api/entry.c")))
        self.assertIn("api/entry.c", str(source_search(*binding, query="return core")))
        planned = plan_write(*binding, expected_revision=0, unit={
            "title": "Module", "purpose": "Trace entry to module", "owned_files": [{"repo_id": "demo", "path": "module/core.c"}],
            "context_files": ["demo:api/entry.c"]})
        work_finish(*binding, revision=planned["revision"])
        settle_action(*binding[:3])
        actions = read_json(run / "progress.json")["actions"].values()
        analysis = next(a for a in actions if a["role"] == "analysis")
        worker = read_json(Path(analysis["task_path"]))
        self.assertEqual(paths(worker["context_files"]), {"api/entry.c"})
        self.assertEqual(worker["unresolved_context_files"], [])
        self.assertIn("api/entry.c", paths(worker["allowed_paths"]))
        worker_binding = (str(self.root), run_id, analysis["action_id"], "worker")
        bind_action(*worker_binding)
        self.assertIn("return core()", str(source_read(*worker_binding, repo_id="demo", path="api/entry.c")))

    def test_explicit_context_is_unioned_without_duplicates(self):
        _, run, task = self.create(context=["api/entry.c", "notes.txt"])
        self.assertEqual(paths(task["reference_scope_paths"]), {"api/entry.c", "unrelated/other.c", "notes.txt"})
        expansion = read_json(run / "inputs/source-manifest.json")["scope_expansion"]
        self.assertEqual(len(expansion["context_files"]), 3)
        self.assertEqual(len(expansion["groups"][0]["context_paths"]), 3)
        self.assertTrue((run / "inputs/source/demo/notes.txt").is_file())

    def test_read_boundary_never_expands_beyond_source_and_explicit_context(self):
        run_id, run, task = self.create(scope=["module"], context=["api/entry.c"])
        self.assertEqual(paths(task["allowed_paths"]), {"module/core.c", "api/entry.c"})
        self.assertFalse((run / "inputs/source/demo/unrelated/other.c").exists())
        binding = (str(self.root), run_id, task["action_id"], "planner")
        bind_action(*binding)
        with self.assertRaises(SourceAccessError):
            source_read(*binding, repo_id="demo", path="unrelated/other.c")
        with self.assertRaises(SourceAccessError):
            source_read(*binding, repo_id="demo", path="../outside.c")

    def test_narrow_source_without_context_does_not_discover_caller(self):
        _, run, task = self.create(scope=["module"])
        self.assertEqual(paths(task["allowed_paths"]), {"module/core.c"})
        self.assertEqual(task["reference_scope_paths"], [])
        self.assertFalse((run / "inputs/source/demo/api/entry.c").exists())

    def test_reference_only_scope_cannot_be_promoted_to_coverage_ownership(self):
        with self.assertRaisesRegex(ValueError, "用户指定范围没有可分析"):
            self.create(scope=["api"])

    def test_no_query_scope_keeps_original_ownership(self):
        _, _, task = self.create(query=False)
        self.assertEqual(paths(task["owned_scope_paths"]), set(self.sources))
        self.assertEqual(task["reference_scope_paths"], [])

    def test_noncoverage_scenarios_and_legacy_profile_keep_original_ownership(self):
        for profile, scenario in (("behavior-test-v2", "module-analysis"), ("behavior-test-v2", "branch-analysis"),
                                  ("behavior-test-v2", "risk-analysis"), ("behavior-test-v1", "module-analysis")):
            with self.subTest(profile=profile, scenario=scenario):
                _, _, task = self.create(profile=profile, scenario=scenario)
                self.assertEqual(paths(task["owned_scope_paths"]), set(self.sources))
                self.assertEqual(task["reference_scope_paths"], [])

    def test_incremental_uses_parent_reference_boundary_and_never_requeries_assets(self):
        parent_id, parent, task = self.create(scope=["module", "api"])
        stop_run(str(self.root), parent_id)
        before = fingerprint(parent)
        (self.repo / "api/entry.c").write_text("int entry(void) { return 99; }\n")
        for mode in ("supplement", "changed-files"):
            with self.subTest(mode=mode):
                request = self.root / "derive.json"
                write_json(request, {"mode": mode, "instruction": "Only module changes",
                                     **({"changed_paths": ["module/core.c"]} if mode == "changed-files" else {})})
                with patch("pangea_agent.graph.nodes.source_first.analysis_asset_inputs", side_effect=AssertionError("must not refresh assets")):
                    child = derive_run(str(self.root), parent_id, str(request))
                run = self.root / "runs" / child["run_id"]
                child_task = read_json(Path(child["agent_actions"][0]["task_path"]))
                # Supplement preserves the parent split. Existing changed-files
                # semantics use the original contract source_scope without applying
                # query filtering again; this fix must not silently alter that mode.
                expected_owned = {"module/core.c"} if mode == "supplement" else {"module/core.c", "api/entry.c"}
                expected_reference = {"api/entry.c"} if mode == "supplement" else set()
                self.assertEqual(paths(child_task["owned_scope_paths"]), expected_owned)
                self.assertEqual(paths(child_task["reference_scope_paths"]), expected_reference)
                self.assertNotIn("unrelated/other.c", paths(child_task["allowed_paths"]))
                expected = self.sources["api/entry.c"] if mode == "supplement" else (self.repo / "api/entry.c").read_text()
                self.assertEqual((run / "inputs/source/demo/api/entry.c").read_text(), expected)
                self.assertEqual(read_json(run / "inputs/coverage-gaps.json"), read_json(parent / "inputs/coverage-gaps.json"))
        self.assertEqual(fingerprint(parent), before)

    def test_resume_keeps_frozen_reference_and_scope_bytes(self):
        run_id, run, _ = self.create()
        before = fingerprint(run / "inputs")
        (self.repo / "api/entry.c").write_text("live changes must not leak\n")
        with patch("pangea_agent.graph.nodes.source_first.analysis_asset_inputs", side_effect=AssertionError("must not refresh assets")):
            resume_module_analysis(run_id, str(self.root))
        self.assertEqual(fingerprint(run / "inputs"), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
