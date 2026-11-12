"""Issue #7: frozen methodology delivery, not model semantic acceptance.
Run: python verification/boundary_assertion_loading.py
"""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pangea_agent.agent_io import write_json
from pangea_agent.analysis_scenarios import PROFILE, SCENES, freeze_scene, scene_rubric_paths
from pangea_agent.graph.nodes.source_first import _freeze_source_first_rubrics

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "src/pangea_agent/rubrics/builtin"
MARKER = "BOUNDARY-ASSERTION"

class BoundaryAssertionLoading(unittest.TestCase):
    def test_required_in_every_scene_without_optional_method_selection(self):
        for scenario in SCENES:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                run = Path(directory)
                _freeze_source_first_rubrics(run)
                write_json(run / "inputs/methodologies/manifest.json", {"enabled_user_methodologies": []})
                spec = freeze_scene(run, {"analysis_profile": PROFILE, "analysis_settings": {"scenario": scenario}})
                paths = scene_rubric_paths(run, spec, "analysis", [], "c_cpp")
                common = next(Path(p) for p in paths if Path(p).stem == "scene_common_generation")
                text = common.read_text()
                for term in (MARKER, "命令字", "反向追踪", "open-question", "unresolved_dependencies", "单元总结", "不由宿主扫描关键词"):
                    self.assertIn(term, text)
                self.assertIn(MARKER, next(Path(p) for p in paths if Path(p).stem == "scene_flow").read_text())
                plan = scene_rubric_paths(run, spec, "planning", [], "c_cpp")
                self.assertIn("repository-local-v1", next(Path(p) for p in plan if Path(p).stem == "scene_planning").read_text())

    def test_resume_keeps_old_frozen_bytes_and_new_run_gets_new_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            shutil.copytree(RULES, installed)
            name = "scene_common_generation.md"
            current = (installed / name).read_bytes()
            (installed / name).write_text("# Historical frozen rules\n")
            with patch("pangea_agent.graph.nodes.source_first.project_path", return_value=installed):
                frozen = _freeze_source_first_rubrics(root / "old")
                before = {p: Path(p).read_bytes() for p in frozen.values()}
                (installed / name).write_bytes(current)
                resumed = _freeze_source_first_rubrics(root / "old")
                fresh = _freeze_source_first_rubrics(root / "new")
            self.assertEqual(frozen, resumed)
            self.assertEqual(before, {p: Path(p).read_bytes() for p in resumed.values()})
            self.assertTrue(any(MARKER in Path(p).read_text() for p in fresh.values()))

if __name__ == "__main__":
    unittest.main(verbosity=2)
