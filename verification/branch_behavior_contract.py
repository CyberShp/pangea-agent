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

from pangea_agent.agent_io import write_json
from pangea_agent.analysis_scenarios import (
    BRANCH_METHOD, PROFILE, SCENES, freeze_scene, scene_rubric_paths, scene_spec,
)
from pangea_agent.flow_schema import check_flow_structure
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
            self.assertIn(str(methods / "builtin/scene_branch.md"), analysis)
            self.assertIn(str(methods / "builtin/scene_branch_review.md"), review)
            self.assertNotIn("scene_risk_review.md", " ".join(review))
            # An unreadable mandatory input is different from a semantic result.
            (methods / "builtin/scene_branch_review.md").unlink()
            with self.assertRaisesRegex(ValueError, "scene_branch_review"):
                scene_rubric_paths(run, spec, "review", [], "c_cpp")

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
