"""Verify coverage rule loading and curated fixture integrity, NOT model semantics.

Run: .venv/bin/python verification/coverage_semantic_examples.py
Positive/negative labels are human-authored review examples, not predictions.
No production result filtering, semantic gate, or historical Run mutation occurs.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pangea_agent.agent_io import write_json
from pangea_agent.analysis_scenarios import PROFILE, freeze_scene, scene_rubric_paths
from pangea_agent.graph.nodes.source_first import _freeze_source_first_rubrics


ROOT = Path(__file__).resolve().parents[1]
RUBRICS = ROOT / "src/pangea_agent/rubrics/builtin"
FIXTURE = ROOT / "verification/fixtures/coverage_semantic_examples.json"


class CoverageRuleExamples(unittest.TestCase):
    def test_examples_have_explicit_evidence_and_contrasting_human_labels(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["format_version"], "coverage-semantic-examples-v1")
        self.assertTrue(fixture["purpose"])
        examples = fixture["examples"]
        ids = [example["id"] for example in examples]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {
            "covered_main_uncovered_error", "uncovered_normal_path", "setup_not_case",
            "same_title_distinct_parameters", "no_gap_zero_cases", "source_limitations",
            "missing_external_trigger", "cross_unit_owner", "reuse_existing_branch_case",
            "per_case_gap_explanation", "multiple_measurement_batches",
        })
        for example in examples:
            with self.subTest(example=example["id"]):
                for field in ("topic", "evidence"):
                    self.assertIsInstance(example[field], str)
                    self.assertTrue(example[field].strip())
                self.assertIn("gap", example)
                for polarity in ("positive", "negative"):
                    for field in ("expected", "proposal", "reason"):
                        self.assertIsInstance(example[polarity][field], str)
                        self.assertTrue(example[polarity][field].strip())
                self.assertNotEqual(example["positive"], example["negative"])

    def test_new_coverage_roles_load_frozen_rules_without_optional_methods(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            _freeze_source_first_rubrics(run)
            write_json(run / "inputs/methodologies/manifest.json", {"enabled_user_methodologies": []})
            spec = freeze_scene(run, {"analysis_profile": PROFILE,
                                     "analysis_settings": {"scenario": "coverage-analysis"}})
            self.assertEqual(spec["risk_responsibility"], "none")
            for role, names in (
                ("planning", ("scene_planning",)),
                ("analysis", ("scene_coverage", "scene_common_generation", "scene_flow")),
                ("review", ("scene_coverage", "scene_common_review", "scene_flow")),
            ):
                selected = scene_rubric_paths(run, spec, role, [])
                for name in names:
                    path = run / f"inputs/methodologies/builtin/{name}.md"
                    self.assertIn(str(path), selected)
                    self.assertEqual(path.read_bytes(), (RUBRICS / f"{name}.md").read_bytes())

    def test_existing_snapshots_are_not_replaced_by_updated_installed_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            installed.mkdir()
            names = ("scene_coverage", "scene_planning")
            for name in names:
                (installed / f"{name}.md").write_text(f"# Prior frozen {name}\n", encoding="utf-8")
            old_run, new_run = root / "old-run", root / "new-run"
            with patch("pangea_agent.graph.nodes.source_first.project_path", return_value=installed):
                prior = _freeze_source_first_rubrics(old_run)
                original = {name: Path(prior[name]).read_bytes() for name in names}
                for name in names:
                    (installed / f"{name}.md").write_bytes((RUBRICS / f"{name}.md").read_bytes())
                resumed = _freeze_source_first_rubrics(old_run)
                fresh = _freeze_source_first_rubrics(new_run)
            for name in names:
                self.assertEqual(Path(resumed[name]).read_bytes(), original[name])
                self.assertEqual(Path(fresh[name]).read_bytes(), (RUBRICS / f"{name}.md").read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
