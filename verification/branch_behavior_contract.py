"""Branch rubric freezing/loading and non-gated evidence records.

Run with: python verification/branch_behavior_contract.py
These checks verify host contracts, not a model's semantic completeness.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.analysis_scenarios import (
    BRANCH_METHOD, PROFILE, SCENES, freeze_scene, scene_rubric_paths, scene_spec,
)
from pangea_agent.flow_schema import check_flow_structure
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.cli.source_first_api import input_read, plan_write, result_write, work_finish
from pangea_agent.graph.nodes.source_first import _freeze_source_first_rubrics
from pangea_agent.graph.result_store import append_records, initialize_result, read_result
from pangea_agent.models.source_first import SourceBinding


ROOT = Path(__file__).resolve().parents[1]
RUBRICS = ROOT / "src/pangea_agent/rubrics/builtin"


def freeze_methods(run: Path) -> Path:
    root = run / "inputs/methodologies"
    shutil.copytree(RUBRICS, root / "builtin")
    write_json(root / "manifest.json", {"enabled_user_methodologies": []})
    return root


def contract(scenario="branch-analysis"):
    return {"analysis_profile": PROFILE, "analysis_settings": {"scenario": scenario}}


def rubric_json(name: str) -> dict:
    source = (RUBRICS / name).read_text(encoding="utf-8")
    return json.loads(re.search(r"```json\s*\n(.*?)\n```", source, re.S).group(1))


class BranchBehaviorContracts(unittest.TestCase):
    def test_new_branch_loads_required_reviewer_without_selected_methodology(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            methods = freeze_methods(run)
            spec = freeze_scene(run, contract())
            self.assertEqual(spec["method_version"], BRANCH_METHOD)
            self.assertEqual(spec["risk_responsibility"], "none")
            analysis = scene_rubric_paths(run, spec, "analysis", [], "c_cpp")
            review = scene_rubric_paths(run, spec, "review", [], "c_cpp")
            self.assertIn(str(methods / "builtin/scene_common_generation.md"), analysis)
            self.assertIn(str(methods / "builtin/scene_common_review.md"), review)
            self.assertIn(str(methods / "builtin/scene_flow.md"), analysis)
            self.assertIn(str(methods / "builtin/scene_flow.md"), review)
            self.assertIn(str(methods / "builtin/scene_branch.md"), analysis)
            self.assertIn(str(methods / "builtin/scene_branch_review.md"), review)
            self.assertNotIn("scene_risk_review.md", " ".join(review))
            # An unreadable mandatory input is different from a semantic result.
            (methods / "builtin/scene_branch_review.md").unlink()
            with self.assertRaisesRegex(ValueError, "scene_branch_review"):
                scene_rubric_paths(run, spec, "review", [], "c_cpp")

    def test_runtime_rule_update_only_reaches_new_run_snapshots(self):
        # Exercise the real freezer, not a copy of its implementation. Simulate
        # an installed-rule update while a previous Run already has snapshots.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            shutil.copytree(RUBRICS, installed)
            names = ("scene_common_generation", "scene_common_review", "scene_branch",
                     "scene_branch_review", "scene_flow", "scene_coverage")
            current = {name: (installed / f"{name}.md").read_bytes() for name in names}
            for name in names:
                (installed / f"{name}.md").write_bytes(f"# Frozen prior rule: {name}\n".encode())
            old_run, new_run = root / "old-run", root / "new-run"
            with patch("pangea_agent.graph.nodes.source_first.project_path", return_value=installed):
                old_paths = _freeze_source_first_rubrics(old_run)
                before = {name: Path(old_paths[name]).read_bytes() for name in names}
                for name, content in current.items():
                    (installed / f"{name}.md").write_bytes(content)
                resumed_paths = _freeze_source_first_rubrics(old_run)
                new_paths = _freeze_source_first_rubrics(new_run)
            for name in names:
                self.assertEqual(Path(resumed_paths[name]).read_bytes(), before[name])
                self.assertEqual(Path(new_paths[name]).read_bytes(), current[name])
                self.assertNotEqual(before[name], current[name])
            for run in (old_run, new_run):
                write_json(run / "inputs/methodologies/manifest.json", {"enabled_user_methodologies": []})
                spec = freeze_scene(run, contract())
                for role, required in (("analysis", ("scene_common_generation", "scene_branch", "scene_flow")),
                                       ("review", ("scene_common_review", "scene_branch_review", "scene_flow"))):
                    selected = scene_rubric_paths(run, spec, role, [])
                    for name in required:
                        self.assertIn(str(run / f"inputs/methodologies/builtin/{name}.md"), selected)

    def test_real_worker_tasks_can_page_through_their_frozen_scenario_rules(self):
        # Use real Graph dispatch, binding and paginated input access. Saved
        # notes are only lifecycle fixtures, never model-quality evidence.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repositories/demo"
            repo.mkdir(parents=True)
            (repo / "close.c").write_text("int close_demo(void) { return 0; }\n", encoding="utf-8")
            request = root / "request.json"
            write_json(request, {"data_root": directory, "repository": "demo", "target": "关闭行为",
                                 "source_scope": ["."], "analysis_profile": PROFILE,
                                 "analysis_settings": {"scenario": "branch-analysis", "mode": "speed",
                                                       "objective": "product-behavior"}})
            created = run_module_analysis(str(request))
            run_id = created["run_id"]
            run = root / "runs" / run_id

            def action_for(role):
                return next(a for a in read_json(run / "progress.json")["actions"].values()
                            if a["role"] == role)

            def finish(action, worker, revision):
                work_finish(directory, run_id, action["action_id"], worker, revision=revision)
                settle_action(directory, run_id, action["action_id"])

            planning = action_for("planning")
            bind_action(directory, run_id, planning["action_id"], "planner")
            written = plan_write(directory, run_id, planning["action_id"], "planner", expected_revision=0,
                                 unit={"title": "关闭", "purpose": "负责关闭入口及结果",
                                       "owned_files": [{"repo_id": "demo", "path": "close.c"}]})
            finish(planning, "planner", written["revision"])
            for role, worker, names in (
                ("analysis", "original-worker", ("scene_common_generation", "scene_branch", "scene_flow")),
                ("review", "reviewer", ("scene_common_review", "scene_branch_review", "scene_flow")),
            ):
                action = action_for(role)
                task = read_json(Path(action["task_path"]))
                bind_action(directory, run_id, action["action_id"], worker)
                for name in names:
                    input_id = f"rubric_{name}"
                    item = next(i for i in task["inputs"] if i["input_id"] == input_id)
                    self.assertIn(item["path"], task["rubric_paths"])
                    parts, cursor = [], None
                    while True:
                        page = input_read(directory, run_id, action["action_id"], worker,
                                          input_id=input_id, cursor=cursor, max_chars=800)
                        parts.append(page["text"])
                        cursor = page["next_cursor"]
                        if cursor is None:
                            break
                    self.assertGreater(len(parts), 1)
                    self.assertEqual("".join(parts), Path(item["path"]).read_text(encoding="utf-8"))
                    self.assertEqual(Path(item["path"]).read_bytes(), (RUBRICS / f"{name}.md").read_bytes())
                if role == "analysis":
                    written = result_write(directory, run_id, action["action_id"], worker, expected_revision=0,
                                           records=[{"kind": "note", "body": "机械派发夹具，不代表业务分析完成或模型通过"}])
                    finish(action, worker, written["revision"])

    def test_existing_freeze_keeps_previous_review_and_flow_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            methods = freeze_methods(run)
            legacy = deepcopy(scene_spec("branch-analysis"))
            legacy.pop("method_version")
            legacy.pop("flow_layout_version")
            legacy["presentation"].pop("behavior_coverage")
            legacy["review_rubrics"].remove("scene_branch_review")
            path = run / "inputs/analysis-scene.json"
            write_json(path, legacy)
            before = path.read_bytes()
            (methods / "builtin/scene_branch_review.md").unlink()
            frozen = freeze_scene(run, contract())
            review = scene_rubric_paths(run, frozen, "review", [])
            self.assertEqual(frozen, legacy)
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn("scene_branch_review.md", " ".join(review))
            self.assertIsNone(freeze_scene(run, {"analysis_profile": "behavior-test-v1"}))

    def test_only_new_scenes_opt_into_flow_layout_and_branch_review(self):
        for scenario in SCENES:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                spec = freeze_scene(Path(directory), contract(scenario))
                self.assertEqual(spec["flow_layout_version"], "stable-v1")
                self.assertEqual("scene_branch_review" in spec["review_rubrics"],
                                 scenario == "branch-analysis")
                # Coverage is an ordinary note, never a result-write contract.
                self.assertNotIn("behavior-coverage-v1", spec["record_contracts"].values())

    def test_missing_unresolved_and_irregular_notes_remain_writable(self):
        body = rubric_json("scene_branch.md")
        designed = body["items"][0]
        designed["case_refs"] = []  # Deliberately inconsistent; Reviewer owns meaning.
        body["items"] += [
            {**deepcopy(designed), "item_id": "B-02", "status": "missing",
             "reason": "停止后再次启动行为已确认，尚未生成用例"},
            {**deepcopy(designed), "item_id": "B-03", "status": "unresolved",
             "reason": "配置何时应用到已有对象的契约未提供"},
        ]
        irregular = {"format_version": "behavior-coverage-v1", "items": "保留异常原文"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            binding = SourceBinding(data_root=directory, run_id="run", action_id="analysis", task_id="worker")
            initialize_result(path, binding)
            append_records(path, binding, 0, [
                {"kind": "note", "body": body},
                {"kind": "note", "body": irregular},
            ], record_contracts=scene_spec("branch-analysis")["record_contracts"])
            append_records(path, binding, 1, [
                {"kind": "summary", "body": "仍有 missing/unresolved，保留已有有效设计"}
            ], record_contracts=scene_spec("branch-analysis")["record_contracts"])
            result = read_result(path)
            self.assertEqual(result.revision, 2)
            self.assertEqual(result.records[0].body, body)
            self.assertEqual(result.records[1].body, irregular)
            self.assertEqual(result.records[2].kind, "summary")

    def test_flow_example_and_legacy_metadata_free_shape_both_pass(self):
        flow = rubric_json("scene_flow.md")
        self.assertEqual(check_flow_structure(flow), [])
        flow.pop("layout_version")
        flow.pop("main_path_ids")
        for node in flow["nodes"]:
            node.pop("semantic_key")
        for edge in flow["edges"]:
            edge.pop("edge_id")
        for path in flow["paths"]:
            path.pop("edge_ids")
        self.assertEqual(check_flow_structure(flow), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
