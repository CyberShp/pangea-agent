from __future__ import annotations

import unittest

from pangea_agent.models.analysis import (
    AnalysisTask,
    ComparisonReviewTask,
    IndependentReviewTask,
    RepositoryRef,
)


class ReviewTaskContractTests(unittest.TestCase):
    def test_analysis_task_exposes_skeleton_path(self) -> None:
        task = AnalysisTask.model_validate({
            "run_id": "RUN-TEST",
            "target": "module",
            "unit": {
                "unit_id": "U01",
                "repo_id": "repo",
                "title": "unit",
                "source_scope": ["src/a.c"],
                "rationale": "test",
                "line_count": 1,
                "function_count": 1,
            },
            "repository": {"repo_id": "repo", "source_root": "repo"},
            "inventory_path": "inventory.json",
            "source_manifest_path": "source-manifest.json",
            "selected_inputs_path": "selected-inputs.json",
            "result_path": "U01.json",
            "rubric_paths": ["rubric.md"],
        })
        self.assertEqual(
            task.result_skeleton_path,
            "schemas/analysis_result.skeleton.json",
        )
        self.assertIn("result_skeleton_path", task.model_dump(mode="json"))

    def test_independent_review_task_exposes_skeleton_path(self) -> None:
        task = IndependentReviewTask(
            run_id="RUN-TEST",
            target="module",
            repositories=[RepositoryRef(repo_id="repo", source_root="repo")],
            unit_plan_path="unit-plan.json",
            inventory_path="inventory.json",
            source_manifest_path="source-manifest.json",
            selected_inputs_path="selected-inputs.json",
            rubric_paths=["rubric.md"],
            result_path="review.json",
        )
        self.assertEqual(
            task.result_skeleton_path,
            "schemas/independent_review_result.skeleton.json",
        )
        self.assertIn("result_skeleton_path", task.model_dump(mode="json"))

    def test_comparison_review_task_exposes_skeleton_path(self) -> None:
        task = ComparisonReviewTask(
            run_id="RUN-TEST",
            target="module",
            unit_plan_path="unit-plan.json",
            analysis_task_paths={"U01": "U01-task.json"},
            analysis_result_paths={"U01": "U01-result.json"},
            independent_review_result_path="review.json",
            selected_inputs_path="selected-inputs.json",
            rubric_paths=["rubric.md"],
            result_path="comparison-review.json",
        )
        self.assertEqual(
            task.result_skeleton_path,
            "schemas/comparison_review_result.skeleton.json",
        )
        self.assertIn("result_skeleton_path", task.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
