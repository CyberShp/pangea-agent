"""Lazy, Run-bound data used for deterministic contract diagnostics."""
from dataclasses import dataclass, field
from pathlib import Path

from pangea_agent.agent_io import read_json
from pangea_agent.evidence_quotes import FrozenSourceResolver


@dataclass
class ContractContext:
    data_root: str
    run_id: str
    task: dict
    current_records: list = field(default_factory=list)
    incoming_relates_to: list = field(default_factory=list)

    @property
    def run_dir(self):
        return Path(self.data_root) / 'runs' / self.run_id

    @property
    def resolver(self):
        if not hasattr(self, '_resolver'):
            self._resolver = FrozenSourceResolver(self.data_root, self.run_id, self.task)
        return self._resolver

    @property
    def signal_ids(self):
        if not hasattr(self, '_signal_ids'):
            self._signal_ids = {s['signal_id'] for p in sorted((self.run_dir / 'inputs/risk-signals').glob('*.json'))
                                for f in read_json(p).get('files', []) for s in f.get('signals', [])}
        return self._signal_ids

    @property
    def unit_ids(self):
        return {u['unit_id'] for u in read_json(self.run_dir / 'inputs/source-first-plan.json').get('units', [])}

    def current_active(self, kind=None):
        from pangea_agent.graph.result_store import active_records
        from pangea_agent.models.source_first import NotesResult, SourceBinding
        result = NotesResult(binding=SourceBinding(data_root=self.data_root, run_id=self.run_id,
                             action_id='context', task_id='context'), revision=0, records=self.current_records)
        return {r.record_id: r for r in active_records(result) if kind is None or r.kind == kind}

    def review_sources(self):
        from pangea_agent.graph.result_store import active_records, read_result
        if not hasattr(self, '_review_sources'):
            path = Path(self.task.get('version_set_path') or self.run_dir / 'inputs/source-first-comparison-version-set.json')
            path.resolve().relative_to(self.run_dir.resolve())
            payload = read_json(path)
            units, probes, required_probes = {}, set(), set()
            for entry in payload.get('entries', []):
                source = Path(entry['result_path'])
                source.resolve().relative_to(self.run_dir.resolve())
                result = read_result(source)
                locked = result.model_copy(update={'records': [r for r in result.records if r.created_revision <= entry['revision']]})
                records = active_records(locked)
                if entry.get('role') == 'analysis':
                    units[entry['unit_id']] = {r.record_id: r for r in records}
                elif entry.get('role') == 'independent_review':
                    probes.update((r.body['unit_id'], r.body['check_id']) for r in records if r.kind == 'check' and isinstance(r.body, dict) and all(isinstance(r.body.get(k), str) for k in ('unit_id', 'check_id')))
                    required_probes.update((r.body['unit_id'], r.body['check_id']) for r in records if r.kind == 'check' and isinstance(r.body, dict) and r.body.get('conclusion') in ('confirmed', 'unresolved') and all(isinstance(r.body.get(k), str) for k in ('unit_id', 'check_id')))
            self._review_sources = units, probes
            self._required_probes = required_probes
        units, probes = self._review_sources
        probes = probes | {(r.body['unit_id'], r.body['check_id']) for r in self.current_active('check').values() if isinstance(r.body, dict) and all(isinstance(r.body.get(k), str) for k in ('unit_id', 'check_id'))}
        return units, probes

    @property
    def required_probes(self):
        self.review_sources()
        return self._required_probes | {(r.body['unit_id'], r.body['check_id']) for r in self.current_active('check').values() if isinstance(r.body, dict) and r.body.get('conclusion') in ('confirmed', 'unresolved') and all(isinstance(r.body.get(k), str) for k in ('unit_id', 'check_id'))}
