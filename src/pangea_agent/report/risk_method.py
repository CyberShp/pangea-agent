"""Render the inspection ledger and reviewer declarations verbatim."""
from collections import Counter
import html
import json
import re
from pathlib import Path

from pangea_agent.agent_io import read_json
from pangea_agent.graph.result_store import active_records
from pangea_agent.risk_method_diagnostics import analysis_unit_diagnostics


def quotes_markdown(quotes, language=None):
    lines = []
    for q in quotes if isinstance(quotes, list) else []:
        if not isinstance(q, dict):
            continue
        snippet = str(q.get('snippet', ''))
        fence = '`' * max(3, 1 + max((len(m[0]) for m in re.finditer(r'`+', snippet)), default=0))
        suffix = Path(str(q.get('ref', '')).rsplit(':', 1)[0]).suffix.lower()
        lang = language or {'.lua': 'lua', '.py': 'python', '.pyw': 'python', '.sh': 'sh', '.bash': 'bash', '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp'}.get(suffix, 'text')
        lines.extend([str(q.get('ref', '')), '', f'{fence}{lang}', snippet, fence, ''])
    return '\n'.join(lines)


def ledger_markdown(delivery, anchors):
    def cell(value):
        return str(value if value is not None else '').replace('|', '\\|').replace('\n', ' ')
    all_checks = [r for _, result in delivery for r in active_records(result) if r.kind == 'check' and isinstance(r.body, dict)]
    is_v2 = any(
        (action.get('task', {}).get('record_contracts') or {}).get('risk') == 'behavior-risk-v3'
        or r.body.get('format_version') == 'behavior-check-v2'
        for action, _ in delivery
        for r in (all_checks[:1] or [type('_R', (), {'body': {}})()])
    )
    if is_v2:
        conclusions = ['confirmed', 'needs_runtime_verification', 'excluded', 'unresolved', 'not_applicable']
        counts = Counter((str(r.body.get('track') or r.body.get('lens') or ''), str(r.body.get('conclusion'))) for r in all_checks)
        lines = [
            '## 四轨风险检查账本（risk-method-v2）', '',
            '| 轨道 | 静态铁证 | 待动态实锤 | 已排除 | 待确认 | 不适用 |',
            '|---|---:|---:|---:|---:|---:|',
        ]
        for bucket in sorted({b for b, _ in counts}):
            lines.append('| ' + cell(bucket) + ' | ' + ' | '.join(str(counts[(bucket, c)]) for c in conclusions) + ' |')
    else:
        counts = Counter((str(r.body.get('lens')), str(r.body.get('conclusion'))) for r in all_checks)
        conclusions = ['confirmed', 'excluded', 'unresolved', 'not_applicable']
        lines = ['## 风险检查账本', '', '| 镜头 | 已证实 | 已排除 | 待确认 | 不适用 |', '|---|---:|---:|---:|---:|']
        for lens in sorted({lens for lens, _ in counts}):
            lines.append('| ' + cell(lens) + ' | ' + ' | '.join(str(counts[(lens, c)]) for c in conclusions) + ' |')
    for action, result in delivery:
        task = action.get('task', {})
        uid = task.get('unit_id', '')
        checks = [r for r in active_records(result) if r.kind == 'check' and isinstance(r.body, dict)]
        col_name = '轨道' if is_v2 else '镜头'
        lines.extend(['', f'### {cell(uid)}', '', f'| 编号 | {col_name} | 对象 | 假设 | 结论 | 位置 |', '|---|---|---|---|---|---|'])
        for r in checks:
            b = r.body
            label = cell(b.get('check_id', r.record_id))
            risk_ids = b.get('risk_ids', [])
            if b.get('conclusion') in ('confirmed', 'needs_runtime_verification') and isinstance(risk_ids, list):
                links = [f'[{cell(rid)}](#{anchors[(uid, "risk_id", rid)]})' for rid in risk_ids if isinstance(rid, str) and (uid, 'risk_id', rid) in anchors]
                label += ' ' + ' '.join(links)
            bucket_val = b.get('track') or b.get('lens')
            lines.append('| ' + ' | '.join([label, cell(bucket_val), *(cell(b.get(k)) for k in ('object', 'hypothesis', 'conclusion')), cell(b.get('sites'))]) + ' |')
        for r in checks:
            lines.extend(['', f'<details><summary>{html.escape(str(r.body.get("check_id", r.record_id)))} · 依据与引文</summary>', '', str(r.body.get('reasoning', '')), '', quotes_markdown(r.body.get('evidence_quotes'), {'lua': 'lua', 'python': 'python', 'shell': 'bash'}.get(task.get('analysis_language'))), '</details>', ''])
        for r in active_records(result):
            if r.kind == 'check' and not isinstance(r.body, dict):
                lines.extend(['', '#### 检查原文（格式待核对）', '', quotes_markdown([{'ref': r.record_id, 'snippet': str(r.body)}], 'text')])
        if is_v2:
            from pangea_agent.risk_v2_schema import blueprint_coverage_diagnostics
            bp_input = next((i for i in task.get('inputs', []) if i.get('input_id') == 'risk_blueprint'), None)
            dts_input = next((i for i in task.get('inputs', []) if i.get('input_id') == 'dts_context'), None)
            blueprint = read_json(Path(bp_input['path'])) if bp_input and Path(bp_input['path']).is_file() else None
            dts_ctx = read_json(Path(dts_input['path'])) if dts_input and Path(dts_input['path']).is_file() else None
            hints = blueprint_coverage_diagnostics(task.get('risk_track'), blueprint, active_records(result), dts_ctx)
        else:
            signal_input = next((i for i in task.get('inputs', []) if i.get('input_id') == 'unit_risk_signals'), None)
            signals = read_json(Path(signal_input['path'])) if signal_input else {}
            hints = analysis_unit_diagnostics(task, active_records(result), signals)
        lines.extend(['### 账本提示（仅供复核参考，不代表质量结论）', '', '```json', json.dumps(hints, ensure_ascii=False, indent=2), '```', ''])
        for warning in result.warnings:
            if warning.get('kind') in {'evidence_quote_mismatch', 'risk_contract_hint', 'check_contract_hint'} and warning.get('record_id') in {r.record_id for r in active_records(result)}:
                lines.append(f'- 引文或格式待核对（{warning.get("kind")}）：{cell(warning.get("message"))}')
    return lines, all_checks


def review_markdown(records):
    def table(header, fields, items):
        lines = [header, '|' + '|'.join('---' for _ in fields) + '|']
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                lines.append('|' + '|'.join(str(item.get(k, '')).replace('|', '\\|').replace('\n', ' ') for k in fields) + '|')
        return lines
    lines = ['## 风险检查复核', '']
    for action, result in records:
        stage = action.get('stage')
        if stage == 'independent_review':
            lines.extend(['### 独立盲审探针', ''])
            for r in active_records(result):
                if r.kind == 'check':
                    lines.extend(['```json', json.dumps(r.body, ensure_ascii=False, indent=2), '```', ''])
        if stage != 'comparison_review':
            continue
        from pangea_agent.contract_context import ContractContext
        context = ContractContext(result.binding.data_root, result.binding.run_id, action.get('task', {}), current_records=result.records)
        units, _ = context.review_sources()
        submitted = {r.body['unit_id'] for r in active_records(result) if r.kind == 'note' and isinstance(r.body, dict) and r.body.get('format_version') == 'behavior-review-check-v1' and isinstance(r.body.get('unit_id'), str)}
        lines.extend(f'- 复核交付待核对：{uid} 缺少 behavior-review-check-v1 复核记录（仅提示，不代表质量结论）。' for uid in sorted(set(units) - submitted))
        for r in active_records(result):
            b = r.body
            if r.kind != 'note' or not isinstance(b, dict) or b.get('format_version') != 'behavior-review-check-v1':
                continue
            lines.extend([f'### {b.get("unit_id", "")}', '', *table('| 风险 | 复核结论 | 依据 |', ('risk_id', 'verdict', 'basis'), b.get('risk_verdicts')), '',
                *table('| 探针 | 状态 | 关联 finding |', ('probe_check_id', 'status', 'finding_record_ids'), b.get('recall_items')), '',
                *table('| 抽查 | 结论 | 依据 |', ('check_id', 'verdict', 'basis'), b.get('ledger_spot_checks')), '', str(b.get('summary', '')), ''])
        lines.extend(f'- 复核格式或引用待核对：{w.get("message", w)}' for w in result.warnings if w.get('kind') in {'review_check_reference_hint', 'review_check_contract_hint', 'evidence_quote_mismatch'} and w.get('record_id') in {r.record_id for r in active_records(result)})
    return lines
