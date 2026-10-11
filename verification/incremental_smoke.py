"""Run with `python verification/incremental_smoke.py` after installing this project.

Uses real source freezing, Graph creation/resume and task input permissions. No LLM.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.run_module_analysis import derive_run, resume_module_analysis, run_module_analysis
from pangea_agent.cli.public_api import stop_run, run_detail
from pangea_agent.cli.adapter_api import bind_action
from pangea_agent.cli.source_first_api import input_read
from pangea_agent.documents.incremental import derivation_options
from pangea_agent.graph.nodes.source_first import _make_analysis_actions, _prepare_review
from pangea_agent.graph.result_store import read_result
from pangea_agent.graph.workflow_store import load_progress


def create_parent(root: Path) -> tuple[str, Path]:
    repo = root / "repositories" / "demo"
    repo.mkdir(parents=True)
    for name, value in (("changed.c", 1), ("deleted.c", 2), ("stable.c", 3)):
        (repo / name).write_text(f"int {name.split('.')[0]}(void) {{ return {value}; }}\n", encoding="utf-8")
    request = root / "parent-request.json"
    write_json(request, {"data_root": str(root), "repository": "demo", "target": "demo module",
                         "source_scope": ["."], "analysis_profile": "behavior-test-v2",
                         "analysis_settings": {"scenario": "branch-analysis", "mode": "depth"}})
    created = run_module_analysis(str(request))
    run_id = created["run_id"]
    run = root / "runs" / run_id
    stop_run(str(root), run_id)
    plan = read_json(run / "inputs/source-first-plan.json")
    index = read_json(run / "inputs/source-index.json")
    region = index["files"][0]["regions"][0]
    plan["units"] = [{"unit_id": "unit-0001", "title": "Existing behavior", "purpose": "reference",
                      "owned_regions": [region["region_id"]], "context_regions": []}]
    write_json(run / "inputs/source-first-plan.json", plan)
    progress = read_json(run / "progress.json")
    for suffix in ("one", "two"):
        action_id = f"{run_id}:analysis:{suffix}"
        task = run / "agent-tasks" / f"fixture-{suffix}.json"
        result = run / "agent-results" / f"fixture-{suffix}.json"
        write_json(task, {"unit_id": "unit-0001", "result_path": str(result)})
        write_json(result, {"format_version": "pangea-notes-v1", "binding": {"data_root": str(root),
                   "run_id": run_id, "action_id": action_id, "task_id": f"worker-{suffix}"}, "revision": 1,
                   "records": [{"record_id": "rec-000001", "kind": "test_case", "body": {"title": f"Historical {suffix}"},
                                "created_revision": 1}]})
        progress["actions"][action_id] = {"action_id": action_id, "action": "dispatch_agent", "role": "analysis",
            "stage": "unit_analysis", "task_path": str(task), "task_id": f"worker-{suffix}", "status": "accepted"}
        progress["accepted_revisions"][action_id] = 1
    write_json(run / "progress.json", progress)
    return run_id, run


def fingerprint(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class IncrementalSmoke(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pangea-incremental-check-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.parent_id, self.parent = create_parent(self.root)

    def derive(self, mode="supplement", **extras):
        path = self.root / "derive.json"
        write_json(path, {"mode": mode, "instruction": "Only add recovery checks", **extras})
        return derive_run(str(self.root), self.parent_id, str(path))

    def test_supplement_freezes_parent_and_has_fresh_results(self):
        before = fingerprint(self.parent)
        (self.root / "repositories/demo/changed.c").write_text("int current_version = 99;\n")
        option = derivation_options(str(self.root), self.parent_id)
        self.assertTrue(option["can_derive"])
        self.assertEqual(len(option["records"]), 2)
        self.assertEqual(option["records"][0]["record_id"], option["records"][1]["record_id"])
        child = self.derive(selected_unit_ids=["unit-0001"], selected_records=[{
            "action_id": option["records"][0]["action_id"], "record_id": "rec-000001"}])
        run = self.root / "runs" / child["run_id"]
        self.assertNotEqual(child["run_id"], self.parent_id)
        self.assertEqual(before, fingerprint(self.parent))
        self.assertIn("return 1", (run / "inputs/source/demo/changed.c").read_text())
        self.assertEqual(child["accepted_revisions"], {})
        task = read_json(Path(child["agent_actions"][0]["task_path"]))
        self.assertEqual(read_result(task["result_path"]).revision, 0)
        self.assertTrue(any(i["input_id"] == "incremental_parent_records" for i in task["inputs"]))
        # Creation/resume uses child's immutable baseline even if parent is unavailable.
        self.parent.rename(self.root / "parent-unavailable")
        resumed = resume_module_analysis(child["run_id"], str(self.root))
        self.assertEqual(resumed["run_id"], child["run_id"])
        action_id = child["agent_actions"][0]["action_id"]
        bind_action(str(self.root), child["run_id"], action_id, "new-child-worker")
        baseline = read_json(run / "inputs/incremental/baseline.json")
        page = input_read(str(self.root), child["run_id"], action_id, "new-child-worker",
                          input_id="incremental_parent_records", cursor=baseline["references"][1]["cursor"],
                          max_chars=baseline["references"][1]["chars"])
        self.assertIn("Historical two", page["text"])
        self.assertNotIn("Historical one", page["text"])

    def test_changed_paths_freeze_diff_and_distinguish_missing_baseline(self):
        repo = self.root / "repositories/demo"
        (repo / "changed.c").write_text("int changed(void) { return 9; }\n")
        (repo / "deleted.c").unlink()
        (repo / "new.c").write_text("int added(void) { return 4; }\n")
        child = self.derive("changed-files", changed_paths=["changed.c", "deleted.c", "new.c", "stable.c", "unknown.c"])
        detail = run_detail(str(self.root), child["run_id"])
        changes = detail["incremental_changes"]["changed_files"]
        self.assertEqual([c["status"] for c in changes], ["modified", "deleted", "baseline-missing", "unchanged", "missing-both"])
        run = self.root / "runs" / child["run_id"]
        self.assertFalse((run / "inputs/source/demo/deleted.c").exists())
        self.assertIn("return 9", (run / "inputs/source/demo/changed.c").read_text())
        self.assertIn("-int changed", (run / "inputs/incremental/diff-0001.txt").read_text())

    def test_bad_selection_and_escape_do_not_mutate_parent(self):
        before = fingerprint(self.parent)
        with self.assertRaisesRegex(ValueError, "选中的记录"):
            self.derive(selected_records=[{"action_id": "foreign:analysis", "record_id": "rec-000001"}])
        with self.assertRaisesRegex(ValueError, "相对文件路径"):
            self.derive("changed-files", changed_paths=["../outside.c"])
        self.assertEqual(before, fingerprint(self.parent))

    def test_unavailable_parent_snapshots_and_running_parent_are_explicit(self):
        progress = read_json(self.parent / "progress.json")
        progress["lifecycle_status"] = "running"
        write_json(self.parent / "progress.json", progress)
        option = derivation_options(str(self.root), self.parent_id)
        self.assertFalse(option["can_derive"])
        self.assertIn("先停止", option["blocked_reason"])
        progress["lifecycle_status"] = "stopped"
        write_json(self.parent / "progress.json", progress)
        (self.parent / "inputs/source/demo/changed.c").unlink()
        self.assertFalse(derivation_options(str(self.root), self.parent_id)["can_derive"])

    def test_existing_review_modes_and_worker_input_scopes_are_preserved(self):
        child = self.derive()
        run = self.root / "runs" / child["run_id"]
        contract = read_json(run / "inputs/task-contract.json")
        state = {"data_root": str(self.root), "run_id": child["run_id"], "task_contract": contract}
        planning = read_json(Path(child["agent_actions"][0]["task_path"]))
        parent_plan = read_json(self.parent / "inputs/source-first-plan.json")
        progress = load_progress(state)
        _make_analysis_actions(state, progress, parent_plan["units"], planning)
        analysis = next(a for a in progress.actions.values() if a.role == "analysis")
        task = read_json(Path(analysis.task_path))
        self.assertIn("incremental_request", task)
        self.assertEqual(read_result(task["result_path"]).records, [])
        for mode, expected in (("depth", "independent_review"), ("speed", "comparison_review")):
            contract["analysis_settings"]["mode"] = mode
            progress.actions.pop(f"{child['run_id']}:review", None)
            _prepare_review(state, progress)
            review = read_json(Path(progress.actions[f"{child['run_id']}:review"].task_path))
            self.assertEqual(review["review_stage"], expected)
            self.assertIn("incremental_request", review)

    def test_inherited_asset_body_does_not_require_live_asset_or_parent(self):
        write_json(self.parent / "inputs/asset-items.json", {"asset:fact": {"title": "Saved fact", "key_facts": ["saved content"],
                    "source_references": [{"path": str(self.root / "assets/deleted/original.txt"), "excerpt": "original evidence"}]}})
        child = self.derive()
        self.parent.rename(self.root / "parent-unavailable")
        action_id = child["agent_actions"][0]["action_id"]
        bind_action(str(self.root), child["run_id"], action_id, "worker")
        run = self.root / "runs" / child["run_id"]
        self.assertIn("saved content", (run / "inputs/asset-items.json").read_text())
        # Parent parsed facts are frozen; historical source references are provenance only.
        resume_module_analysis(child["run_id"], str(self.root))


if __name__ == "__main__":
    unittest.main(verbosity=2)
