import sys
import tempfile
import unittest
import json
import subprocess
from unittest.mock import patch
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow.work_store import WorkStore
from workflow.inbox_model import InboxError, StaleSourceError


class WorkStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = WorkStore(self.root)

    def test_capture_start_and_restore_only_this_work(self):
        self.store.add('示例项目/读取团队文档', summary='接收链接并按用户权限读取正文')
        self.store.add('示例项目/其他事项', summary='不能混入上下文')
        started = self.store.start('示例项目/读取团队文档')
        self.assertTrue((self.root / started['workPath']).is_file())
        restored = WorkStore(self.root).resume('读取团队文档')
        self.assertEqual(restored['name'], '示例项目/读取团队文档')
        self.assertIn('接收链接并按用户权限读取正文', restored['document'])
        self.assertNotIn('不能混入上下文', restored['document'])
        self.assertEqual(self.store.start(restored['name'])['workPath'], restored['workPath'])
        self.assertEqual(restored['lifecycleStatus'], 'active')

    def test_finish_preserves_result_repairs_links_and_can_reopen(self):
        self.store.add('demo/One', summary='A meaningful goal')
        one = self.store.start('demo/One')
        self.store.add('demo/Two', summary='Depends on One')
        two = self.store.start('demo/Two')
        second = self.root / two['workPath']
        second.write_text(second.read_text() + '\n[One](One.md#result)\n')
        original = self.root / one['workPath']
        original.write_text(original.read_text() + '\n[Other](Two.md)\n')
        finished = self.store.finish('demo/One', result='Delivered, checks passed')
        archived = self.root / finished['workPath']
        self.assertFalse(original.exists())
        self.assertIn('/work/archive/', finished['workPath'])
        self.assertIn('../archive/One.md#result', second.read_text())
        self.assertIn('../active/Two.md', archived.read_text())
        self.assertIn('Delivered, checks passed', archived.read_text())
        self.assertEqual(self.store.finish('demo/One')['closeoutPending'], [])
        reopened = self.store.transition('demo/One', 'reopen')
        self.assertEqual(reopened['id'], one['id'])
        self.assertTrue(original.exists())
        self.assertIn('[One](One.md#result)', second.read_text())

    def test_edit_save_detects_other_writer_and_preserves_draft(self):
        self.store.add('demo/One', summary='Goal')
        self.store.start('demo/One')
        a, b = self.root / 'a.md', self.root / 'b.md'
        self.store.edit('demo/One', a)
        self.store.edit('demo/One', b)
        a.write_text(a.read_text().replace('## 事项说明\n\nGoal', '## 事项说明\n\nAlice outcome'))
        b.write_text(b.read_text().replace('## 事项说明\n\nGoal', '## 事项说明\n\nBob outcome'))
        self.store.save(a)
        with self.assertRaises(StaleSourceError):
            self.store.save(b)
        self.assertIn('Bob outcome', b.read_text())
        self.assertIn('Alice outcome', self.store.resume('demo/One')['document'])
        self.store.save(a)  # repeated save of identical content is harmless

    def test_names_aliases_and_personal_attention_do_not_change_lifecycle(self):
        self.store.add('a/Same'); self.store.add('b/Same')
        with self.assertRaises(InboxError): self.store.resume('Same')
        one = self.store.start('a/Same')
        renamed = self.store.rename('a/Same', 'a/New')
        self.assertEqual(renamed['id'], one['id'])
        self.assertEqual(self.store.resume('a/Same')['name'], 'a/New')
        with self.assertRaises(InboxError): self.store.add('a/Same')
        self.store.arrange('a/New', status='Done')
        self.assertEqual(self.store.resume('a/New')['lifecycleStatus'], 'active')
        self.store.arrange('a/New', hide=True)
        self.assertNotIn('a/New', [r['name'] for r in self.store.list()])
        self.assertEqual(self.store.resume('a/New')['lifecycleStatus'], 'active')

    def test_shared_document_is_not_archived_under_active_consumer(self):
        self.store.add('demo/One'); self.store.add('demo/Two')
        one = self.store.start('demo/One')
        self.store.start('demo/Two', work=one['workPath'])
        result = self.store.finish('demo/One', result='One done')
        self.assertIn('/active/', result['workPath'])
        self.assertTrue(result['closeoutPending'])
        self.assertTrue((self.root / one['workPath']).exists())

    def test_recover_completes_interrupted_file_write_and_protects_external_edits(self):
        self.store.add('demo/One')
        import os
        real_replace = os.replace
        def fail_state(src, dst):
            if Path(dst).name == 'state.json':
                raise OSError('simulated disk failure')
            return real_replace(src, dst)
        with patch('os.replace', side_effect=fail_state):
            with self.assertRaises(OSError): self.store.start('demo/One')
        with self.assertRaises(InboxError): self.store.resume('demo/One')
        self.store.recover()
        self.assertEqual(self.store.resume('demo/One')['lifecycleStatus'], 'active')
        with patch('os.replace', side_effect=fail_state):
            with self.assertRaises(OSError): self.store.rename('demo/One', 'demo/New')
        inbox = self.root / 'personal/inbox/inbox.md'
        external = inbox.read_text().replace('demo/New', 'demo/External')
        inbox.write_text(external)
        with self.assertRaises(InboxError): self.store.recover()
        self.assertEqual(inbox.read_text(), external)

    def test_real_git_start_finish_and_dirty_retry(self):
        repo = self.root / 'repo'; repo.mkdir()
        def git(*args):
            return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout
        git('init', '-b', 'main'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
        (repo / 'file').write_text('initial'); git('add', '.'); git('commit', '-m', 'initial')
        self.store.add('demo/Tree')
        item = self.store.start('demo/Tree', worktree=True, repo=repo)
        tree = Path(item['resources'][0]['path'])
        (tree / 'unfinished').write_text('keep me')
        result = self.store.finish('demo/Tree', result='Documented outcome')
        self.assertTrue(tree.exists())
        self.assertTrue(result['closeoutPending'])
        (tree / 'unfinished').unlink()
        result = self.store.finish('demo/Tree')
        self.assertFalse(tree.exists())
        self.assertEqual(result['closeoutPending'], [])
        self.store.finish('demo/Tree')

    def test_completed_consumer_keeps_branch_until_it_releases_use(self):
        repo = self.root / 'repo'; repo.mkdir()
        for args in [('init', '-b', 'main'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.invalid'), ('commit', '--allow-empty', '-m', 'initial')]:
            subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        self.store.add('demo/Owner'); self.store.add('demo/Consumer')
        owner = self.store.start('demo/Owner', worktree=True, repo=repo)
        resource = owner['resources'][0]
        self.store.link('demo/Consumer', 'branch', repo=repo, branch=resource['branch'])
        self.store.finish('demo/Consumer', result='Consumer delivered')
        finished = self.store.finish('demo/Owner', result='Owner delivered')
        self.assertTrue(Path(resource['path']).exists())
        self.assertTrue(finished['closeoutPending'])
        self.store.release('demo/Consumer', resource['branch'])
        self.store.finish('demo/Owner')
        self.assertFalse(Path(resource['path']).exists())

    def test_shared_document_retains_each_independent_result(self):
        self.store.add('demo/One'); self.store.add('demo/Two')
        one = self.store.start('demo/One')
        self.store.start('demo/Two', work=one['workPath'])
        self.store.finish('demo/One', result='Outcome One')
        two = self.store.finish('demo/Two', result='Outcome Two')
        text = (self.root / two['workPath']).read_text()
        self.assertIn('Outcome One', text)
        self.assertIn('Outcome Two', text)

    def test_dry_run_cli_is_readable_and_keeps_worktree(self):
        repo = self.root / 'repo'; repo.mkdir()
        for args in [('init', '-b', 'main'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.invalid'), ('commit', '--allow-empty', '-m', 'initial')]:
            subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        self.store.add('demo/Tree')
        item = self.store.start('demo/Tree', worktree=True, repo=repo)
        cli = ROOT / 'bin/work'
        result = subprocess.run([sys.executable, str(cli), '--root', str(self.root), 'finish', 'demo/Tree', '--result', 'Done', '--dry-run'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('未执行收尾', result.stdout)
        self.assertTrue(Path(item['resources'][0]['path']).exists())
        self.assertEqual(self.store.resume('demo/Tree')['lifecycleStatus'], 'active')

    def test_editor_change_during_write_preparation_is_preserved(self):
        self.store.add('demo/One', summary='original')
        revision = self.store.resume('demo/One')['revision']
        original_write = self.store._write
        inbox = self.root / 'personal/inbox/inbox.md'
        def intervening_editor(path, value, **kwargs):
            if path == inbox:
                inbox.write_text(inbox.read_text().replace('original', 'EXTERNAL'))
            return original_write(path, value, **kwargs)
        with patch.object(self.store, '_write', side_effect=intervening_editor):
            with self.assertRaises(InboxError):
                self.store.update('demo/One', summary='ours', expected_revision=revision)
        self.assertIn('EXTERNAL', inbox.read_text())
        self.assertNotIn(' — ours', inbox.read_text())

    def test_cli_plain_output_hides_internal_metadata(self):
        self.store.add('demo/One'); self.store.start('demo/One')
        cli = ROOT / 'bin/work'
        result = subprocess.run([sys.executable, str(cli), '--root', str(self.root), 'resume', 'demo/One'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('demo/One', result.stdout)
        self.assertNotIn('sha256:', result.stdout)
        self.assertNotIn('ownership_token', result.stdout)

if __name__ == '__main__':
    unittest.main()
