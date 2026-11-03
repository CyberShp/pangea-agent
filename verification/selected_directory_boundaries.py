"""New directory boundaries and immutable historical freeze compatibility."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.run_module_analysis import run_module_analysis, resume_module_analysis, derive_run
from pangea_agent.cli.public_api import stop_run
from pangea_agent.graph.nodes.open_run import open_run
from pangea_agent.inventory.selected_directories import validate_selected_directories
from pangea_agent.inventory.scope_preview import preview_scope
from pangea_agent.inventory.test_tool_sources import preview_test_tools


class SelectedDirectoryBoundaries(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='selected-directories-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'repositories/repo'
        (self.repo / '中文 空格,目录/nested').mkdir(parents=True)
        (self.repo / '中文 空格,目录/api.c').write_text('int api(void) { return 1; }\n')
        (self.repo / 'tools').mkdir()
        (self.repo / 'tools/probe.py').write_text('raise RuntimeError("NEVER EXECUTE")')
        self.contract = dict(data_root=str(self.root), repository='repo', target='fixture',
                             source_scope=['中文 空格,目录'], workflow_version='source-first-v1',
                             analysis_profile='behavior-test-v2', analysis_settings={'scenario': 'module-analysis', 'mode': 'speed'})

    def create(self, **changes):
        request = self.root / 'request.json'
        write_json(request, {**self.contract, **changes})
        return run_module_analysis(str(request))

    def test_actual_directory_positive_and_all_structural_negatives(self):
        good = ['中文 空格,目录', '中文 空格,目录/nested']
        self.assertEqual(validate_selected_directories(str(self.root), 'repo', good), good)
        self.assertEqual(preview_scope(str(self.root), 'repo', [good[0]])['matched_file_count'], 1)
        for paths in [[], [''], [' '], ['.'], ['./'], ['.//.'], ['中文 空格,目录/..'], ['../repo'],
                      ['missing'], ['中文 空格,目录/api.c'], [str(self.repo / good[0])], ['C:\\repo\\src'],
                      ['C:src'], ['\\\\server\\share'], ['src\0bad'], ['中文 空格,目录', '']]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.create(source_scope=paths)
            with self.subTest(tool_paths=paths), self.assertRaises(ValueError):
                preview_test_tools(str(self.root), [{'repo_id': 'repo', 'paths': paths}])
        self.assertEqual(preview_test_tools(str(self.root), [{'repo_id': 'repo', 'paths': ['tools']}])['matched_file_count'], 1)
        for name, target in [('root-link', self.repo), ('outside-link', self.root)]:
            (self.repo / name).symlink_to(target, target_is_directory=True)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.create(source_scope=[name])
        with self.assertRaises(ValueError):
            self.create(test_tool_sources=[{'repo_id': 'repo', 'paths': ['.']}])
        self.assertTrue(self.create()['agent_actions'])

    def test_frozen_root_and_file_runs_resume_without_revalidation_or_rewrite(self):
        for scope in [['.'], ['中文 空格,目录/api.c']]:
            # Build historical fixture using the former creation contract, never user data.
            with patch('pangea_agent.inventory.selected_directories.validate_new_selections'):
                created = self.create(source_scope=scope)
            run = self.root / 'runs' / created['run_id']
            frozen = run / 'inputs/task-contract.json'
            before = frozen.read_bytes()
            with patch('pangea_agent.inventory.selected_directories.validate_selected_directories', side_effect=AssertionError('must not inspect live selection')):
                self.assertEqual(resume_module_analysis(created['run_id'], str(self.root))['run_id'], created['run_id'])
            self.assertEqual(frozen.read_bytes(), before)
            stop_run(str(self.root), created['run_id'])
            request = self.root / 'derive.json'
            write_json(request, {'mode': 'supplement', 'instruction': 'Check original snapshot'})
            child = derive_run(str(self.root), created['run_id'], str(request))
            self.assertTrue(child['agent_actions'])
            write_json(request, {'mode': 'changed-files', 'changed_paths': ['中文 空格,目录/api.c']})
            with self.assertRaises(ValueError):
                derive_run(str(self.root), created['run_id'], str(request))
            with self.assertRaises(ValueError):
                self.create(source_scope=['tools'], incremental_request={'mode': 'supplement', 'parent_run_id': created['run_id'], 'instruction': 'fake'})
            self.assertEqual(frozen.read_bytes(), before)

    def test_historical_whole_tool_repository_remains_frozen(self):
        tools = self.root / 'repositories/legacy-tools'
        tools.mkdir()
        (tools / 'probe.py').write_text('ORIGINAL TOOL SNAPSHOT')
        with patch('pangea_agent.inventory.selected_directories.validate_new_selections'):
            created = self.create(test_tool_sources=[{'repo_id': 'legacy-tools', 'paths': ['.']}])
        run = self.root / 'runs' / created['run_id']
        frozen = run / 'inputs/task-contract.json'
        original = frozen.read_bytes()
        (tools / 'probe.py').unlink()
        tools.rmdir()
        with patch('pangea_agent.inventory.test_tool_sources.preview_test_tools', side_effect=AssertionError('must not refresh tools')):
            resume_module_analysis(created['run_id'], str(self.root))
            stop_run(str(self.root), created['run_id'])
            request = self.root / 'derive.json'
            write_json(request, {'mode': 'supplement', 'instruction': 'Read original tool snapshot'})
            child = derive_run(str(self.root), created['run_id'], str(request))
        self.assertEqual((self.root / 'runs' / child['run_id'] / 'inputs/source/legacy-tools/probe.py').read_text(), 'ORIGINAL TOOL SNAPSHOT')
        self.assertEqual(frozen.read_bytes(), original)

    def test_preview_and_freeze_agree_for_valid_directory_aliases(self):
        (self.repo / 'alias').symlink_to(self.repo / '中文 空格,目录', target_is_directory=True)
        for scope in ['./中文 空格,目录', '中文 空格,目录/.', '中文 空格,目录//', 'alias']:
            with self.subTest(scope=scope):
                preview = preview_scope(str(self.root), 'repo', [scope])
                result = self.create(source_scope=[scope], expected_scope_revision=preview['scope_revision'])
                manifest = read_json(self.root / 'runs' / result['run_id'] / 'inputs/source-manifest.json')
                self.assertEqual(manifest['source_scope'], [item['relative_path'] for item in preview['files']])

    def test_new_request_at_frozen_contract_path_cannot_impersonate_old_run(self):
        from pangea_agent.models.contract import TaskContract
        contract = TaskContract.model_validate({**self.contract, 'run_id': 'forged-old', 'source_scope': ['.']}).model_dump(mode='json', exclude_none=True)
        frozen = self.root / 'runs/forged-old/inputs/task-contract.json'
        write_json(frozen, contract)
        before = frozen.read_bytes()
        with self.assertRaisesRegex(ValueError, '非根下级目录'):
            run_module_analysis(str(frozen))
        self.assertEqual(frozen.read_bytes(), before)
        self.assertFalse((frozen.parents[1] / 'progress.json').exists())
        for progress in [{}, {'run_id': 'another-run'}]:
            write_json(frozen.parents[1] / 'progress.json', progress)
            with self.subTest(progress=progress), self.assertRaises(ValueError):
                run_module_analysis(str(frozen))
            self.assertEqual(frozen.read_bytes(), before)


    def test_supplement_checks_parent_contract_and_progress_identity(self):
        created = self.create()
        stop_run(str(self.root), created['run_id'])
        run = self.root / 'runs' / created['run_id']
        request = self.root / 'derive.json'
        write_json(request, {'mode': 'supplement', 'instruction': 'Bound parent only'})
        for relative in ['inputs/task-contract.json', 'progress.json']:
            path = run / relative
            original = path.read_bytes()
            value = read_json(path)
            write_json(path, {**value, 'run_id': 'foreign'})
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, '身份不一致'):
                derive_run(str(self.root), created['run_id'], str(request))
            path.write_bytes(original)

    def test_context_scope_is_explicit_file_reference_not_directory_expansion(self):
        from pangea_agent.graph.nodes.source_first import _explicit_context_files
        other = self.root / 'repositories/other'
        (other / 'tools').mkdir(parents=True)
        (other / 'tools/probe.py').write_text('other reference')
        repos = [{'repo_id': repo_id, 'source_root': str(path)} for repo_id, path in [('repo', self.repo), ('other', other)]]
        expansion = {'groups': []}
        for scope in ['.', 'repo:.', 'tools', 'repo:tools', 'repo:tools/..', 'repo:../other/tools/probe.py']:
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                _explicit_context_files(repos, [scope], expansion)
        with self.assertRaisesRegex(ValueError, '路径不唯一'):
            _explicit_context_files(repos, ['tools/probe.py'], expansion)
        records = _explicit_context_files(repos, ['repo:tools/probe.py'], expansion)
        self.assertEqual(records, [{'repo_id': 'repo', 'path': 'tools/probe.py', 'reason': 'explicit_context'}])
        for scope in ['.', 'repo:.', 'tools', 'repo:tools']:
            with self.subTest(new_run_context=scope), self.assertRaisesRegex(ValueError, 'context_scope 文件不存在'):
                self.create(context_scope=[scope])
        self.assertTrue(self.create(context_scope=['repo:tools/probe.py'])['agent_actions'])

    def test_supplement_context_must_match_parent_and_stays_frozen(self):
        created = self.create(context_scope=['repo:tools/probe.py'])
        run = self.root / 'runs' / created['run_id']
        frozen = run / 'inputs/task-contract.json'
        original = frozen.read_bytes()
        stop_run(str(self.root), created['run_id'])
        (self.repo / 'tools/probe.py').unlink()
        request = self.root / 'derive-context.json'
        write_json(request, {'mode': 'supplement', 'instruction': 'Keep original reference'})
        child = derive_run(str(self.root), created['run_id'], str(request))
        reference = self.root / 'runs' / child['run_id'] / 'inputs/source/repo/tools/probe.py'
        self.assertIn('NEVER EXECUTE', reference.read_text())
        for context in [[], ['.'], ['repo:中文 空格,目录/api.c']]:
            with self.subTest(context=context), self.assertRaisesRegex(ValueError, '父 Run 冻结范围：context_scope'):
                self.create(context_scope=context, incremental_request={'mode': 'supplement', 'parent_run_id': created['run_id'], 'instruction': 'Must retain original reference'})
        self.assertEqual(frozen.read_bytes(), original)

    def test_fake_parent_and_frozen_contract_mismatch_do_not_bypass(self):
        with self.assertRaises(ValueError):
            self.create(source_scope=['.'], incremental_request={'mode': 'supplement', 'parent_run_id': 'missing', 'instruction': 'fake'})
        state = dict(run_id='pending-old', data_root=str(self.root), task_contract={**self.contract, 'run_id': 'pending-old'})
        opened = open_run(state)
        frozen = self.root / 'runs/pending-old/inputs/task-contract.json'
        original = frozen.read_bytes()
        with self.assertRaisesRegex(ValueError, '冻结输入不一致'):
            open_run({**state, 'task_contract': {**state['task_contract'], 'source_scope': ['.']}})
        self.assertEqual(frozen.read_bytes(), original)
        self.assertTrue(open_run(state)['needs_prepare'])


if __name__ == '__main__':
    unittest.main()
