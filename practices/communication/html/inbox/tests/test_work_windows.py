"""Exercise actual Windows entry points, not mocked platform flags."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from workflow.work_interface import command, install
from workflow.work_store import WorkStore
from workflow.work_resources import plan_worktree, ensure_worktree, cleanup_resource, _locked, ResourceError


@unittest.skipUnless(os.name == 'nt', 'Windows integration')
class WindowsJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "中文 space's"
        self.root.mkdir()
        self.store = WorkStore(self.root)

    def powershell(self, script):
        import base64
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        return subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                              capture_output=True, encoding='utf-8', cwd=self.temp.name)

    def test_commands_and_installed_entry_complete_chinese_work(self):
        name = "测试/含空格 O'Brien"
        for parts in [('start', name, '--summary', '中文目标'),
                      ('record', name, '--text', '保留证据'), ('resume', name),
                      ('finish', name, '--result', '已验证')]:
            result = self.powershell(command(self.store, '--json', *parts))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['name'], name)
        item = self.store.resume(name)
        self.assertEqual(item['lifecycleStatus'], 'completed')
        self.assertIn('/archive/', item['workPath'])
        bindir = self.root / 'my bin'
        installed = install(self.store, bindir)
        self.assertEqual(install(self.store, bindir), installed)
        target = Path(installed['installedPath'])
        result = self.powershell("& '" + str(target).replace("'", "''") + "' --json --root '" + str(self.root).replace("'", "''") + "' list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]['name'], name)
        target.write_text('existing program', encoding='utf-8')
        with self.assertRaises(Exception):
            install(self.store, bindir)
        self.assertEqual(target.read_text(), 'existing program')

    def test_resource_lock_and_chinese_path_cleanup(self):
        repo = self.root / '仓库'; repo.mkdir()
        for args in [('init', '-b', 'main'), ('config', 'user.name', 'Test'),
                     ('config', 'user.email', 'test@example.invalid'), ('commit', '--allow-empty', '-m', 'initial')]:
            subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        resource = plan_worktree(repo, self.root / '开发树', 'codex/windows')
        with _locked(resource):
            with self.assertRaises(ResourceError) as caught:
                ensure_worktree(resource)
            self.assertEqual(caught.exception.code, 'busy')
        resource = ensure_worktree(resource)
        self.assertEqual(cleanup_resource(resource)['status'], 'cleaned')
        self.assertFalse(Path(resource['path']).exists())

    def test_edit_crlf_document_preserves_original_history(self):
        name = '测试/编辑'
        item = self.store.start(name, summary='Original goal')
        self.store.record_event(name, 'Original history')
        path = self.root / item['workPath']
        original = path.read_text(encoding='utf-8').replace('\n', '\r\n')
        path.write_bytes(original.encode('utf-8'))
        history = original.split('## 历史记录', 1)[1]
        draft = Path(self.store.edit(name)['draftPath'])
        self.addCleanup(lambda: (draft.unlink(missing_ok=True), draft.parent.rmdir()))
        draft.write_text(draft.read_text(encoding='utf-8').replace('Original goal', 'New goal'), encoding='utf-8')
        self.store.save(draft)
        saved = path.read_bytes().decode('utf-8')
        self.assertIn('New goal', saved)
        self.assertIn('## 历史记录' + history, saved)

    def test_installed_chinese_launcher_restores_cmd_code_page(self):
        launcher = self.root / '中文入口.py'
        launcher.write_text("print('launcher-ok')\n", encoding='utf-8')
        self.store.launcher = launcher
        target = install(self.store, self.root / 'bin')['installedPath']
        result = subprocess.run(f'cmd /d /c chcp 936 >nul & call "{target}" & chcp',
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'launcher-ok', result.stdout, result.stderr.decode('utf-8', errors='replace'))
        self.assertIn(b'936', result.stdout)
