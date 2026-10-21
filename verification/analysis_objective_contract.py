"""Real frozen tasks, original-worker closure and report objective compatibility; no model."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.analysis_scenarios import freeze_scene, scene_spec
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.run_module_analysis import run_module_analysis, resume_module_analysis
from pangea_agent.cli.source_first_api import (
    comparison_finding_write, plan_write, result_write, review_decide, work_finish,
)
from pangea_agent.models.contract import TaskContract
from pangea_agent.models.source_first import NoteRecord
from pangea_agent.report.source_first import _behavior_markdown


class ObjectiveContracts(unittest.TestCase):
    def test_objective_is_explicit_optional_for_old_contracts_and_never_inferred(self):
        base = dict(repository="repo", target="driver library", workflow_version="source-first-v1",
                    analysis_profile="behavior-test-v2", analysis_settings={"scenario": "module-analysis", "mode": "speed"})
        old = TaskContract.model_validate(base)
        self.assertNotIn("objective", old.analysis_settings)
        for objective in ("product-behavior", "interface-contract"):
            with self.subTest(objective=objective), tempfile.TemporaryDirectory() as td:
                selected = deepcopy(base)
                selected["analysis_settings"]["objective"] = objective
                contract = TaskContract.model_validate(selected).model_dump(mode="json")
                scene = freeze_scene(Path(td), contract)
                self.assertEqual(scene["objective"], objective)
                self.assertEqual(scene["presentation"]["blackbox_target"], objective == "product-behavior")
                self.assertEqual(scene["record_contracts"]["diagram"], "diagram-contract-v1")
        with self.assertRaises(ValueError):
            TaskContract.model_validate({**base, "analysis_settings": {**base["analysis_settings"], "objective": "auto"}})

    def test_old_scene_and_rubric_are_not_upgraded_on_resume(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            old = scene_spec("risk-analysis")
            old["record_contracts"].pop("diagram")
            write_json(run / "inputs/analysis-scene.json", old)
            before = (run / "inputs/analysis-scene.json").read_bytes()
            contract = {"analysis_profile": "behavior-test-v2", "analysis_settings": {"scenario": "risk-analysis", "mode": "speed"}}
            self.assertEqual(freeze_scene(run, contract), old)
            self.assertEqual((run / "inputs/analysis-scene.json").read_bytes(), before)
            self.assertFalse(old["presentation"]["blackbox_target"])

    def test_real_graph_preserves_goal_into_analysis_review_comparison_and_original_closure(self):
        for mode in ("depth", "speed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                repo = root / "repositories/repo"
                repo.mkdir(parents=True)
                (repo / "entry.c").write_text("int entry(void) { return 0; }\n")
                target, focus = "用户目标：启动到资源回收", ["跨文件异常传播", "调用方在边界外时记录缺口"]
                settings = {"scenario": "module-analysis", "mode": mode, "objective": "interface-contract"}
                request = root / "request.json"
                write_json(request, {"data_root": td, "repository": "repo", "target": target, "focus": focus,
                                     "source_scope": ["."], "analysis_profile": "behavior-test-v2", "analysis_settings": settings})
                created = run_module_analysis(str(request))
                run_id = created["run_id"]
                run = root / "runs" / run_id
                def actions():
                    return read_json(run / "progress.json")["actions"].values()
                def task(action):
                    return read_json(Path(action["task_path"]))
                def assert_goal(value):
                    self.assertEqual((value["target"], value["focus"], value["analysis_settings"]), (target, focus, settings))
                def finish(action, worker, revision):
                    work_finish(td, run_id, action["action_id"], worker, revision=revision)
                    settle_action(td, run_id, action["action_id"])
                planning = next(a for a in actions() if a["role"] == "planning")
                assert_goal(task(planning))
                bind_action(td, run_id, planning["action_id"], "planner")
                planned = plan_write(td, run_id, planning["action_id"], "planner", expected_revision=0,
                                     unit={"title": "主干", "purpose": "负责入口及跨单元挂接", "owned_files": [{"repo_id": "repo", "path": "entry.c"}]})
                finish(planning, "planner", planned["revision"])
                analysis = next(a for a in actions() if a["role"] == "analysis")
                assert_goal(task(analysis))
                bind_action(td, run_id, analysis["action_id"], "original-worker")
                written = result_write(td, run_id, analysis["action_id"], "original-worker", expected_revision=0,
                                       records=[{"kind": "note", "body": "Caller outside frozen boundary; retain unresolved evidence gap."}])
                finish(analysis, "original-worker", written["revision"])
                review = next(a for a in actions() if a["role"] == "review")
                assert_goal(task(review))
                bind_action(td, run_id, review["action_id"], "same-reviewer")
                if mode == "depth":
                    self.assertEqual(task(review)["review_stage"], "independent_review")
                    write = result_write(td, run_id, review["action_id"], "same-reviewer", expected_revision=0,
                                         records=[{"kind": "note", "body": "Independent source review retained."}])
                    finish(review, "same-reviewer", write["revision"])
                    review = next(a for a in actions() if a["stage"] == "comparison_review")
                    self.assertEqual(review["task_id"], "same-reviewer")
                    bind_action(td, run_id, review["action_id"], "same-reviewer")
                assert_goal(task(review))
                finding = comparison_finding_write(td, run_id, review["action_id"], "same-reviewer", expected_revision=0,
                                                   unit_ids=["unit-0001"], finding={"summary": "核对边界缺口", "body": "Retain current target and missing external caller evidence."})
                decision = review_decide(td, run_id, review["action_id"], "same-reviewer", expected_revision=finding["revision"],
                                         decision={"disposition": "finding", "version_set_id": task(review)["version_set_id"],
                                                   "correction_record_ids": finding["record_ids"]})
                finish(review, "same-reviewer", decision["revision"])
                closure = next(a for a in actions() if a["role"] == "closure")
                assert_goal(task(closure))
                self.assertEqual(closure["task_id"], "original-worker")
                frozen_path = run / "inputs/methodologies/builtin/scene_common_generation.md"
                frozen = frozen_path.read_bytes()
                resume_module_analysis(run_id, td)
                self.assertEqual(frozen_path.read_bytes(), frozen)

    def test_report_counts_same_declared_ready_fraction_for_all_objectives(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "runs/run/inputs"
            write_json(root / "coverage-gaps.json", [])
            cases = []
            for index, (level, readiness) in enumerate((("business_blackbox", "ready"), ("developer_assisted", "ready"),
                                                       ("developer_assisted", "needs_instrumentation"), ("interface_contract", "ready"))):
                cases.append(NoteRecord(record_id=str(index), kind="test_case", created_revision=1, evidence=[], relates_to=[], supersedes=[],
                             body={"format_version": "behavior-test-case-v1", "title": f"Case {index}", "test_level": level,
                                   "purpose": "branch", "execution_readiness": readiness}))
            records = [({"stage": "unit_analysis", "status": "accepted", "task": {"unit_id": "u"}}, SimpleNamespace(records=cases, warnings=[]))]
            for scenario, objective in (("module-analysis", "product-behavior"), ("module-analysis", "interface-contract"),
                                        ("risk-analysis", "product-behavior")):
                scene = freeze_scene(Path(td) / scenario / objective, {"analysis_profile": "behavior-test-v2",
                                     "analysis_settings": {"scenario": scenario, "mode": "speed", "objective": objective}})
                md = _behavior_markdown({"data_root": td, "run_id": "run"}, {"quality_status": "UNRESOLVED"}, records, scene=scene)
                self.assertIn("`2/4`，`50.0%`", md)
                self.assertEqual("交付目标 ≥70%" in md, scenario != "risk-analysis" and objective == "product-behavior")
                self.assertIn("UNRESOLVED", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
