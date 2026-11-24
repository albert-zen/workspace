"""Tests for `work intent` append-only recording, reading, and resume integration."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workflow.work_store import WorkStore
from workflow.inbox_model import InboxError


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


class WorkIntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = WorkStore(self.root)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(WORKSPACE_ROOT / 'bin/work'), '--root', str(self.root), *args],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False,
        )

    def test_work_and_project_intent_are_append_only_and_surfaced_in_resume(self) -> None:
        self.store.start('demo/Rules', summary='Refactor rules')

        first = self.run_cli(
            'intent', 'add', 'demo/Rules',
            '--text', '写法偏向人类，人类可读、好维护、AI 也好理解。',
            '--source', 'thread-1',
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('已建立并记录原始意图', first.stdout)

        work_intent = self.root / 'agent-data/projects/demo/work/active/Rules/intent.md'
        self.assertTrue(work_intent.is_file())
        snapshot_after_first = work_intent.read_text(encoding='utf-8')
        self.assertIn('> 写法偏向人类，人类可读、好维护、AI 也好理解。', snapshot_after_first)
        self.assertIn('来源：thread-1', snapshot_after_first)

        second = self.run_cli(
            'intent', 'add', 'demo/Rules',
            '--text', '按第二个方案走',
            '--context', '采纳方案 B：保留独立 review.md',
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('已追加原始意图', second.stdout)
        snapshot_after_second = work_intent.read_text(encoding='utf-8')
        # Existing bytes are preserved intact (append-only).
        self.assertTrue(snapshot_after_second.startswith(snapshot_after_first))
        self.assertIn('> 按第二个方案走', snapshot_after_second)
        self.assertIn('所指方案／上下文补充：采纳方案 B：保留独立 review.md', snapshot_after_second)

        proj = self.run_cli(
            'intent', 'add', '--project', 'demo',
            '--text', '长期目标：用 Chief of Staff 包圆全局工作。',
        )
        self.assertEqual(proj.returncode, 0, proj.stderr)
        proj_intent = self.root / 'agent-data/projects/demo/intent.md'
        self.assertTrue(proj_intent.is_file())

        shown = self.run_cli('intent', 'show', 'demo/Rules')
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn('[事项原始意图]', shown.stdout)
        self.assertIn('[项目原始意图]', shown.stdout)
        self.assertIn('写法偏向人类', shown.stdout)
        self.assertIn('包圆全局工作', shown.stdout)

        shown_json = self.run_cli('--json', 'intent', 'show', 'demo/Rules')
        self.assertEqual(shown_json.returncode, 0, shown_json.stderr)
        payload = json.loads(shown_json.stdout)
        self.assertEqual([item['scope'] for item in payload['intents']], ['work', 'project'])

        resumed = self.run_cli('resume', 'demo/Rules')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn('事项原始意图：', resumed.stdout)
        self.assertIn('项目原始意图：', resumed.stdout)
        self.assertIn('intent show demo/Rules', resumed.stdout)

    def test_intent_validation_rejects_ambiguous_or_unsafe_targets(self) -> None:
        self.store.add('demo/Unstarted', summary='Only added, not started')

        with self.assertRaises(InboxError):
            self.store.intent_add(text='missing target')
        with self.assertRaises(InboxError):
            self.store.intent_add(name='demo/Unstarted', project='demo', text='both targets')
        with self.assertRaises(InboxError):
            self.store.intent_add(project='demo', text='   ')
        with self.assertRaises(InboxError):
            self.store.intent_add(project='../escape', text='unsafe path')
        with self.assertRaises(InboxError) as ctx:
            self.store.intent_add(name='demo/Unstarted', text='needs work first')
        self.assertEqual(ctx.exception.code, 'work_not_started')

    def test_project_intent_show_reports_empty_cleanly(self) -> None:
        result = self.run_cli('intent', 'show', '--project', 'fresh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('暂无原始意图记录', result.stdout)
        self.assertIn('--project fresh', result.stdout)


if __name__ == '__main__':
    unittest.main()
