"""No-review workflow and historical recovery regressions, without model calls."""
import sys
from pathlib import Path

# Embedded Python does not add the script directory to sys.path.
# Keep sibling verification helpers local to this acceptance process.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from copy import deepcopy
import unittest

import observability_recovery as recovery
from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import bind_action, next_actions, settle_action, _validate_action, defer_action
from pangea_agent.cli.public_api import resume_run, run_detail
from pangea_agent.graph.nodes.advance_workflow import advance_workflow
from pangea_agent.graph.result_store import read_result
from pangea_agent.graph.workflow_store import load_progress, save_progress
from pangea_agent.models.analysis import ActionState, WorkflowProgress


class NoReviewWorkflow(unittest.TestCase):
    def setUp(self):
        self.h = recovery.RecoveryChecks()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def finish_analysis(self):
        for action in self.h.analysis:
            self.h.write(action, self.h.bind(action))

    def historical_action(self, stage='independent_review', status='pending', *, changed=False):
        """Persist an old, explicitly bound action; do not execute obsolete routing."""
        self.finish_analysis()
        h = self.h
        progress = h.progress()
        progress.pop('review_policy', None)
        progress.update(lifecycle_status='running', stage='closing' if stage == 'targeted_closure' else 'reviewing')
        origin = h.task(h.analysis[0])
        action_id = h.run_id + ':historical-' + stage
        task = deepcopy(origin)
        task.update(action_id=action_id, task_type='source_first_closure' if stage == 'targeted_closure' else 'source_first_review',
                    review_stage=stage, base_revision=1, result_path=str(h.run / 'agent-results' / (stage+'.json')))
        task_path = h.run / 'agent-tasks' / (stage+'.json')
        write_json(task_path, task)
        result = read_json(Path(origin['result_path']))
        worker = 'historical-worker' if status != 'pending' or stage == 'targeted_closure' else None
        result['binding'].update(action_id=action_id, task_id=worker or 'pending')
        result['completion'] = None
        if changed:
            result['revision'] += 1
            result['records'].append({'record_id': 'historical-correction', 'kind': 'note', 'body': 'Keep existing correction', 'created_revision': result['revision']})
        write_json(Path(task['result_path']), result)
        progress['actions'][action_id] = ActionState(action_id=action_id, action='continue_agent' if worker else 'dispatch_agent',
            role='closure' if stage == 'targeted_closure' else 'review', stage=stage, task_id=worker,
            task_path=str(task_path), status=status).model_dump(mode='json')
        write_json(h.run / 'progress.json', progress)
        return action_id, task, worker

    def test_analysis_completes_without_review_and_has_truthful_report(self):
        self.finish_analysis()
        progress = self.h.progress()
        self.assertEqual(progress['stage'], 'complete')
        self.assertEqual(progress['review_policy'], 'disabled')
        self.assertEqual(progress['quality_status'], 'UNRESOLVED')
        self.assertEqual({a['role'] for a in progress['actions'].values()}, {'planning', 'analysis'})
        self.assertFalse(next_actions(str(self.h.root), self.h.run_id)['actions'])
        report = Path(progress['report_path']).read_text()
        self.assertIn('未执行模型审核', report)
        self.assertEqual(run_detail(str(self.h.root), self.h.run_id)['review_policy'], 'disabled')

    def test_pending_old_review_not_dispatchable_and_resume_preserves_bytes(self):
        action, task, _ = self.historical_action()
        before = Path(task['result_path']).read_bytes()
        self.assertEqual(next_actions(str(self.h.root), self.h.run_id)['actions'], [])
        with self.assertRaisesRegex(ValueError, 'MODEL_REVIEW_DISABLED'):
            bind_action(str(self.h.root), self.h.run_id, action, 'new-reviewer')
        output = resume_run(str(self.h.root), self.h.run_id)
        self.assertEqual(output['lifecycle_status'], 'complete')
        self.assertEqual(Path(task['result_path']).read_bytes(), before)
        retired = self.h.progress()['actions'][action]
        self.assertEqual(retired['status'], 'paused')
        self.assertTrue(retired['review_skipped'])
        self.assertNotIn(action, self.h.progress()['accepted_revisions'])

    def test_running_review_requires_quiescence_and_never_requeues(self):
        action, task, worker = self.historical_action('comparison_review', 'dispatched')
        self.assertTrue(resume_run(str(self.h.root), self.h.run_id)['requires_host_quiescence'])
        with self.assertRaisesRegex(ValueError, 'MODEL_REVIEW_DISABLED'):
            _validate_action(str(self.h.root), self.h.run_id, action)
        with self.assertRaisesRegex(ValueError, 'MODEL_REVIEW_DISABLED'):
            defer_action(str(self.h.root), self.h.run_id, action, worker, reason_code='worker_error', reason='interrupted')
        output = resume_run(str(self.h.root), self.h.run_id, host_quiescent=True)
        self.assertEqual(output['lifecycle_status'], 'complete')
        self.assertFalse(output.get('agent_actions'))
        detail = run_detail(str(self.h.root), self.h.run_id)
        self.assertFalse(detail['execution_view']['recovery']['resume_from'])
        self.assertFalse(detail['execution_view']['unresolved'])

    def test_in_progress_closure_delivers_existing_changes_without_new_worker(self):
        action, task, _ = self.historical_action('targeted_closure', 'dispatched', changed=True)
        before = Path(task['result_path']).read_bytes()
        output = resume_run(str(self.h.root), self.h.run_id, host_quiescent=True)
        self.assertEqual(output['lifecycle_status'], 'complete')
        self.assertEqual(Path(task['result_path']).read_bytes(), before)
        retired = self.h.progress()['actions'][action]
        self.assertEqual(retired['delivery_revision'], read_result(task['result_path']).revision)
        self.assertNotIn(action, self.h.progress()['accepted_revisions'])
        self.assertIn('Keep existing correction', Path(output['report_path']).read_text())
        self.assertEqual(run_detail(str(self.h.root), self.h.run_id)['execution_view']['unit_counts']['completed'], 2)

    def test_closure_binding_and_path_safety_still_block(self):
        action, task, _ = self.historical_action('targeted_closure', 'dispatched', changed=True)
        result = read_json(Path(task['result_path']))
        result['binding']['run_id'] = 'wrong-run'
        write_json(Path(task['result_path']), result)
        with self.assertRaisesRegex(ValueError, '绑定不一致'):
            resume_run(str(self.h.root), self.h.run_id, host_quiescent=True)
        self.assertEqual(self.h.progress()['stage'], 'closing')
        result['binding']['run_id'] = self.h.run_id
        write_json(Path(task['result_path']), result)
        action_data = self.h.progress()['actions'][action]
        task['result_path'] = str(self.h.root / 'outside-result.json')
        write_json(Path(action_data['task_path']), task)
        with self.assertRaises(ValueError):
            resume_run(str(self.h.root), self.h.run_id, host_quiescent=True)

    def test_completed_history_is_not_rewritten_on_resume(self):
        self.finish_analysis()
        progress = self.h.progress()
        progress.pop('review_policy', None)
        progress['quality_status'] = 'PASS'
        write_json(self.h.run / 'progress.json', progress)
        before = {path: path.read_bytes() for path in [self.h.run / 'progress.json', Path(progress['report_path'])]}
        resumed = resume_run(str(self.h.root), self.h.run_id)
        self.assertEqual(resumed['quality_status'], 'PASS')
        self.assertIsNone(resumed['review_policy'])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_legacy_review_stage_advances_without_any_pending_result(self):
        state = {'data_root': str(self.h.root), 'run_id': 'legacy-test', 'workflow_version': 'legacy-v1'}
        progress = WorkflowProgress(run_id='legacy-test', workflow_version='legacy-v1', stage='reviewing')
        progress.actions['legacy-review'] = ActionState(action_id='legacy-review', action='dispatch_agent', role='review',
            stage='independent_review', task_path=str(self.h.root / 'absent-history.json'))
        save_progress(state, progress)
        output = advance_workflow(state)
        self.assertTrue(output['ready_to_finalize'])
        self.assertEqual(output['agent_actions'], [])
        self.assertTrue(load_progress(state).actions['legacy-review'].review_skipped)


if __name__ == '__main__':
    unittest.main()
