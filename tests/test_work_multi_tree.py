import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow.work_store import WorkStore
from workflow.inbox_model import InboxError


class MultipleWorktreesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.git('commit', '--allow-empty', '-m', 'initial')
        self.initial = self.git('rev-parse', 'HEAD').stdout.strip()
        self.store = WorkStore(self.root / 'workspace')
        self.item = 'demo/多工作树'
        self.store.start(self.item, summary='先讨论，随后创建开发和验收工作树')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', '-C', str(cwd or self.repo), *args], check=True, text=True, capture_output=True)

    def test_add_first_and_second_then_finish_cleans_both(self):
        first = self.store.add_worktree(self.item, tree_name='开发', repo=self.repo)
        expected_parent = self.store.root / 'dev' / 'worktrees' / self.repo.name
        self.assertEqual(Path(first['resources'][0]['path']).parent, expected_parent)
        self.git('commit', '--allow-empty', '-m', 'main advanced')
        second = self.store.add_worktree(self.item, tree_name='验收', at=self.initial)
        resources = second['resources']
        self.assertEqual(Path(resources[1]['path']).parent, expected_parent)
        self.assertEqual([r['name'] for r in resources], ['开发', '验收'])
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=resources[1]['path']).stdout.strip(), self.initial)
        self.assertEqual(first['workPath'], second['workPath'])
        finished = self.store.finish(self.item, result='验证完成')
        self.assertEqual(finished['closeoutPending'], [])
        for r in resources:
            self.assertFalse(Path(r['path']).exists())
        self.assertEqual(len(self.git('worktree', 'list', '--porcelain').stdout.split('worktree '))-1, 1)

    def test_same_name_reuses_and_explicit_conflicts_do_not_mutate(self):
        first = self.store.add_worktree(self.item, tree_name='开发', repo=self.repo)
        resource = first['resources'][0]
        repeated = self.store.add_worktree(self.item, tree_name='开发')
        self.assertEqual(repeated['resources'][0]['ownership_token'], resource['ownership_token'])
        original = self.store.state_path.read_bytes()
        self.git('commit', '--allow-empty', '-m', 'another')
        for options in ({'path': self.root / 'elsewhere'}, {'branch': 'codex/other'}, {'at': 'HEAD'}, {'base': 'HEAD~1'}):
            with self.assertRaises(Exception):
                self.store.add_worktree(self.item, tree_name='开发', **options)
            self.assertEqual(self.store.state_path.read_bytes(), original)
        self.assertTrue(Path(resource['path']).is_dir())

    def test_start_shortcut_and_legacy_tree_use_same_development_name(self):
        item = self.store.start(self.item, worktree=True, repo=self.repo)
        first = item['resources'][0]
        self.assertEqual(Path(first['path']).parent, self.store.root / 'dev' / 'worktrees' / self.repo.name)
        self.assertEqual(first['name'], '开发')
        # Simulate a record made by the earlier CLI, which did not store a name.
        state = json.loads(self.store.state_path.read_text())
        state['items'][item['id']]['resources'][0].pop('name')
        self.store.state_path.write_text(json.dumps(state))
        same = self.store.add_worktree(self.item, tree_name='开发')['resources'][0]
        self.assertEqual(same['ownership_token'], first['ownership_token'])
        self.store.add_worktree(self.item, tree_name='验收', at=self.initial)
        repeated = self.store.start(self.item, worktree=True)
        self.assertEqual(len(repeated['resources']), 2)
        self.assertEqual(repeated['resources'][0]['ownership_token'], first['ownership_token'])

    def test_existing_old_path_and_explicit_path_are_preserved(self):
        old_path = self.repo.parent / '.worktrees' / self.repo.name / 'existing'
        original = self.store.add_worktree(self.item, tree_name='开发', repo=self.repo, path=old_path)['resources'][0]
        reused = self.store.start(self.item, worktree=True)['resources'][0]
        self.assertEqual(reused['path'], str(old_path))
        self.assertEqual(reused['ownership_token'], original['ownership_token'])
        custom_path = self.root / 'custom-qa'
        item = self.store.add_worktree(self.item, tree_name='验收', path=custom_path)
        self.assertEqual(item['resources'][1]['path'], str(custom_path))
        self.assertFalse((self.store.root / 'dev' / 'worktrees').exists())
        result = self.store.finish(self.item, result='路径兼容验证完成')
        self.assertEqual(result['closeoutPending'], [])
        self.assertFalse(old_path.exists())
        self.assertFalse(custom_path.exists())

    def test_finish_retains_dirty_tree_and_cleans_other_then_can_retry(self):
        self.store.add_worktree(self.item, tree_name='开发', repo=self.repo)
        item = self.store.add_worktree(self.item, tree_name='验收', at=self.initial)
        dev, qa = (Path(r['path']) for r in item['resources'])
        (qa/'untracked.txt').write_text('keep')
        finished = self.store.finish(self.item, result='业务已完成')
        self.assertFalse(dev.exists())
        self.assertTrue((qa/'untracked.txt').is_file())
        self.assertTrue(finished['closeoutPending'])
        (qa/'untracked.txt').unlink()
        self.assertFalse(self.store.finish(self.item)['closeoutPending'])
        self.assertFalse(qa.exists())

    def test_fixed_unmerged_commit_is_retained(self):
        self.git('checkout', '-b', 'unmerged')
        self.git('commit', '--allow-empty', '-m', 'unmerged work')
        tip = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('checkout', 'main')
        item = self.store.add_worktree(self.item, tree_name='验收', repo=self.repo, at=tip)
        finished = self.store.finish(self.item, result='验收完成，提交仍须保留')
        self.assertTrue(finished['closeoutPending'])
        self.assertTrue(Path(item['resources'][0]['path']).is_dir())

    def test_cli_help_and_named_tree_output(self):
        cli = ROOT / 'bin/work'
        result = subprocess.run([sys.executable, str(cli), '--root', str(self.store.root), 'add-worktree', self.item,
                                 '--name', '验收', '--repo', str(self.repo), '--at', self.initial],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('关联worktree（验收）', result.stdout)
        self.assertIn('验收', self.store.resume(self.item)['resources'][0]['name'])

    def test_repo_inference_requires_unique_repository(self):
        with self.assertRaises(InboxError):
            self.store.add_worktree(self.item, tree_name='开发')
        self.assertEqual(self.store.resume(self.item)['resources'], [])
        self.store.add_worktree(self.item, tree_name='开发', repo=self.repo)
        other = self.root / 'other'
        self.git('clone', str(self.repo), str(other))
        self.store.add_worktree(self.item, tree_name='其他仓库', repo=other)
        with self.assertRaises(InboxError):
            self.store.add_worktree(self.item, tree_name='验收')
        self.assertEqual(len(self.store.resume(self.item)['resources']), 2)
