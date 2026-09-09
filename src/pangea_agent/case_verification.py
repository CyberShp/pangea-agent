"""Collect execution evidence for reviewer-authored C probes; never judge Run quality."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from pangea_agent.native_case_process import run_probe


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root, path):
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise ValueError('Verification path leaves the bound Run')
    return resolved


def _probe_path(root, value):
    if not isinstance(value, str) or Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('Probe must be a relative C file in the verification plan directory')
    result = _inside(root, root / value)
    if not result.is_file() or result.suffix.lower() != '.c' or result.stat().st_size > 1024 * 1024:
        raise ValueError('Probe must be a C file no larger than 1 MiB')
    return result


def _compile_and_run(probe, source, work, *, cancelled=lambda: False):
    compiler = os.environ.get('PANGEA_CASE_CC', '').strip()
    if os.name != 'nt' or not compiler or not Path(compiler).is_file():
        return {'status': 'unverified', 'reason': 'Requires Windows restricted execution and an existing compiler configured by PANGEA_CASE_CC'}
    compiler = str(Path(compiler).resolve())
    binary, log = work / 'probe.exe', work / 'compile.txt'
    command = [compiler, *(['cc'] if Path(compiler).stem.lower() == 'zig' else []),
               '-std=c11', '-O0', '-Wall', '-Wextra', '-I', str(source), str(probe), '-o', str(binary)]
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        'SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}}
    # The configured compiler is trusted and uses its existing standard-library
    # cache. The generated program receives a separate restricted environment.
    environment.update(TEMP=str(work), TMP=str(work))
    status = 'executed'
    with log.open('wb') as output:
        process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, creationflags=0x08000000)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled(): status = 'cancelled'
                elif time.monotonic() - started >= 45: status = 'timed_out'
                elif log.stat().st_size > 262144: status = 'output_limit'
                if status != 'executed':
                    break
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'), '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True, creationflags=0x08000000)
                process.wait(timeout=10)
    with log.open('rb') as output:
        compiler_output = output.read(262144).decode('utf-8', errors='replace')
    build = {'command': command, 'compiler': compiler, 'compile_exit_code': process.returncode,
             'compile_output': compiler_output}
    if status != 'executed':
        return {'status': status, 'phase': 'compile', **build}
    if process.returncode or not binary.is_file() or log.stat().st_size > 262144:
        return {'status': 'unverified', 'reason': 'Compilation failed, produced no executable, or exceeded output limit', **build}
    # Only the resulting program runs under the capability-free token. The trusted
    # configured compiler receives fixed arguments; no project hooks or shell run.
    return {**run_probe(binary, work, source, timeout=10, cancelled=cancelled), **build, 'binary_sha256': _digest(binary)}


def verify_cases(data_root, run_id, review_request_id, *, formal=False, cancel_file=None):
    cancelled = lambda: bool(cancel_file and Path(cancel_file).exists())
    for identity in (run_id, review_request_id):
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', identity):
            raise ValueError('Explicit Run and review request bindings are required')
    data = Path(data_root).resolve(strict=True)
    root = _inside(data / 'runs', data / 'runs' / run_id)
    internal = _inside(root, root/'内部索引')
    receipts = internal / '执行校验结果'
    if receipts.exists():
        _inside(internal, receipts)
    else:
        receipts.mkdir()
    receipt_dir = receipts / review_request_id
    receipt_dir.mkdir(exist_ok=False)
    receipt_path = receipt_dir/'执行记录.json'
    if receipt_path.exists():
        raise ValueError('Execution receipt already exists for this review request')
    receipt = {'run_id': run_id, 'review_request_id': review_request_id, 'formal': formal,
               'status': 'unverified', 'cases': [], 'warnings': [], 'started_at': time.time()}
    try:
        document = _inside(root, root/('正式输出' if formal else '活文档')/'黑盒测试用例.md')
        document_hash = _digest(document)
        receipt.update(document_path=str(document), document_sha256=document_hash)
        plan_path = _inside(internal, internal/'执行校验计划.json')
        if plan_path.stat().st_size > 1024 * 1024:
            raise ValueError('Execution plan exceeds 1 MiB')
        plan = json.loads(plan_path.read_text(encoding='utf-8-sig'))
        if plan.get('run_id') != run_id or plan.get('review_request_id') != review_request_id:
            raise ValueError('Execution plan binding does not match the current review')
        entries = plan.get('cases')
        if not isinstance(entries, list) or not entries:
            raise ValueError('No case execution plan supplied')
        receipt['plan_sha256'] = _digest(plan_path)
        shutil.copyfile(plan_path, receipt_dir/'计划.json')
        shutil.copyfile(document, receipt_dir/'用例原文.md')
        projection_path = _inside(internal, internal/'工作台投影.json')
        projection = json.loads(projection_path.read_text(encoding='utf-8-sig'))
        expected_ids = [row['test_case_id'] for row in projection.get('test_cases', [])]
        seen = set()
        source = _inside(root, root/'inputs/source/repository')
        files, total = [], 0
        for item in source.rglob('*'):
            _inside(source, item)
            if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
                raise ValueError('Source snapshot contains a link; verification remains unverified')
            if item.is_file():
                total += item.stat().st_size
                files.append(item)
                if total > 256 * 1024 * 1024:
                    raise ValueError('Source exceeds the local probe copy limit (256 MiB)')
        receipt['source_sha256'] = {str(item.relative_to(source)): _digest(item) for item in files}
        with tempfile.TemporaryDirectory(prefix='pangea-case-verification-') as temporary:
            staging = Path(temporary)
            copied_source = staging/'source'
            shutil.copytree(source, copied_source)
            for index, entry in enumerate(entries):
                case_id = entry.get('case_id') if isinstance(entry, dict) else None
                item = {'case_id': case_id, 'status': 'unverified'}
                receipt['cases'].append(item)
                if cancelled():
                    item['reason'] = 'Verification cancelled by host'
                    continue
                if not isinstance(case_id, str) or case_id not in expected_ids or case_id in seen:
                    item['reason'] = 'Unknown, missing or duplicate Case ID'
                    continue
                seen.add(case_id)
                if isinstance(entry.get('unverified_reason'), str) and entry['unverified_reason'].strip():
                    item['reason'] = entry['unverified_reason']
                    continue
                try:
                    plan_dir = _inside(internal, internal/'执行校验方案')
                    probe = _probe_path(plan_dir, entry.get('probe'))
                    work = staging / f'case-{index}'
                    work.mkdir()
                    probe_copy = work / 'probe.c'
                    shutil.copyfile(probe, probe_copy)
                    shutil.copyfile(probe_copy, receipt_dir / f'probe-{index}.c')
                    item.update(probe_path=str(probe), probe_sha256=_digest(probe_copy))
                    item.update(_compile_and_run(probe_copy, copied_source, work, cancelled=cancelled))
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    item.update(status='unverified', reason=str(error))
            for case_id in expected_ids:
                if case_id not in seen:
                    receipt['cases'].append({'case_id': case_id, 'status': 'unverified', 'reason': 'Case missing from reviewer execution plan'})
        if _digest(document) != document_hash:
            receipt['warnings'].append('Case document changed during execution; evidence belongs to the archived revision only')
        receipt['status'] = 'recorded'
    except (OSError, ValueError, TypeError, KeyError) as error:
        receipt['warnings'].append(str(error))
    receipt['finished_at'] = time.time()
    _json(receipt_path, receipt)
    return {'status': receipt['status'], 'receipt_path': str(receipt_path),
            'cases': [{key: item[key] for key in ('case_id', 'status', 'exit_code', 'reason') if key in item} for item in receipt['cases']],
            'warnings': receipt['warnings']}
