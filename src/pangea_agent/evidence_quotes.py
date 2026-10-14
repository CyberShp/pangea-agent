"""Literal quotation checks against the same frozen source view as source-read."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from pangea_agent.risk_schema import _SOURCE_REFERENCE


def parse_source_ref(ref):
    if not isinstance(ref, str) or not _SOURCE_REFERENCE.fullmatch(ref):
        raise ValueError('ref 必须使用 repo_id:path:line 或 repo_id:path:line-line')
    repo, rest = ref.split(':', 1)
    path, interval = rest.rsplit(':', 1)
    values = interval.split('-')
    return repo, path, int(values[0]), int(values[-1])


def normalize(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text)).strip()


class FrozenSourceResolver:
    def __init__(self, data_root, run_id, task):
        # Deferred import avoids the source_access/result_store import cycle.
        from pangea_agent.inventory.source_access import _run_dir, _source_manifest, _allowed_paths, _repositories
        self.run_dir = _run_dir(data_root, run_id)
        manifest = _source_manifest(self.run_dir, task)
        self.allowed = _allowed_paths(task, manifest)
        self.repositories = _repositories(self.run_dir, manifest)
        self.cache = {}

    def lines(self, repo, path):
        from pangea_agent.inventory.source_access import _file_allowed, _read_frozen_file
        if not _file_allowed(repo, path, self.allowed):
            raise ValueError(f'引文路径不在 task allowed_paths：{repo}:{path}')
        key = (repo, path)
        if key not in self.cache:
            self.cache[key] = _read_frozen_file(self.repositories, repo, path)[1]
        return self.cache[key]


def check_quotes(quotes, resolver, *, required_refs=None):
    if not isinstance(quotes, list) or not quotes:
        return ['evidence_quotes 必须是非空数组']
    errors, refs = [], set()
    for i, quote in enumerate(quotes):
        prefix = f'evidence_quotes[{i}]'
        if not isinstance(quote, dict):
            errors.append(f'{prefix} 必须是含 ref、snippet 的对象')
            continue
        ref, snippet = quote.get('ref'), quote.get('snippet')
        try:
            repo, path, start, end = parse_source_ref(ref)
            refs.add(ref)
            if start > end or end-start+1 > 40:
                raise ValueError('行段必须正序且跨度不超过 40 行')
            lines = resolver.lines(repo, path)
            if not 1 <= start <= end <= len(lines):
                raise ValueError(f'行号越界，文件共 {len(lines)} 行')
            if not isinstance(snippet, str) or not normalize(snippet) or len(normalize(snippet)) > 400:
                raise ValueError('snippet 规范化后须非空且不超过 400 字符')
            actual = normalize('\n'.join(lines[start-1:end]))
            if normalize(snippet) not in actual:
                raise ValueError(f'snippet 不在 {ref} 中；该行段实际内容（规范化，前300字）：{actual[:300]}')
        except (ValueError, OSError) as exc:
            errors.append(f'{prefix} 的 {exc}')
    if required_refs is not None and (not isinstance(required_refs, list)
            or any(not isinstance(ref, str) for ref in required_refs) or set(required_refs) != refs):
        errors.append('source_evidence 与 evidence_quotes 的 ref 集合必须相等')
    return errors
