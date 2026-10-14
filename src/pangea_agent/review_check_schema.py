"""Review ledger structure and exact references, without judging verdicts."""
REVIEW_CHECK_FORMAT = 'behavior-review-check-v1'


def check_review_check_structure(body):
    if not isinstance(body, dict):
        return ['review_check.body 必须是 JSON 对象']
    errors = []
    if body.get('format_version') != REVIEW_CHECK_FORMAT:
        errors.append(f'format_version 必须是 {REVIEW_CHECK_FORMAT}')
    for key in ('unit_id', 'version_set_id', 'summary'):
        if not isinstance(body.get(key), str) or not body[key].strip():
            errors.append(f'{key} 必须是非空字符串')
    for field, ids, enum_key, choices, lists in (
        ('risk_verdicts', ('record_id', 'risk_id'), 'verdict', {'confirmed', 'refuted', 'uncertain'}, ('finding_record_ids',)),
        ('recall_items', ('probe_check_id',), 'status', {'covered', 'missed', 'not_applicable'}, ('analysis_record_ids', 'finding_record_ids')),
        ('ledger_spot_checks', ('record_id', 'check_id'), 'verdict', {'agree', 'disagree'}, ()),
    ):
        if not isinstance(body.get(field), list):
            errors.append(f'{field} 必须是数组')
            continue
        for i, item in enumerate(body[field]):
            prefix = f'{field}[{i}]'
            if not isinstance(item, dict):
                errors.append(f'{prefix} 必须是对象')
                continue
            for key in (*ids, 'basis'):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    errors.append(f'{prefix}.{key} 必须是非空字符串')
            if not isinstance(item.get(enum_key), str) or item[enum_key] not in choices:
                errors.append(f'{prefix}.{enum_key} 取值错误')
            for key in lists:
                if not isinstance(item.get(key), list) or any(not isinstance(s, str) or not s for s in item[key]):
                    errors.append(f'{prefix}.{key} 必须是字符串数组')
    return errors


def check_review_references(body, context):
    errors = []
    if context.task.get('review_stage') != 'comparison_review':
        errors.append('复核记录只允许 comparison_review 阶段')
        return errors
    if body.get('version_set_id') != context.task.get('version_set_id'):
        errors.append('version_set_id 与 task 不一致')
    unit = body.get('unit_id')
    units, probes = context.review_sources()
    if unit not in units:
        return [*errors, f'未知 unit_id：{unit}']
    records = units[unit]
    findings = context.current_active('finding')
    def finding_refs(item, prefix):
        for ref in item.get('finding_record_ids', []):
            record = findings.get(ref)
            if record is None or unit not in (record.relates_to if isinstance(record.relates_to, list) else []):
                errors.append(f'{prefix}.finding_record_ids：{ref} 不存在或不属于 {unit}')
    for field, kind, id_key in (('risk_verdicts', 'risk', 'risk_id'), ('ledger_spot_checks', 'check', 'check_id')):
        for i, item in enumerate(body.get(field, [])):
            record = records.get(item.get('record_id'))
            prefix = f'{field}[{i}]'
            if record is None or record.kind != kind:
                errors.append(f'{prefix}.record_id 不存在于本单元锁定的 active {kind}')
            elif not isinstance(record.body, dict) or record.body.get(id_key) != item.get(id_key):
                errors.append(f'{prefix}.{id_key} 与所引记录不一致')
            finding_refs(item, prefix)
    for i, item in enumerate(body.get('recall_items', [])):
        prefix = f'recall_items[{i}]'
        if (unit, item.get('probe_check_id')) not in probes:
            errors.append(f'{prefix}.probe_check_id 不存在于本单元盲审或当前 comparison 检查')
        for ref in item.get('analysis_record_ids', []):
            if ref not in records:
                errors.append(f'{prefix}.analysis_record_ids：{ref} 不存在于本单元锁定结果')
        finding_refs(item, prefix)
    return errors
