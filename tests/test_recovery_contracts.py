from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pangea_agent.agent_io import write_json
from pangea_agent.cli.adapter_api import (
    bind_action,
    next_actions,
    settle_action,
    validate_action,
)
from pangea_agent.graph.nodes.advance_workflow import advance_workflow
from pangea_agent.graph.workflow_store import load_progress, save_progress
from pangea_agent.models.analysis import ActionState, WorkflowProgress


class RecoveryContractTests(unittest.TestCase):
    def _state(self, root: str, run_id: str = "RUN-RECOVERY") -> dict:
        return {"data_root": root, "run_id": run_id}

    def _write_analysis_task(self, state: dict, *, status: str = "dispatched") -> tuple[str, Path]:
        run_dir = Path(state["data_root"], "runs", state["run_id"])
        task_path = run_dir / "agent-tasks" / "analysis" / "U01.json"
        selected_inputs_path = run_dir / "inputs" / "units" / "U01.json"
        missing_result_path = run_dir / "agent-results" / "analysis" / "U01.json"
        write_json(selected_inputs_path, {
            "asset_items": {},
            "defect_mechanisms": {},
            "coverage_gaps": [],
            "test_case_examples": [],
        })
        write_json(task_path, {
            "schema_version": "1.0",
            "task_type": "analysis",
            "run_id": state["run_id"],
            "target": "module",
            "unit": {
                "unit_id": "U01",
                "repo_id": "repo",
                "title": "unit",
                "source_scope": ["src/a.c"],
                "context_scope": [],
                "rationale": "test",
                "asset_item_ids": [],
                "coverage_ids": [],
                "mechanism_ids": [],
                "line_count": 1,
                "function_count": 1,
            },
            "repository": {
                "repo_id": "repo",
                "source_root": ".",
                "git": {},
            },
            "inventory_path": str(run_dir / "inputs" / "inventory.json"),
            "source_manifest_path": str(run_dir / "inputs" / "source-manifest.json"),
            "selected_inputs_path": str(selected_inputs_path),
            "coverage_context": [],
            "result_schema_path": "schemas/analysis_result.schema.json",
            "result_path": str(missing_result_path),
            "rubric_paths": ["rubric.md"],
        })
        action_id = f'{state["run_id"]}:analysis:U01'
        save_progress(
            state,
            WorkflowProgress(
                run_id=state["run_id"],
                stage="analyzing",
                actions={
                    action_id: ActionState(
                        action_id=action_id,
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path=str(task_path),
                        task_id="analysis-agent-1",
                        status=status,
                    )
                },
            ),
        )
        return action_id, missing_result_path

    def test_closure_reuses_originating_analysis_worker(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = self._state(root)
            Path(root, "runs", state["run_id"]).mkdir(parents=True)
            progress = WorkflowProgress(
                run_id=state["run_id"],
                stage="closing",
                actions={
                    f'{state["run_id"]}:analysis:U01': ActionState(
                        action_id=f'{state["run_id"]}:analysis:U01',
                        action="dispatch_agent",
                        role="analysis",
                        stage="unit_analysis",
                        task_path="analysis-U01.json",
                        task_id="analysis-agent-1",
                        status="accepted",
                    ),
                    f'{state["run_id"]}:closure:U01': ActionState(
                        action_id=f'{state["run_id"]}:closure:U01',
                        action="dispatch_agent",
                        role="closure",
                        stage="targeted_closure",
                        task_path="closure-U01.json",
                        status="pending",
                    ),
                },
            )
            save_progress(state, progress)

            exposed = next_actions(root, state["run_id"])["actions"]
            self.assertEqual(len(exposed), 1)
            self.assertEqual(exposed[0]["action"], "continue_agent")
            self.assertEqual(exposed[0]["task_id"], "analysis-agent-1")

            with self.assertRaisesRegex(ValueError, "禁止新建或替换 worker"):
                bind_action(
                    root,
                    state["run_id"],
                    f'{state["run_id"]}:closure:U01',
                    "replacement-agent",
                )

            bound = bind_action(
                root,
                state["run_id"],
                f'{state["run_id"]}:closure:U01',
                "analysis-agent-1",
            )
            self.assertEqual(bound["action"], "continue_agent")
            self.assertEqual(bound["task_id"], "analysis-agent-1")
            stored = load_progress(state)
            assert stored is not None
            closure = stored.actions[f'{state["run_id"]}:closure:U01']
            self.assertEqual(closure.status, "dispatched")
            self.assertEqual(closure.action, "continue_agent")
            self.assertEqual(closure.task_id, "analysis-agent-1")

    def test_accepted_action_validation_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = self._state(root)
            Path(root, "runs", state["run_id"]).mkdir(parents=True)
            action_id = f'{state["run_id"]}:analysis:U01'
            save_progress(
                state,
                WorkflowProgress(
                    run_id=state["run_id"],
                    stage="reviewing",
                    actions={
                        action_id: ActionState(
                            action_id=action_id,
                            action="dispatch_agent",
                            role="analysis",
                            stage="unit_analysis",
                            task_path="already-accepted.json",
                            task_id="analysis-agent-1",
                            status="accepted",
                        )
                    },
                ),
            )

            validated = validate_action(root, state["run_id"], action_id)
            self.assertEqual(validated["status"], "valid")
            self.assertTrue(validated["already_accepted"])

    def test_settled_action_can_be_settled_again_before_batch_advances(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = self._state(root)
            Path(root, "runs", state["run_id"]).mkdir(parents=True)
            action_id = f'{state["run_id"]}:analysis:U01'
            save_progress(
                state,
                WorkflowProgress(
                    run_id=state["run_id"],
                    stage="analyzing",
                    actions={
                        action_id: ActionState(
                            action_id=action_id,
                            action="dispatch_agent",
                            role="analysis",
                            stage="unit_analysis",
                            task_path="settled.json",
                            task_id="analysis-agent-1",
                            status="settled",
                        )
                    },
                ),
            )

            with patch(
                "pangea_agent.cli.adapter_api.resume_module_analysis",
                return_value={
                    "run_id": state["run_id"],
                    "data_root": root,
                    "stage": "analyzing",
                    "agent_actions": [],
                },
            ) as resume:
                result = settle_action(root, state["run_id"], action_id)

            resume.assert_called_once_with(state["run_id"], root)
            self.assertEqual(result["stage"], "analyzing")

    def test_missing_canonical_analysis_result_returns_repair_action(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = self._state(root)
            action_id, _ = self._write_analysis_task(state)

            validation = validate_action(root, state["run_id"], action_id)

            self.assertEqual(validation["status"], "invalid")
            self.assertTrue(validation["recoverable"])
            self.assertEqual(validation["repair_action"]["action"], "continue_agent")
            self.assertEqual(
                validation["repair_action"]["task_id"], "analysis-agent-1"
            )
            self.assertEqual(validation["repair_action"]["action_id"], action_id)
            stored = load_progress(state)
            assert stored is not None
            self.assertEqual(stored.lifecycle_status, "running")
            self.assertEqual(stored.actions[action_id].status, "dispatched")
            self.assertIsNotNone(stored.actions[action_id].error)

    def test_settle_does_not_advance_when_validation_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = self._state(root)
            action_id, _ = self._write_analysis_task(state)

            with patch(
                "pangea_agent.cli.adapter_api.resume_module_analysis"
            ) as resume:
                result = settle_action(root, state["run_id"], action_id)

            resume.assert_not_called()
            self.assertEqual(result["validation"]["status"], "invalid")
            self.assertEqual(result["agent_actions"][0]["action"], "continue_agent")
            self.assertEqual(
                result["agent_actions"][0]["task_id"], "analysis-agent-1"
            )
            stored = load_progress(state)
            assert stored is not None
            self.assertEqual(stored.lifecycle_status, "running")
            self.assertEqual(stored.actions[action_id].status, "dispatched")

    def test_missing_analysis_result_cannot_silently_skip_review(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            state = self._state(root)
            action_id, missing_result = self._write_analysis_task(state, status="settled")
            self.assertFalse(missing_result.exists())

            with self.assertRaises(FileNotFoundError):
                advance_workflow({
                    **state,
                    "task_contract": {"target": "module"},
                })

            failed = load_progress(state)
            assert failed is not None
            self.assertEqual(failed.lifecycle_status, "failed")
            self.assertEqual(failed.actions[action_id].status, "failed")
            self.assertFalse(
                any(action.role == "review" for action in failed.actions.values())
            )


if __name__ == "__main__":
    unittest.main()
