from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import (
    _validate_action,
    bind_action,
    settle_action,
    validate_action,
)
from pangea_agent.cli.run_module_analysis import _default_run_id
from pangea_agent.graph.nodes.advance_workflow import (
    _accept_comparison_review,
    _validate_comparison_review,
    _validate_review,
    advance_workflow,
)
from pangea_agent.graph.nodes.prepare_inputs import _coverage_for_owned_sources
from pangea_agent.graph.result_contract import validate_unit_result
from pangea_agent.graph.workflow_store import load_progress, save_progress
from pangea_agent.models.analysis import (
    ActionState,
    AnalysisTask,
    AnalysisUnit,
    CodeFlow,
    ComparisonReviewResult,
    IndependentReviewResult,
    SourceEvidence,
    UnitSemanticResult,
    WorkflowProgress,
)


REPO_ID = "PANGEA-Mainline"


class RunIdAllocationTests(unittest.TestCase):
    def test_default_run_id_allocation_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            contract = {
                "target": "DHCHAP NVMe-oF fabric",
                "data_root": root,
            }
            with ThreadPoolExecutor(max_workers=5) as executor:
                run_ids = list(executor.map(
                    lambda _: _default_run_id(contract),
                    range(5),
                ))

            self.assertEqual(len(set(run_ids)), 5)
            self.assertTrue(all(
                (Path(root) / "runs" / run_id).is_dir()
                for run_id in run_ids
            ))


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


def _analysis_task(root: str, result_path: Path | None = None) -> AnalysisTask:
    run_dir = Path(root) / "runs" / "RUN-TEST"
    selected_inputs_path = run_dir / "inputs" / "units" / "U00.json"
    write_json(selected_inputs_path, {
        "asset_items": {},
        "defect_mechanisms": {},
        "coverage_gaps": [],
        "test_case_examples": [],
    })
    return AnalysisTask.model_validate({
        "run_id": "RUN-TEST",
        "target": "module",
        "unit": {
            "unit_id": "U00",
            "repo_id": REPO_ID,
            "title": "unit",
            "source_scope": ["src/a.c"],
            "context_scope": [],
            "rationale": "test",
            "line_count": 10,
            "function_count": 1,
        },
        "repository": {"repo_id": REPO_ID, "source_root": ".", "git": {}},
        "inventory_path": str(run_dir / "inputs" / "inventory.json"),
        "source_manifest_path": str(run_dir / "inputs" / "source-manifest.json"),
        "selected_inputs_path": str(selected_inputs_path),
        "coverage_context": [],
        "result_path": str(
            result_path or run_dir / "agent-results" / "analysis" / "U00.json"
        ),
        "rubric_paths": ["rubric.md"],
    })


def _semantic_result(
    *,
    linked_input_ids: list[str] | None = None,
    basis: list[str] | None = None,
) -> UnitSemanticResult:
    payload = {
        "summary": "analysis",
        "flows": [{
            "flow_key": "F01",
            "title": "flow",
            "entry": "entry",
            "summary": "flow summary",
            "steps": [
                {
                    "step_key": "S1",
                    "label": "entry",
                    "kind": "entry",
                    "evidence": [{
                        "repo_id": REPO_ID,
                        "path": "src/a.c",
                        "line_start": 1,
                        "observation": "entry",
                    }],
                },
                {
                    "step_key": "S2",
                    "label": "exit",
                    "kind": "exit",
                    "evidence": [{
                        "repo_id": REPO_ID,
                        "path": "src/a.c",
                        "line_start": 2,
                        "observation": "exit",
                    }],
                },
            ],
            "edges": [{"source_step_key": "S1", "target_step_key": "S2"}],
        }],
        "input_decisions": [],
        "coverage_decisions": [],
        "mechanism_decisions": [],
        "risks": [],
        "test_cases": [],
        "review_finding_decisions": [],
        "unresolved": [],
    }
    if linked_input_ids is not None:
        payload["test_cases"] = [{
            "case_key": "TC01",
            "title": "case",
            "basis": basis or ["code_flow"],
            "covered_flow_keys": ["F01"],
            "linked_input_ids": linked_input_ids,
            "linked_risk_keys": [],
            "level": "blackbox",
            "preconditions": ["ready"],
            "steps": [{"action": "run", "expected_result": "ok"}],
            "observability": ["result"],
            "cleanup": ["none"],
        }]
    return UnitSemanticResult.model_validate(payload)


class CodeFlowReferenceTests(unittest.TestCase):
    def test_unknown_edge_reference_is_advisory(self) -> None:
        flow = CodeFlow.model_validate({
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
        with tempfile.TemporaryDirectory() as root:
            result = _semantic_result()
            result.flows = [flow]
            warnings = validate_unit_result(
                _analysis_task(root),
                result,
                {"asset_items": {}, "coverage_gaps": [], "defect_mechanisms": {}},
            )
        self.assertTrue(any("未知 step_key" in item for item in warnings))

    def test_evidence_path_preserves_agent_value(self) -> None:
        evidence = SourceEvidence(
            repo_id=REPO_ID,
            path=r"tls\ntt_x.c",
            line_start=1,
            observation="source",
        )
        self.assertEqual(evidence.path, r"tls\ntt_x.c")


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

        warnings = _validate_review(progress, result)
        self.assertTrue(any("affected_unit_ids" in item for item in warnings))

    def test_unique_basename_suggests_path_without_rewriting(self) -> None:
        progress = _progress(_unit("U03", ["tls/ntt_x.c"]))
        result = IndependentReviewResult.model_validate({
            "summary": "review",
            "findings": [
                _finding(affected_unit_ids=["U03"], path="tls/packet/ntt_x.c")
            ],
            "unresolved": [],
        })

        warnings = _validate_review(progress, result)
        self.assertEqual(
            result.findings[0].evidence[0].path,
            "tls/packet/ntt_x.c",
        )
        self.assertTrue(any("possible_match=tls/ntt_x.c" in item for item in warnings))

    def test_ambiguous_basename_is_degraded_instead_of_guessed(self) -> None:
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

        warnings = _validate_review(progress, result)
        self.assertTrue(any("allowed_paths" in item for item in warnings))

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

        warnings = _validate_comparison_review(
            progress,
            independent,
            comparison,
            selected_inputs={},
        )
        self.assertTrue(any("affected_unit_ids" in item for item in warnings))

    def test_python_does_not_overrule_confirmed_missed_flow(self) -> None:
        progress = _progress(_unit("U01", ["tls/u01.c"]))
        independent = IndependentReviewResult.model_validate({
            "summary": "independent",
            "findings": [{
                **_finding(affected_unit_ids=["U01"], path="tls/u01.c"),
                "category": "missed_flow",
            }],
            "unresolved": [],
        })
        comparison = ComparisonReviewResult.model_validate({
            "summary": "comparison",
            "independent_finding_decisions": [{
                "finding_key": "RF-1",
                "disposition": "confirmed",
                "conclusion": "same lines contain a different state path",
                "evidence": [{
                    "repo_id": REPO_ID,
                    "path": "tls/u01.c",
                    "line_start": 1,
                    "observation": "reviewer confirmed the semantic omission",
                }],
            }],
            "findings": [],
            "unresolved": [],
        })

        _validate_comparison_review(
            progress,
            independent,
            comparison,
            selected_inputs={},
        )


class ResultTrustBoundaryTests(unittest.TestCase):
    def test_missing_coverage_decision_is_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            warnings = validate_unit_result(
                _analysis_task(root),
                _semantic_result(),
                {
                    "asset_items": {},
                    "coverage_gaps": [{"coverage_id": "COV-1"}],
                    "defect_mechanisms": {},
                },
            )
        self.assertTrue(any("coverage_decisions" in item for item in warnings))

    def test_unknown_input_reference_is_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            warnings = validate_unit_result(
                _analysis_task(root),
                _semantic_result(linked_input_ids=["NOT-REAL"]),
                {
                    "asset_items": {},
                    "coverage_gaps": [],
                    "defect_mechanisms": {},
                },
            )
        self.assertTrue(any("未知输入" in item for item in warnings))

    def test_basis_warning_does_not_rewrite_agent_result(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            result = _semantic_result(
                linked_input_ids=["COV-1"],
                basis=["requirement"],
            )
            warnings = validate_unit_result(
                _analysis_task(root),
                result,
                {
                    "asset_items": {},
                    "coverage_gaps": [{"coverage_id": "COV-1"}],
                    "defect_mechanisms": {},
                },
            )
        self.assertEqual(result.test_cases[0].basis, ["requirement"])
        self.assertTrue(any("basis" in item for item in warnings))

    def test_noncanonical_flow_field_is_rejected(self) -> None:
        payload = _semantic_result().model_dump(mode="json")
        payload["flows"][0]["flow_id"] = payload["flows"][0].pop("flow_key")
        with self.assertRaises(ValueError):
            UnitSemanticResult.model_validate(payload)

    def test_coverage_only_selects_owned_source(self) -> None:
        records = [
            {
                "coverage_id": "COV-owned",
                "matches": [{"repo_id": REPO_ID, "path": "src/owned.c"}],
            },
            {
                "coverage_id": "COV-context",
                "matches": [{"repo_id": REPO_ID, "path": "src/context.c"}],
            },
        ]
        selected = _coverage_for_owned_sources(records, {
            "groups": [{
                "repo_id": REPO_ID,
                "code_paths": ["src/owned.c"],
                "context_paths": ["src/context.c"],
            }],
        })
        self.assertEqual(
            [item["coverage_id"] for item in selected],
            ["COV-owned"],
        )


class ActionLifecycleTests(unittest.TestCase):
    def test_public_validate_redirects_to_single_settle_entry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            result = validate_action(root, "RUN-TEST", "RUN-TEST:analysis:U00")

            self.assertEqual(result["status"], "settle_required")
            self.assertIn("pangea_action_settle", result["message"])
            self.assertFalse((Path(root) / "runs").exists())

    def test_workflow_copies_closure_result_and_continues_analysis_worker(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {
                "data_root": root,
                "run_id": "RUN-TEST",
                "task_contract": {"target": "module"},
            }
            run_dir = Path(root) / "runs" / "RUN-TEST"
            task = _analysis_task(root)
            analysis_task_path = (
                run_dir / "agent-tasks" / "analysis" / "U00.json"
            )
            write_json(analysis_task_path, task.model_dump(mode="json"))
            original_result = _semantic_result().model_dump(mode="json")
            write_json(Path(task.result_path), original_result)
            selected_inputs_path = run_dir / "inputs" / "selected-inputs.json"
            write_json(selected_inputs_path, {})

            review_result_path = run_dir / "agent-results" / "review.json"
            write_json(review_result_path, {
                "summary": "independent",
                "findings": [{
                    **_finding(
                        affected_unit_ids=["U00"],
                        path="src/a.c",
                    ),
                    "category": "missed_flow",
                }],
                "unresolved": [],
            })
            write_json(run_dir / "agent-tasks" / "review.json", {
                "task_type": "independent_review",
                "run_id": "RUN-TEST",
                "target": "module",
                "repositories": [{"repo_id": REPO_ID, "source_root": "."}],
                "unit_plan_path": str(run_dir / "inputs" / "unit-plan.json"),
                "inventory_path": str(run_dir / "inputs" / "inventory.json"),
                "source_manifest_path": str(
                    run_dir / "inputs" / "source-manifest.json"
                ),
                "selected_inputs_path": str(selected_inputs_path),
                "rubric_paths": ["rubric.md"],
                "result_path": str(review_result_path),
            })

            comparison_result_path = (
                run_dir / "agent-results" / "comparison-review.json"
            )
            write_json(comparison_result_path, {
                "summary": "comparison",
                "independent_finding_decisions": [{
                    "finding_key": "RF-1",
                    "disposition": "confirmed",
                    "conclusion": "confirmed",
                    "evidence": [{
                        "repo_id": REPO_ID,
                        "path": "src/a.c",
                        "line_start": 1,
                        "observation": "confirmed evidence",
                    }],
                }],
                "findings": [],
                "unresolved": [],
            })
            comparison_task_path = (
                run_dir / "agent-tasks" / "comparison-review.json"
            )
            write_json(comparison_task_path, {
                "task_type": "comparison_review",
                "run_id": "RUN-TEST",
                "target": "module",
                "unit_plan_path": str(run_dir / "inputs" / "unit-plan.json"),
                "analysis_task_paths": {"U00": str(analysis_task_path)},
                "analysis_result_paths": {"U00": task.result_path},
                "independent_review_result_path": str(review_result_path),
                "selected_inputs_path": str(selected_inputs_path),
                "rubric_paths": ["rubric.md"],
                "result_path": str(comparison_result_path),
            })

            analysis_action_id = "RUN-TEST:analysis:U00"
            comparison_action_id = "RUN-TEST:comparison-review"
            comparison_action = ActionState(
                action_id=comparison_action_id,
                action="continue_agent",
                role="review",
                stage="comparison_review",
                task_path=str(comparison_task_path),
                task_id="review-session",
                status="settled",
            )
            progress = WorkflowProgress(
                run_id="RUN-TEST",
                stage="reviewing",
                analysis_units=[task.unit],
                actions={
                    analysis_action_id: ActionState(
                        action_id=analysis_action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(analysis_task_path),
                        task_id="analysis-session",
                        status="accepted",
                    ),
                    comparison_action_id: comparison_action,
                },
            )

            _accept_comparison_review(state, progress, comparison_action)

            closure_result_path = (
                run_dir / "agent-results" / "closure" / "U00.json"
            )
            self.assertEqual(read_json(closure_result_path), original_result)
            self.assertEqual(read_json(Path(task.result_path)), original_result)
            closure_action = progress.actions["RUN-TEST:closure:U00"]
            self.assertEqual(closure_action.action, "continue_agent")
            self.assertEqual(closure_action.task_id, "analysis-session")

    def test_continue_action_cannot_replace_originating_task(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            action_id = "RUN-TEST:closure:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="closing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="continue_agent",
                        role="closure",
                        stage="targeted_closure",
                        task_path="closure-U00.json",
                        task_id="analysis-session",
                    )
                },
            ))

            with self.assertRaisesRegex(ValueError, "禁止替换"):
                bind_action(root, "RUN-TEST", action_id, "replacement-session")
            bound = bind_action(
                root,
                "RUN-TEST",
                action_id,
                "analysis-session",
            )
            self.assertEqual(bound["status"], "dispatched")
            self.assertEqual(
                bind_action(root, "RUN-TEST", action_id, "analysis-session"),
                bound,
            )

    def test_invalid_result_returns_repair_without_settling(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            task = _analysis_task(root)
            task_path = (
                Path(root) / "runs" / "RUN-TEST"
                / "agent-tasks" / "analysis" / "U00.json"
            )
            write_json(task_path, task.model_dump(mode="json"))
            write_json(Path(task.result_path), {
                "schema_version": "1.0",
                "summary": "unfinished",
                "flows": [],
            })
            action_id = "RUN-TEST:analysis:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="analyzing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(task_path),
                        task_id="analysis-session",
                        status="dispatched",
                    )
                },
            ))

            validation = _validate_action(root, "RUN-TEST", action_id)
            self.assertEqual(validation["status"], "invalid")
            self.assertEqual(
                validation["repair_action"]["task_id"],
                "analysis-session",
            )
            settled = settle_action(root, "RUN-TEST", action_id)
            self.assertEqual(settled["validation"]["status"], "invalid")
            progress = load_progress(state)
            assert progress is not None
            self.assertEqual(progress.actions[action_id].status, "dispatched")

    def test_repeated_invalid_result_requests_attention_without_failing_run(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            task = _analysis_task(root)
            task_path = (
                Path(root) / "runs" / "RUN-TEST"
                / "agent-tasks" / "analysis" / "U00.json"
            )
            write_json(task_path, task.model_dump(mode="json"))
            write_json(Path(task.result_path), {"summary": "still incomplete"})
            action_id = "RUN-TEST:analysis:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="analyzing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(task_path),
                        task_id="analysis-session",
                        status="dispatched",
                    )
                },
            ))

            validation = None
            for _ in range(3):
                validation = _validate_action(root, "RUN-TEST", action_id)
            assert validation is not None
            self.assertEqual(validation["status"], "invalid")
            self.assertTrue(validation["attention_required"])
            progress = load_progress(state)
            assert progress is not None
            self.assertEqual(progress.lifecycle_status, "running")
            self.assertEqual(progress.actions[action_id].status, "dispatched")

    def test_cumulative_changing_errors_request_attention(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            task = _analysis_task(root)
            task_path = (
                Path(root) / "runs" / "RUN-TEST"
                / "agent-tasks" / "analysis" / "U00.json"
            )
            write_json(task_path, task.model_dump(mode="json"))
            write_json(Path(task.result_path), {"summary": "still incomplete"})
            action_id = "RUN-TEST:analysis:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="analyzing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(task_path),
                        task_id="analysis-session",
                        status="dispatched",
                    )
                },
            ))

            validation = None
            for attempt in range(6):
                progress = load_progress(state)
                assert progress is not None
                progress.actions[action_id].error = f"different-error-{attempt}"
                save_progress(state, progress)
                validation = _validate_action(root, "RUN-TEST", action_id)
            assert validation is not None
            self.assertTrue(validation["attention_required"])
            self.assertEqual(validation["validation_failures"], 6)
            self.assertEqual(validation["repeated_validation_failures"], 1)

    def test_validation_error_details_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            task = _analysis_task(root)
            task_path = (
                Path(root) / "runs" / "RUN-TEST"
                / "agent-tasks" / "analysis" / "U00.json"
            )
            write_json(task_path, task.model_dump(mode="json"))
            write_json(Path(task.result_path), {"flows": [{} for _ in range(30)]})
            action_id = "RUN-TEST:analysis:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="analyzing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(task_path),
                        task_id="analysis-session",
                        status="dispatched",
                    )
                },
            ))

            validation = _validate_action(root, "RUN-TEST", action_id)
            error = validation["error"]
            self.assertEqual(len(error["details"]), 24)
            self.assertGreater(error["detail_count"], len(error["details"]))
            self.assertTrue(error["details_truncated"])

    def test_successful_repair_preserves_cumulative_failure_count(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            task = _analysis_task(root)
            task_path = (
                Path(root) / "runs" / "RUN-TEST"
                / "agent-tasks" / "analysis" / "U00.json"
            )
            write_json(task_path, task.model_dump(mode="json"))
            write_json(
                Path(task.result_path),
                _semantic_result().model_dump(mode="json"),
            )
            action_id = "RUN-TEST:analysis:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="analyzing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(task_path),
                        task_id="analysis-session",
                        status="dispatched",
                        error="previous schema error",
                        validation_failures=2,
                        repeated_validation_failures=2,
                    )
                },
            ))

            validation = _validate_action(root, "RUN-TEST", action_id)
            self.assertEqual(validation["status"], "valid")
            progress = load_progress(state)
            assert progress is not None
            action = progress.actions[action_id]
            self.assertEqual(action.validation_failures, 2)
            self.assertEqual(action.repeated_validation_failures, 0)
            self.assertIsNone(action.error)

    def test_advisory_result_is_preserved_and_recorded_as_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            task = _analysis_task(root)
            task_path = (
                Path(root) / "runs" / "RUN-TEST"
                / "agent-tasks" / "analysis" / "U00.json"
            )
            write_json(task_path, task.model_dump(mode="json"))
            original = _semantic_result(
                linked_input_ids=["NOT-REAL"],
                basis=["requirement"],
            ).model_dump(mode="json")
            write_json(Path(task.result_path), original)
            action_id = "RUN-TEST:analysis:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="analyzing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(task_path),
                        task_id="analysis-session",
                        status="dispatched",
                    )
                },
            ))

            validation = _validate_action(root, "RUN-TEST", action_id)
            self.assertEqual(validation["status"], "valid")
            self.assertTrue(validation["warnings"])
            self.assertEqual(read_json(Path(task.result_path)), original)
            progress = load_progress(state)
            assert progress is not None
            self.assertTrue(progress.degradations)
            self.assertEqual(
                progress.degradations[0]["action_id"],
                action_id,
            )

    def test_settled_action_resumes_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            action_id = "RUN-TEST:analysis:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="analyzing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path="U00.json",
                        task_id="analysis-session",
                        status="settled",
                    )
                },
            ))
            with patch(
                "pangea_agent.cli.adapter_api.resume_module_analysis",
                return_value={"run_id": "RUN-TEST", "stage": "analyzing"},
            ) as resume:
                result = settle_action(root, "RUN-TEST", action_id)
            resume.assert_called_once_with("RUN-TEST", root)
            self.assertEqual(result["stage"], "analyzing")

    def test_missing_settled_result_cannot_advance_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = {"data_root": root, "run_id": "RUN-TEST"}
            task = _analysis_task(root)
            task_path = (
                Path(root) / "runs" / "RUN-TEST"
                / "agent-tasks" / "analysis" / "U00.json"
            )
            write_json(task_path, task.model_dump(mode="json"))
            action_id = "RUN-TEST:analysis:U00"
            save_progress(state, WorkflowProgress(
                run_id="RUN-TEST",
                stage="analyzing",
                analysis_units=[task.unit],
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(task_path),
                        task_id="analysis-session",
                        status="settled",
                    )
                },
            ))
            with self.assertRaises(FileNotFoundError):
                advance_workflow({
                    **state,
                    "task_contract": {"target": "module"},
                })
            progress = load_progress(state)
            assert progress is not None
            self.assertEqual(progress.lifecycle_status, "failed")
            self.assertFalse(
                any(action.role == "review" for action in progress.actions.values())
            )


if __name__ == "__main__":
    unittest.main()
