"""Mechanical inspection ledger contract; conclusions remain the worker's."""
from pangea_agent.risk_schema import _SOURCE_REFERENCE

CHECK_FORMAT = 'behavior-check-v1'
LENSES = {'error_path', 'resource_lifecycle', 'boundary', 'concurrency', 'untrusted_input', 'contract', 'scope'}
CONCLUSIONS = {'confirmed', 'excluded', 'unresolved', 'not_applicable'}


def check_check_structure(body):
    if not isinstance(body, dict):
        return ['check.body 必须是 JSON 对象']
    errors = []
    if body.get('format_version') != CHECK_FORMAT:
        errors.append(f'format_version 必须是 {CHECK_FORMAT}')
    for key in ('check_id', 'lens', 'object', 'hypothesis', 'conclusion', 'reasoning'):
        if not isinstance(body.get(key), str) or not body[key].strip():
            errors.append(f'{key} 必须是非空字符串')
    if not isinstance(body.get('lens'), str) or body['lens'] not in LENSES:
        errors.append('lens 不是已知检查镜头')
    if not isinstance(body.get('conclusion'), str) or body['conclusion'] not in CONCLUSIONS:
        errors.append('conclusion 必须是 confirmed/excluded/unresolved/not_applicable')
    sites = body.get('sites')
    if not isinstance(sites, list) or not sites or any(not isinstance(s, str) or not _SOURCE_REFERENCE.fullmatch(s) for s in sites):
        errors.append('sites 必须是非空源码引用数组')
    for key in ('signal_ids', 'risk_ids'):
        if key in body and (not isinstance(body[key], list) or any(not isinstance(s, str) or not s for s in body[key])):
            errors.append(f'{key} 必须是字符串数组')
    for key in ('risk_id', 'case_id', 'test_case_id', 'flow_id', 'coverage_id'):
        if key in body:
            errors.append(f'check 不允许顶层 {key}；关联风险请用 risk_ids')
    if body.get('conclusion') in ('confirmed', 'excluded') and not body.get('evidence_quotes'):
        errors.append('confirmed/excluded 的 evidence_quotes 必填')
    return errors
