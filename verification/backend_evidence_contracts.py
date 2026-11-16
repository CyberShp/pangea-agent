"""Deterministic diagnostics and bounded evidence contracts; no model or user Run."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest

from pangea_agent.analysis_scenarios import scene_spec
from pangea_agent.readiness_diagnostics import readiness_diagnostics
from pangea_agent.source_fact_diagnostics import source_fact_diagnostics
from pangea_agent.risk_falsifier import FALSIFIER_CONTRACT, falsifier_diagnostics
from pangea_agent.boundary_reconciliation import reconcile_boundaries
from pangea_agent.graph.result_store import initialize_result, append_records, read_result, active_records, current_result_warnings, ResultStoreError
from pangea_agent.models.source_first import SourceBinding, NoteRecord, NotesResult


class Resolver:
    def lines(self, repo, path):
        if (repo, path) != ('repo', 'src/a.c'):
            raise ValueError('outside frozen source')
        return ['int caller(void) {', '  if (!valid) return EINVAL;', '  return allocate();', '}']


def trace(outcome='counterevidence_found'):
    return {'format_version': FALSIFIER_CONTRACT, 'initial_severity': 'High', 'depth': 1,
            'outcome': outcome, 'reason': 'Caller checks input', 'source_evidence': ['repo:src/a.c:2'],
            'evidence_quotes': [{'ref': 'repo:src/a.c:2', 'snippet': 'if (!valid) return EINVAL;'}]}


def delivery(unit, bodies):
    binding = SourceBinding(data_root='/fixture', run_id='run', action_id=f'action-{unit}', task_id=f'task-{unit}')
    records = [NoteRecord(record_id=f'rec-{i:06d}', kind='risk', body=body, created_revision=1)
               for i, body in enumerate(bodies, 1)]
    return ({'task': {'unit_id': unit}}, NotesResult(binding=binding, revision=1, records=records))


def interface(role, codes, **extra):
    return {'repo_id': 'repo', 'path': 'src/a.c', 'symbol': 'allocate', 'role': role,
            'error_codes': codes, 'source_evidence': ['repo:src/a.c:3'], **extra}


class BackendEvidenceChecks(unittest.TestCase):
    def test_readiness_keeps_semantic_authority(self):
        for action in ('模拟握手超时', '注入内存分配失败 ENOMEM', '模拟磁盘 I/O 错误', '构造并发竞态窗口', 'inject ENOMEM'):
            body = {'execution_readiness': 'ready', 'steps': [{'action': action}]}
            original = deepcopy(body)
            self.assertEqual(readiness_diagnostics(body)[0]['field'], 'body.steps[0].action')
            self.assertEqual(body, original)
        for status in ('unknown', 'needs_instrumentation'):
            self.assertEqual(readiness_diagnostics({'execution_readiness': status, 'preconditions': ['故障注入']}), [])
        self.assertEqual(readiness_diagnostics({'execution_readiness': 'ready', 'steps': [{'action': '公开 API 输入无效参数'}]}), [])

    def test_literal_source_facts(self):
        body = {'source_evidence': ['repo:src/a.c:1-4'], 'entry': {'repo_id': 'repo', 'path': 'src/a.c', 'symbol': 'caller'}}
        self.assertEqual(source_fact_diagnostics(body, Resolver()), [])
        body['source_evidence'] += ['repo:src/a.c:5', 'repo:../secret:1']
        body['entry']['symbol'] = 'call'
        self.assertEqual([x['code'] for x in source_fact_diagnostics(body, Resolver())], ['invalid_source_reference', 'invalid_source_reference', 'symbol_not_found'])

    def test_static_fixture_budget(self):
        start = time.perf_counter()
        for _ in range(1000):
            self.assertEqual(source_fact_diagnostics({'source_evidence': ['repo:src/a.c:1-4']}, Resolver()), [])
        elapsed = time.perf_counter() - start
        print(f'1000 cached small-file source checks: {elapsed:.4f}s (fixture, not a whole-Run bound)')
        self.assertLess(elapsed, 1)

    def test_high_risk_requires_bounded_original_worker_evidence(self):
        self.assertEqual(falsifier_diagnostics({'severity': 'Low'}), [])
        self.assertEqual(falsifier_diagnostics({'severity': 'High'})[0]['code'], 'caller_evidence_pending')
        body = {'severity': 'Medium', 'upstream_falsification': trace()}
        original = deepcopy(body)
        self.assertEqual(falsifier_diagnostics(body, Resolver()), [])
        self.assertEqual(body, original)

    def test_no_counterevidence_keeps_grade_and_literal_quote(self):
        body = {'severity': 'High', 'upstream_falsification': trace('no_counterevidence')}
        self.assertEqual(falsifier_diagnostics(body, Resolver()), [])
        body['severity'] = 'Low'
        self.assertIn('unsupported_grade_change', [x['code'] for x in falsifier_diagnostics(body, Resolver())])
        body['upstream_falsification']['evidence_quotes'][0]['snippet'] = 'invented mutex'
        self.assertIn('caller_quote_mismatch', [x['code'] for x in falsifier_diagnostics(body, Resolver())])

    def test_unresolved_is_not_success_and_cannot_increase_grade(self):
        body = {'severity': 'High', 'upstream_falsification': {**trace('unresolved'), 'depth': 0, 'source_evidence': [], 'evidence_quotes': []}}
        self.assertEqual(falsifier_diagnostics(body, Resolver()), [])
        body['upstream_falsification']['outcome'] = 'no_counterevidence'
        self.assertIn('untraced_outcome', [x['code'] for x in falsifier_diagnostics(body, Resolver())])
        body.update(severity='Critical')
        body['upstream_falsification']['depth'] = 3
        codes = [x['code'] for x in falsifier_diagnostics(body, Resolver())]
        self.assertIn('invalid_depth', codes)
        self.assertIn('grade_increase', codes)

    def test_cross_unit_explicit_errors_only(self):
        producer = delivery('a', [{'risk_id': 'R1', 'interface_assumptions': [interface('producer', ['ENOMEM', 'EINVAL'])]}])
        consumer = delivery('b', [{'risk_id': 'R2', 'interface_assumptions': [interface('consumer', ['EINVAL'])], 'risk_refs': [{'unit_id': 'a', 'risk_id': 'R1'}]}])
        before = deepcopy(consumer[1].model_dump())
        result = reconcile_boundaries([producer, consumer])
        self.assertEqual(result['comparisons'][0]['differing_codes'], ['ENOMEM'])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(consumer[1].model_dump(), before)
        same = delivery('b', [{'interface_assumptions': [interface('consumer', ['EINVAL', 'ENOMEM'])]}])
        self.assertEqual(reconcile_boundaries([producer, same])['comparisons'][0]['status'], 'declared_codes_match')

    def test_unmatched_identity_is_pending_not_inferred(self):
        rows = [delivery('a', [{'interface_assumptions': [interface('producer', ['EIO'])]}]),
                delivery('b', [{'interface_assumptions': [interface('consumer', ['EIO'], path='src/other.c')]}])]
        result = reconcile_boundaries(rows)
        self.assertEqual(result['status'], 'evidence_pending')
        self.assertEqual(result['comparisons'], [])
        self.assertEqual(result['diagnostics'][0]['code'], 'producer_evidence_pending')
        self.assertEqual(reconcile_boundaries([])['status'], 'evidence_pending')

    def test_references_missing_ambiguous_and_superseded(self):
        first = delivery('a', [{'risk_id': 'R'}, {'risk_id': 'R'}])
        second = delivery('b', [{'risk_refs': [{'unit_id': 'a', 'risk_id': 'R'}, {'unit_id': 'a', 'risk_id': 'missing'}]}])
        self.assertEqual([x['code'] for x in reconcile_boundaries([first, second])['diagnostics']], ['reference_ambiguous', 'reference_missing'])
        first[1].records[1].supersedes = [first[1].records[0].record_id]
        self.assertEqual([x['code'] for x in reconcile_boundaries([first, second])['diagnostics']], ['reference_missing'])

    def test_bound_warning_repair_preserves_original_and_no_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            binding = SourceBinding(data_root=directory, run_id='r', action_id='a', task_id='t')
            path = Path(directory) / 'result.json'
            initialize_result(path, binding)
            contracts = {'risk_falsifier': FALSIFIER_CONTRACT, 'source_facts': 'source-facts-v1'}
            body = {'severity': 'High', 'risk_id': 'R'}
            context = SimpleNamespace(resolver=Resolver())
            append_records(path, binding, 0, [{'kind': 'risk', 'body': body}], record_contracts=contracts, context=context)
            stored = read_result(path)
            warning = current_result_warnings(stored)[0]
            self.assertEqual((warning['action_id'], warning['task_id'], warning['record_revision']), ('a', 't', 1))
            fixed = {**body, 'upstream_falsification': trace()}
            append_records(path, binding, 1, [{'kind': 'risk', 'body': fixed, 'supersedes': ['rec-000001']}], record_contracts=contracts, context=context)
            stored = read_result(path)
            self.assertEqual(stored.records[0].body, body)
            self.assertEqual(len(active_records(stored)), 1)
            self.assertEqual(current_result_warnings(stored), [])
            self.assertIsNone(stored.completion)
            with self.assertRaises(ResultStoreError):
                append_records(path, binding.model_copy(update={'task_id': 'other'}), 2, [{'body': 'wrong worker'}])

    def test_legacy_and_new_freezes_remain_separate(self):
        for scene in ('module-analysis', 'risk-analysis', 'branch-analysis', 'coverage-analysis'):
            self.assertEqual(scene_spec(scene)['record_contracts']['risk_falsifier'], FALSIFIER_CONTRACT)
        with tempfile.TemporaryDirectory() as directory:
            binding = SourceBinding(data_root=directory, run_id='r', action_id='a', task_id='t')
            path = Path(directory) / 'legacy.json'
            initialize_result(path, binding)
            append_records(path, binding, 0, [{'kind': 'risk', 'body': {'severity': 'High'}}])
            self.assertEqual(read_result(path).warnings, [])


if __name__ == '__main__':
    unittest.main()
