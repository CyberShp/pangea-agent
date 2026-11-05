"""Frozen business-language rules reach Analysis, without changing old Runs.

Exercises real dispatch and paginated input access. This is an engineering
contract check, not evidence that a model follows the rules or improves cases.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.analysis_scenarios import PROFILE, SCENES, freeze_scene, scene_rubric_paths, scene_spec
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.cli.source_first_api import input_read, plan_write, result_write, work_finish
from pangea_agent.graph.nodes.source_first import _freeze_source_first_rubrics
from pangea_agent.graph.result_store import read_result

ROOT = Path(__file__).resolve().parents[1]
RUBRICS = ROOT / "src/pangea_agent/rubrics/builtin"
BUSINESS_METHODS = {"c_cpp_business_semantics", "lua_business_semantics"}


def contract(scenario="branch-analysis"):
    return {"analysis_profile": PROFILE, "analysis_settings": {"scenario": scenario}}


def frozen_methods(run: Path):
    paths = _freeze_source_first_rubrics(run)
    write_json(run / "inputs/methodologies/manifest.json", {"enabled_user_methodologies": []})
    return paths


class BusinessLanguageLoading(unittest.TestCase):
    def test_every_new_scene_loads_only_its_analysis_language_and_keeps_risk_methods(self):
        for scenario in SCENES:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                run = Path(directory)
                frozen_methods(run)
                spec = freeze_scene(run, contract(scenario))
                for language in ("c_cpp", "lua"):
                    name = f"{language}_business_semantics"
                    analysis = scene_rubric_paths(run, spec, "analysis", [], language)
                    names = {Path(path).stem for path in analysis}
                    self.assertEqual(names & BUSINESS_METHODS, {name})
                    self.assertNotIn("c_cpp_analysis", names)
                    self.assertNotIn("lua_analysis", names)
                    # Explicitly selecting a required method must not duplicate it.
                    selected = scene_rubric_paths(run, spec, "analysis", [name, name], language)
                    self.assertEqual(selected, analysis)
                    for role in ("planning", "review"):
                        role_names = {Path(path).stem for path in scene_rubric_paths(run, spec, role, [], language)}
                        self.assertFalse(role_names & BUSINESS_METHODS)
                        if role == "review":
                            self.assertEqual(f"{language}_risk_methods" in role_names, scenario == "risk-analysis")
                    self.assertEqual(f"{language}_risk_methods" in names, scenario == "risk-analysis")
                # An absent language is not guessed from title, scenario or files.
                untyped = scene_rubric_paths(run, spec, "analysis", [], None)
                self.assertFalse({Path(path).stem for path in untyped} & BUSINESS_METHODS)

    def test_existing_scenes_and_rule_bytes_are_not_backfilled_on_resume(self):
        for scenario in SCENES:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                installed = root / "installed"
                shutil.copytree(RUBRICS, installed)
                new_rules = {name: (installed / f"{name}.md").read_bytes() for name in BUSINESS_METHODS}
                for name in BUSINESS_METHODS:
                    (installed / f"{name}.md").unlink()
                old_run = root / "old-run"
                old_spec = deepcopy(scene_spec(scenario))
                if scenario == "risk-analysis":
                    for methods in old_spec["language_rubrics"]["analysis"].values():
                        methods[:] = [name for name in methods if name not in BUSINESS_METHODS]
                else:
                    old_spec.pop("language_rubrics")
                spec_path = old_run / "inputs/analysis-scene.json"
                write_json(spec_path, old_spec)
                before_spec = spec_path.read_bytes()
                with patch("pangea_agent.graph.nodes.source_first.project_path", return_value=installed):
                    frozen = frozen_methods(old_run)
                    before_rules = {Path(path): Path(path).read_bytes() for path in frozen.values()}
                    for name, content in new_rules.items():
                        (installed / f"{name}.md").write_bytes(content)
                    resumed = frozen_methods(old_run)
                    new_run = root / "new-run"
                    fresh = frozen_methods(new_run)
                self.assertEqual(resumed, frozen)
                self.assertEqual(before_rules, {path: path.read_bytes() for path in before_rules})
                self.assertEqual(freeze_scene(old_run, contract(scenario)), old_spec)
                self.assertEqual(spec_path.read_bytes(), before_spec)
                self.assertFalse(set(resumed) & BUSINESS_METHODS)
                self.assertEqual(set(fresh) & BUSINESS_METHODS, BUSINESS_METHODS)
                for language in ("c_cpp", "lua"):
                    selected = scene_rubric_paths(old_run, old_spec, "analysis", [], language)
                    self.assertFalse({Path(path).stem for path in selected} & BUSINESS_METHODS)

    def test_real_c_and_lua_analysis_tasks_read_frozen_methods_and_finish_without_review(self):
        corpus = json.loads((ROOT / "verification/fixtures/blackbox-semantic-examples-v1.json").read_text(encoding="utf-8"))
        # Intentionally bad semantics remain writable: the host does not judge
        # business correctness or silently turn a case into a different result.
        body = next(pair for pair in corpus["pairs"] if pair["id"] == "internal-oracle")["negative"]["body"]
        for language, extension, source in (
            ("c_cpp", "c", "int public_entry(void) { return 0; }\n"),
            ("lua", "lua", "local M = {}\nfunction M.start() return true end\nreturn M\n"),
        ):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo = root / "repositories/demo/src"
                repo.mkdir(parents=True)
                relative = f"src/entry.{extension}"
                (root / "repositories/demo" / relative).write_text(source, encoding="utf-8")
                request = root / "request.json"
                write_json(request, {"data_root": directory, "repository": "demo", "target": "入口行为",
                                     "source_scope": ["src"], "analysis_profile": PROFILE,
                                     "analysis_settings": {"scenario": "branch-analysis", "mode": "speed",
                                                           "objective": "product-behavior"}})
                run_id = run_module_analysis(str(request))["run_id"]
                run = root / "runs" / run_id

                def actions():
                    return list(read_json(run / "progress.json")["actions"].values())

                planning = next(action for action in actions() if action["role"] == "planning")
                plan_task = read_json(Path(planning["task_path"]))
                self.assertFalse({Path(path).stem for path in plan_task["rubric_paths"]} & BUSINESS_METHODS)
                bind_action(directory, run_id, planning["action_id"], "planner")
                saved = plan_write(directory, run_id, planning["action_id"], "planner", expected_revision=0,
                                   unit={"title": "入口", "purpose": "负责入口和后续行为",
                                         "owned_files": [{"repo_id": "demo", "path": relative}], "methodology_ids": []})
                work_finish(directory, run_id, planning["action_id"], "planner", revision=saved["revision"])
                settle_action(directory, run_id, planning["action_id"])
                action = next(action for action in actions() if action["role"] == "analysis")
                task = read_json(Path(action["task_path"]))
                self.assertEqual(task["analysis_language"], language)
                name = f"{language}_business_semantics"
                self.assertEqual({Path(path).stem for path in task["rubric_paths"]} & BUSINESS_METHODS, {name})
                inputs = [item for item in task["inputs"] if item["input_id"] == f"rubric_{name}"]
                self.assertEqual(len(inputs), 1)
                frozen_path = Path(inputs[0]["path"])
                self.assertTrue(frozen_path.is_relative_to(run / "inputs/methodologies/builtin"))
                self.assertEqual(frozen_path.read_bytes(), (RUBRICS / f"{name}.md").read_bytes())
                bind_action(directory, run_id, action["action_id"], "original-worker")
                args = (directory, run_id, action["action_id"], "original-worker")
                parts, cursor = [], None
                while True:
                    page = input_read(*args, input_id=inputs[0]["input_id"], cursor=cursor, max_chars=800)
                    parts.append(page["text"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
                self.assertEqual("".join(parts), frozen_path.read_text(encoding="utf-8"))
                saved = result_write(*args, expected_revision=0, records=[{"kind": "test_case", "body": body}])
                self.assertEqual(read_result(task["result_path"]).records[0].body, body)
                work_finish(*args, revision=saved["revision"])
                settle_action(directory, run_id, action["action_id"])
                progress = read_json(run / "progress.json")
                self.assertEqual(progress["lifecycle_status"], "complete")
                self.assertEqual({action["role"] for action in actions()}, {"planning", "analysis"})
                self.assertEqual(progress["review_policy"], "disabled")
                self.assertEqual(progress["quality_status"], "UNRESOLVED")


if __name__ == "__main__":
    print("仅验证冻结装配、真实派发和写入边界；不证明模型生成质量。", flush=True)
    unittest.main(verbosity=2)
