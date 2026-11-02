"""Frozen gap facts and per-case rationale stay visible without semantic verdicts."""
import copy
import json
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pangea_agent.report.source_first import _behavior_markdown, _case_markdown, _coverage_summary, _html


def record(body):
    return SimpleNamespace(record_id='case', kind='test_case', body=body,
                           evidence=[], relates_to=[], supersedes=[])


body = {'format_version': 'behavior-test-case-v1', 'case_id': 'C-1',
        'title': '保留正常入口并注入失败', 'purpose': 'coverage',
        'test_level': 'business_blackbox', 'execution_readiness': 'ready',
        'summary': 'G1: 普通操作不能触发失败；新增断网条件。\nG2: 恢复后重试。',
        'steps': [{'action': '正常请求后断网', 'expected': '外部可见失败，清理完成'}],
        'coverage_refs': ['G1', 'G2']}
gaps = [{'coverage_id': 'G1', 'asset_id': 'A1', 'source': 'measurement-A',
         'path': 'src/request.c', 'function': 'request', 'line': 21,
         'coverage_type': 'branch', 'branch_id': 'b7', 'true_count': 0, 'false_count': 9,
         'meaning': 'branch_execution_reference_only'},
        {'coverage_id': 'G2', 'asset_id': 'A1', 'source': 'measurement-A',
         'path': 'src/retry.c', 'function': 'retry', 'coverage_type': 'function', 'count': 0}]
sources = [{'asset_id': 'A1', 'warnings': ['旧批次，版本未核实'], 'scope': 'module-a'}]
md = _case_markdown(record(body), coverage_gaps=gaps, coverage_sources=sources)
for text in (body['summary'], 'src/request.c', 'src/retry.c', 'measurement-A', 'b7',
             '**true 计数**：0', '**false 计数**：9', '**执行计数**：0',
             '旧批次，版本未核实', '正常请求后断网'):
    assert text in md, text
assert '无法核实' not in md

missing = copy.deepcopy(body)
missing.pop('summary')
missing['coverage_refs'] = []
text = _case_markdown(record(missing), coverage_gaps=[])
assert '未提供逐用例 summary' in text and '依据待核实' in text
assert '正常请求后断网' in text  # no filtering, relabeling or deletion of authored actions
for refs in (['invented'], [None], 'G1', [{'coverage_id': ''}]):
    missing['coverage_refs'] = refs
    text = _case_markdown(record(missing), coverage_gaps=gaps)
    assert '依据待核实' in text and '正常请求后断网' in text
assert '不可读取，无法核实' in _case_markdown(record(body))

conflict = copy.deepcopy(gaps[0])
conflict['path'] = 'conflicting/path.c'
text = _case_markdown(record(body), coverage_gaps=[*gaps, conflict], coverage_sources=sources)
assert '编号重复或冲突，无法唯一核实' in text
assert 'conflicting/path.c' in text and 'src/request.c' in text
assert '版本、批次及来源限制未核实' in _case_markdown(record(body), coverage_gaps=gaps)

branch = copy.deepcopy(body)
branch['purpose'] = 'branch'
branch['coverage_refs'] = []
branch.pop('summary')
assert '冻结缺口事实' not in _case_markdown(record(branch), coverage_gaps=[])
assert '未提供逐用例 summary' not in _case_markdown(record(branch), coverage_gaps=[])
assert 'legacy text' in _case_markdown(record('legacy text'))
assert 'old-format detail' in _case_markdown(record({'description': 'old-format detail'}))

with tempfile.TemporaryDirectory() as td:
    state = {'data_root': td, 'run_id': 'explicit-run'}
    inputs = Path(td) / 'runs' / 'explicit-run' / 'inputs'
    inputs.mkdir(parents=True)
    (inputs / 'coverage-gaps.json').write_text(json.dumps([*gaps, conflict]))
    (inputs / 'coverage-diagnostics.json').write_text(json.dumps(sources))
    stats = _coverage_summary(state, [record(body)])
    assert stats['valid_gaps'] == 1 and stats['linked_valid_gaps'] == 1
    assert stats['unverified_refs'] == 1 and stats['coverage_cases'] == 1
    action = {'stage': 'unit_analysis', 'status': 'accepted', 'task': {'unit_id': 'u'}}
    result = SimpleNamespace(records=[record(body)], warnings=[])
    report = _behavior_markdown(state, {}, [(action, result)])
    assert body['summary'] in report and 'conflicting/path.c' in report
    assert '非已验证补测' in report and '作者声明 coverage' in report
    assert report.count('关联有依据不等于实际命中') == 1
    assert '## 覆盖率补测用例' in report
    assert '<details><summary>来源与限制（冻结记录）</summary>' in report
    assert '旧批次，版本未核实' in _html(report)
    # Zero frozen gaps is not permission to delete a normal-entry candidate.
    (inputs / 'coverage-gaps.json').write_text('[]')
    report = _behavior_markdown(state, {}, [(action, result)])
    assert '正常请求后断网' in report and '依据待核实' in report
    assert _coverage_summary(state, [record(body)])['linked_cases'] == 0
    (inputs / 'coverage-gaps.json').write_text('[null]')
    assert _coverage_summary(state, [record(body)])['valid_gaps'] is None
    assert '形态错误' in _behavior_markdown(state, {}, [(action, result)])
    # Damaged input is unknown, never reported as zero confirmed gaps.
    (inputs / 'coverage-gaps.json').write_text('{broken')
    assert _coverage_summary(state, [record(body)])['valid_gaps'] is None
    assert '不可读取，无法核实' in _behavior_markdown(state, {}, [(action, result)])

# Author headings must not terminate the case's HTML detail before its gap facts.
class RecordText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'details':
            self.details.append(dict(attrs).get('id'))

    def handle_endtag(self, tag):
        if tag == 'details':
            self.details.pop()

    def handle_data(self, data):
        self.text.append((data, tuple(self.details)))


for level in (1, 2, 3):
    authored = copy.deepcopy(body)
    authored['summary'] = '#' * level + ' 缺口依据\n说明'
    case_md = _case_markdown(record(authored), coverage_gaps=gaps, coverage_sources=sources)
    assert not any(line.startswith(('# ', '## ', '### ')) for line in case_md.splitlines())
    page = _html('## 覆盖率补测用例\n\n### `u` · case <!--pangea-record:record-u-case-->\n\n' + case_md)
    parsed = RecordText()
    parsed.feed(page)
    for expected in ('缺口依据', '说明', 'src/request.c', 'src/retry.c', '旧批次，版本未核实'):
        matches = [owners for text, owners in parsed.text if expected in text]
        assert matches and all('record-u-case' in owners for owners in matches), (level, expected)
    assert '<a href="#section-1">覆盖率补测用例</a>' in page
assert '<a href="#section-1">未覆盖用例</a>' in _html('## 未覆盖用例\n旧报告')

print('PASS: case summary, multiple frozen gaps/source limitations, empty/invalid/ambiguous refs, zero gaps, legacy and branch compatibility')
