"""Inspection coverage hints; never a completeness or quality gate."""
from collections import Counter

from pangea_agent.evidence_quotes import parse_source_ref


def planning_diagnostics(units, regions, summary):
    from pangea_agent.check_schema import LENSES
    owned, context, lens_warnings = {}, {}, []
    for unit in units:
        uid = unit['unit_id']
        owned[uid], context[uid] = set(), set()
        for key, output in (('owned_regions', owned[uid]), ('context_regions', context[uid])):
            for region in unit.get(key, []):
                r = regions.get(region if isinstance(region, str) else region.get('region_id'))
                if r:
                    output.add((r['repo_id'], r['path']))
        for f in unit.get('owned_files', []):
            if isinstance(f, dict) and f.get('repo_id') and f.get('path'):
                owned[uid].add((f['repo_id'], f['path']))
        for f in unit.get('context_files', []):
            if isinstance(f, str) and ':' in f:
                context[uid].add(tuple(f.split(':', 1)))
        lenses = unit.get('risk_lenses', [])
        if isinstance(lenses, list):
            lens_warnings.extend({'unit_id': uid, 'lens': l, 'message': '未知镜头，仅供核对'} for l in lenses if not isinstance(l, str) or l not in LENSES - {'scope'})
    files = {(f['repo_id'], f['path']): f for f in summary.get('files', [])}
    hints = []
    ids = list(owned)
    for i, left in enumerate(ids):
        for right in ids[i+1:]:
            left_fields = {field for pair in owned[left] - context[right] for field in files.get(pair, {}).get('fields', [])}
            right_fields = {field for pair in owned[right] - context[left] for field in files.get(pair, {}).get('fields', [])}
            common = sorted(left_fields & right_fields)[:10]
            if common:
                hints.append({'unit_ids': [left, right], 'fields': common})
    all_owned = set().union(*owned.values()) if owned else set()
    return {'lens_warnings': lens_warnings, 'shared_field_hints': hints[:20],
            'signal_files_without_owner': [f'{r}:{p}' for (r, p), f in sorted(files.items()) if f.get('counts') and (r, p) not in all_owned]}


def analysis_unit_diagnostics(task, records, unit_signals):
    rows = [r.model_dump(mode='json') if hasattr(r, 'model_dump') else r for r in records]
    checks = [r['body'] for r in rows if r.get('kind') == 'check' and isinstance(r.get('body'), dict)]
    risks = {r['body']['risk_id'] for r in rows if r.get('kind') == 'risk' and isinstance(r.get('body'), dict) and isinstance(r['body'].get('risk_id'), str)}
    signal_map = {s['signal_id']: (f['repo_id'], f['path'], s['category'], s.get('role', f.get('role')))
                  for f in unit_signals.get('files', []) for s in f.get('signals', [])}
    covered_files, covered_signals, linked, confirmed_without, counts = set(), set(), set(), [], Counter()
    for check in checks:
        sites = check.get('sites', [])
        for ref in sites if isinstance(sites, list) else []:
            try:
                repo, path, _, _ = parse_source_ref(ref)
                covered_files.add((repo, path))
            except ValueError:
                pass
        ids = check.get('signal_ids', [])
        for sid in ids if isinstance(ids, list) else []:
            if isinstance(sid, str):
                covered_signals.add(sid)
                if sid in signal_map:
                    covered_files.add(signal_map[sid][:2])
        lens, conclusion = check.get('lens'), check.get('conclusion')
        if isinstance(lens, str) and isinstance(conclusion, str):
            counts[(lens, conclusion)] += 1
        if conclusion == 'confirmed':
            refs = check.get('risk_ids', [])
            refs = {r for r in refs if isinstance(r, str)} if isinstance(refs, list) else set()
            linked.update(refs)
            if not refs or refs - risks:
                confirmed_without.append(check.get('check_id'))
    owned = {(r['repo_id'], r['path']) for r in task.get('owned_regions', []) if isinstance(r, dict) and 'repo_id' in r and 'path' in r}
    return {
        'ledger_missing_owned_files': [f'{repo}:{path}' for repo, path in sorted(owned - covered_files)],
        'confirmed_without_risk': confirmed_without,
        'risk_without_confirmed_check': sorted(risks - linked),
        'uncovered_priority_signals': [sid for sid, (_, _, category, role) in signal_map.items()
             if role == 'owned' and category in {'input', 'refcount', 'lock', 'cancel'} and sid not in covered_signals][:20],
        'check_counts': {lens: {c: n for (l, c), n in sorted(counts.items()) if l == lens} for lens, _ in sorted(counts)},
    }
