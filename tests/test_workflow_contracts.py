from __future__ import annotations

import unittest

from pangea_agent.graph.nodes.advance_workflow import (
    _validate_comparison_review,
    _validate_review,
)
from pangea_agent.models.analysis import (
    AnalysisUnit,
    CodeFlow,
    ComparisonReviewResult,
    IndependentReviewResult,
    SourceEvidence,
    WorkflowProgress,
)


REPO_ID = "PANGEA-Mainline"


def _unit(unit_id: str, source_scope: list[str], context_scope: list[str] | None = None) -> AnalysisUnit:
    return AnalysisUnit(
        unit_id=unit_id,
        repo_id=REPO_ID,
        title=unit_id,
        source_scope=source_scope,
        context_scope=context_scope or [],
        rationale="regression test",
        line_count=10,
        function_count=1,
    )


def _progress(*units: AnalysisUnit) -> WorkflowProgress:
    return WorkflowProgress(run_id="RUN-TEST", analysis_units=list(units))


def _finding(
    *,
    finding_key: str = "RF-1",
    affected_unit_ids: list[str],
    path: str,
) -> dict:
    return {
        "finding_key": finding_key,
        "category": "risk",
        "affected_unit_ids": affected_unit_ids,
        "linked_input_ids": [],
        "summary": "源码风险检查",
        "required_check": "核对该路径的真实行为",
        "evidence": [
            {
                "repo_id": REPO_ID,
                "path": path,
                "line_start": 1,
                "line_end": 1,
                "observation": "直接源码证据",
            }
        ],
    }


class CodeFlowReferenceTests(unittest.TestCase):
    def test_edge_must_reference_defined_step(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知 step_key"):
            CodeFlow.model_validate({
                "flow_key": "F01",
                "title": "flow",
                "entry": "entry",
                "summary": "summary",
                "steps": [
                    {
                        "step_key": "F01-S1",
                        "label": "entry",
                        "kind": "entry",
                        "evidence": [
                            {
                                "repo_id": REPO_ID,
                                "path": "tls/a.c",
                                "line_start": 1,
                                "observation": "entry",
                            }
                        ],
                    }
                ],
                "edges": [
                    {
                        "source_step_key": "F01-S1",
                        "target_step_key": "F01-SX",
                    }
                ],
            })

    def test_evidence_path_normalizes_windows_separator(self) -> None:
        evidence = SourceEvidence(
            repo_id=REPO_ID,
            path=r"tls\ntt_x.c",
            line_start=1,
            observation="source",
        )
        self.assertEqual(evidence.path, "tls/ntt_x.c")


class ReviewScopeTests(unittest.TestCase):
    def test_same_repo_multiple_units_do_not_overwrite_earlier_scope(self) -> None:
        progress = _progress(
            _unit("U01", ["tls/u01.c"]),
            _unit("U02", ["tls/u02.c"]),
            _unit("U03", ["tls/u03.c"]),
            _unit("U04", ["tls/u04.c"]),
        )
        result = IndependentReviewResult.model_validate({
            "summary": "review",
            "findings": [
                _finding(affected_unit_ids=["U01"], path="tls/u01.c")
            ],
            "unresolved": [],
        })

        _validate_review(progress, result)
        self.assertEqual(result.findings[0].evidence[0].path, "tls/u01.c")

    def test_finding_cannot_borrow_path_from_unaffected_unit(self) -> None:
        progress = _progress(
            _unit("U01", ["tls/u01.c"]),
            _unit("U02", ["tls/u02.c"]),
        )
        result = IndependentReviewResult.model_validate({
            "summary": "review",
            "findings": [
                _finding(affected_unit_ids=["U01"], path="tls/u02.c")
            ],
            "unresolved": [],
        })

        with self.assertRaisesRegex(ValueError, "affected_unit_ids"):
            _validate_review(progress, result)

    def test_unique_basename_repairs_review_evidence_to_canonical_path(self) -> None:
        progress = _progress(_unit("U03", ["tls/ntt_x.c"]))
        result = IndependentReviewResult.model_validate({
            "summary": "review",
            "findings": [
                _finding(affected_unit_ids=["U03"], path="tls/packet/ntt_x.c")
            ],
            "unresolved": [],
        })

        _validate_review(progress, result)
        self.assertEqual(result.findings[0].evidence[0].path, "tls/ntt_x.c")

    def test_ambiguous_basename_is_rejected_instead_of_guessed(self) -> None:
        progress = _progress(
            _unit("U03", ["tls/client/ntt_x.c", "tls/server/ntt_x.c"])
        )
        result = IndependentReviewResult.model_validate({
            "summary": "review",
            "findings": [
                _finding(affected_unit_ids=["U03"], path="tls/packet/ntt_x.c")
            ],
            "unresolved": [],
        })

        with self.assertRaisesRegex(ValueError, "allowed_paths"):
            _validate_review(progress, result)

    def test_comparison_decision_uses_original_finding_affected_units(self) -> None:
        progress = _progress(
            _unit("U01", ["tls/u01.c"]),
            _unit("U02", ["tls/u02.c"]),
        )
        independent = IndependentReviewResult.model_validate({
            "summary": "independent",
            "findings": [
                _finding(affected_unit_ids=["U01"], path="tls/u01.c")
            ],
            "unresolved": [],
        })
        comparison = ComparisonReviewResult.model_validate({
            "summary": "comparison",
            "independent_finding_decisions": [
                {
                    "finding_key": "RF-1",
                    "disposition": "dismissed",
                    "conclusion": "dismissed",
                    "evidence": [
                        {
                            "repo_id": REPO_ID,
                            "path": "tls/u02.c",
                            "line_start": 1,
                            "observation": "wrong unit evidence",
                        }
                    ],
                }
            ],
            "findings": [],
            "unresolved": [],
        })

        with self.assertRaisesRegex(ValueError, "affected_unit_ids"):
            _validate_comparison_review(
                progress,
                independent,
                comparison,
                selected_inputs={},
                analysis_results={},
            )


if __name__ == "__main__":
    unittest.main()
