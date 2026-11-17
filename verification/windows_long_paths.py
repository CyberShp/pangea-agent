"""Source snapshot I/O beyond MAX_PATH, also run by the Windows package build.

Uses production preparation, worker reads, structural indexing and inheritance.
No model, registry changes, or access to user repositories is required.
"""
from pathlib import Path
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pangea_agent.fs_paths import (
    _windows_path, long_path, ordinary_path, resolve_path, glob_paths, walk_paths,
)
from pangea_agent.agent_io import read_json, write_json
from pangea_agent.graph.nodes.prepare_inputs import _freeze_sources, _copy_source
from pangea_agent.inventory.on_demand import file_inventory, ensure_file_index, detail_path
from pangea_agent.inventory.source_access import _read_frozen_file, SourceAccessError
from pangea_agent.inventory.languages import source_files
from pangea_agent.inventory.multilanguage import build_inventory
from pangea_agent.inventory.repository_source import materialize
from pangea_agent.inventory.investigation_policy import _inherit_supplemental


class WindowsPathFormatting(unittest.TestCase):
    def test_drive_unc_and_already_extended(self):
        self.assertEqual(_windows_path(r'C:\dir\..\目录\name.c'), '\\\\?\\C:\\目录\\name.c')
        self.assertEqual(_windows_path(r'\\server\share\目录\name.c'), '\\\\?\\UNC\\server\\share\\目录\\name.c')
        for value in [r'\\?\C:\dir\name.c', r'\\?\UNC\server\share\name.c']:
            self.assertEqual(_windows_path(value), value)

    def test_roundtrip_and_relative_path(self):
        path = Path('relative') / 'name.c'
        expected = path.absolute() if os.name == 'nt' else path
        self.assertEqual(ordinary_path(long_path(path)), expected)
        self.assertEqual(long_path(long_path(path)), long_path(path))


class SourceLongPaths(unittest.TestCase):
    def setUp(self):
        self.root = resolve_path(tempfile.mkdtemp(prefix='pangea-long-'))
        self.addCleanup(lambda: shutil.rmtree(long_path(self.root)))
        self.repo = self.root / 'repositories' / 'repo'
        self.state = {'data_root': str(self.root), 'run_id': 'long-path-run'}
        self.run = self.root / 'runs' / self.state['run_id']
        self.relative_dir = '/'.join(['src', 'network_' + 'a' * 62, 'transport_' + 'b' * 60, '目录 with space_' + 'c' * 60])
        self.contents = {
            'main.cpp': 'int marker(void) { return 7; }\n',
            'main.lua': 'function marker() return 7 end\n',
            'main.py': 'def marker():\n    return 7\n',
            'main.sh': '#!/bin/sh\necho marker\n',
        }
        self.paths = [self.relative_dir + '/' + name for name in self.contents]
        for name, content in self.contents.items():
            target = self.repo / self.relative_dir / name
            long_path(target.parent).mkdir(parents=True, exist_ok=True)
            long_path(target).write_text(content, encoding='utf-8')
        self.repositories = [{'repo_id': 'repo', 'source_root': str(self.repo)}]
        self.expansion = {'groups': [{'repo_id': 'repo', 'code_paths': self.paths, 'context_paths': []}]}

    def freeze(self):
        return _freeze_sources(self.state, self.repositories, self.expansion)

    def test_freeze_retry_inventory_index_and_worker_read(self):
        # A previous interrupted staging tree and previous final tree must both be removable.
        leftover = self.run / 'inputs/.source-staging/repo' / self.paths[0]
        long_path(leftover.parent).mkdir(parents=True)
        long_path(leftover).write_text('interrupted', encoding='utf-8')
        frozen = self.freeze()
        frozen = self.freeze()
        root = Path(frozen[0]['source_root'])
        self.assertGreater(len(str(root / self.paths[0])), 260)
        self.assertNotIn('\\\\?\\', str(frozen))
        self.assertFalse(long_path(self.run / 'inputs/.source-staging').exists())
        self.assertEqual(file_inventory(frozen, self.expansion)['file_count'], 4)
        self.assertEqual(len(list(source_files(frozen, ['src']))), 4)
        self.assertEqual(build_inventory(frozen, ['src'])['file_count'], 4)
        for relative in self.paths:
            resolved, lines = _read_frozen_file({'repo': (root, {})}, 'repo', relative)
            self.assertEqual(resolved, root / relative)
            self.assertEqual(lines, self.contents[Path(relative).name].splitlines())
            ensure_file_index(self.run, 'repo', relative, root)
            index = read_json(detail_path(self.run, 'repo', relative))
            self.assertEqual(index['path'], relative)
        self.assertEqual(len([p for p in glob_paths(root, '*', recursive=True) if long_path(p).is_file()]), 4)
        self.assertEqual(sum(len(files) for _, _, files in walk_paths(root)), 4)

    def test_short_source_becomes_long_only_in_snapshot(self):
        # Match the reported failure: the original opens, but staging exceeds MAX_PATH.
        relative = 'edge'
        while len(str(self.repo / relative / 'edge.cpp')) < 245:
            remaining = 245 - len(str(self.repo / relative / 'edge.cpp'))
            relative += '/' + 'x' * min(60, max(1, remaining - 1))
        relative += '/edge.cpp'
        source = self.repo / relative
        self.assertLess(len(str(source)), 260)
        long_path(source.parent).mkdir(parents=True, exist_ok=True)
        long_path(source).write_bytes(b'int edge = 1;\n')
        expansion = {'groups': [{'repo_id': 'repo', 'code_paths': [relative], 'context_paths': []}]}
        frozen = _freeze_sources(self.state, self.repositories, expansion)
        target = Path(frozen[0]['source_root']) / relative
        self.assertGreater(len(str(target)), 260)
        self.assertEqual(long_path(target).read_bytes(), long_path(source).read_bytes())
        from pangea_agent.report.asset_comparison import _fingerprint_sources
        self.assertEqual(len(_fingerprint_sources(self.run, {'repositories': frozen, 'scope_expansion': expansion})), 1)

    def test_supplemental_atomic_copy_and_inheritance(self):
        frozen = self.freeze()
        relative = self.relative_dir + '/extra.cpp'
        long_path(self.repo / relative).write_text('int extra(void) { return 9; }\n', encoding='utf-8')
        write_json(self.run / 'inputs/source-manifest.json', {'scope_expansion': self.expansion, 'repositories': frozen})
        write_json(self.run / 'inputs/source-investigation.json', {'policy': 'repository-local-v1', 'repositories': self.repositories})
        saved = materialize(self.run, {'source_investigation_policy': 'repository-local-v1'}, 'repo', relative, 'action')
        self.assertEqual(long_path(saved).read_bytes(), long_path(self.repo / relative).read_bytes())
        child = self.root / 'runs/child'
        _inherit_supplemental(self.run, child, {'repo'})
        self.assertEqual(long_path(child / 'inputs/source/repo' / relative).read_bytes(), long_path(saved).read_bytes())

    def test_real_run_preparation_and_bound_worker_access(self):
        from pangea_agent.cli.run_module_analysis import run_module_analysis
        from pangea_agent.cli.adapter_api import bind_action, settle_action
        from pangea_agent.cli.source_first_api import plan_write, work_finish, source_read
        from pangea_agent.inventory.scope_preview import preview_scope
        from pangea_agent.evidence_quotes import FrozenSourceResolver, check_quotes
        preview = preview_scope(str(self.root), 'repo', ['src'])
        self.assertEqual(preview['matched_file_count'], 4)
        request = self.root / 'request.json'
        write_json(request, {'data_root': str(self.root), 'repository': 'repo', 'target': 'Long source access',
                            'source_scope': ['src'], 'analysis_profile': 'behavior-test-v2',
                            'analysis_settings': {'scenario': 'module-analysis', 'mode': 'speed'}})
        created = run_module_analysis(str(request))
        run = self.root / 'runs' / created['run_id']
        task = read_json(Path(created['agent_actions'][0]['task_path']))
        binding = (str(self.root), created['run_id'], task['action_id'], 'planner')
        bind_action(*binding)
        planned = plan_write(*binding, expected_revision=0, unit={'title': 'Source access', 'purpose': 'Read frozen source', 'owned_files': task['owned_scope_paths']})
        work_finish(*binding, revision=planned['revision'])
        settle_action(*binding[:3])
        action = next(a for a in read_json(run / 'progress.json')['actions'].values() if a['role'] == 'analysis')
        worker = read_json(Path(action['task_path']))
        binding = (str(self.root), created['run_id'], action['action_id'], 'worker')
        bind_action(*binding)
        relative = self.paths[0]
        long_path(self.repo / relative).write_text('LIVE_CHANGED', encoding='utf-8')
        response = source_read(*binding, repo_id='repo', path=relative)
        self.assertIn('int marker', str(response))
        self.assertNotIn('LIVE_CHANGED', str(response))
        resolver = FrozenSourceResolver(str(self.root), created['run_id'], worker)
        self.assertEqual(check_quotes([{'ref': f'repo:{relative}:1', 'snippet': 'int marker'}], resolver), [])

    def test_escape_rejected_and_failed_copy_names_both_paths(self):
        frozen = self.freeze()
        root = Path(frozen[0]['source_root'])
        with self.assertRaises(SourceAccessError):
            _read_frozen_file({'repo': (root, {})}, 'repo', '../outside.c')
        source, target = self.repo / self.paths[0], root / self.paths[0]
        with patch('pangea_agent.graph.nodes.prepare_inputs.shutil.copy2', side_effect=FileNotFoundError(2, 'missing')):
            with self.assertRaises(RuntimeError) as caught:
                _copy_source(source, target)
        self.assertIn(str(source), str(caught.exception))
        self.assertIn(str(target), str(caught.exception))
        self.assertIn('errno=2', str(caught.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
