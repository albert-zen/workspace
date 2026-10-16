import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow.work_store import WorkStore
from workflow.work_interface import context_text
from workflow.inbox_model import InboxError, StaleSourceError


class SemanticFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkStore(self.root)
        self.name = 'demo/Work'
        self.store.start(self.name, summary='Build the agreed change')

    def test_pause_cancel_reopen_sync_inbox_without_platform_control(self):
        self.store.link(self.name, 'thread', url='codex://threads/example')
        paused = self.store.transition(self.name, 'pause', reason='稍后做')
        self.assertEqual(paused['attention'], 'Waiting')
        blocked = self.store.transition(self.name, 'pause', reason='等用户确定范围', needs_user=True)
        self.assertEqual(blocked['attention'], 'Needs you')
        cancelled = self.store.transition(self.name, 'cancel', reason='不再需要')
        self.assertFalse(cancelled['inbox'])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(cancelled['resources'][0]['url'], 'codex://threads/example')
        self.assertFalse(cancelled['resources'][0].get('released', False))
        reopened = self.store.transition(self.name, 'reopen')
        self.assertEqual(reopened['attention'], 'In progress')

    def test_completion_updates_current_and_preserves_both_rounds(self):
        v = self.store.resume(self.name)
        self.store.update(self.name, progress='Next: finish the task', expected_revision=v['revision'])
        with patch('workflow.work_store.now', return_value='2026-09-16T01:00:00+00:00'):
            first = self.store.finish(self.name, result='First delivered result')
        body, _ = context_text(first['document'])
        self.assertNotIn('Next: finish', body)
        self.assertIn('First delivered result', body)
        before = self.store.history(self.name)
        self.store.finish(self.name)
        self.assertEqual(before, self.store.history(self.name))
        self.store.transition(self.name, 'reopen')
        with patch('workflow.work_store.now', return_value='2026-09-17T02:00:00+00:00'):
            second = self.store.finish(self.name, result='Second delivered result')
        self.assertNotEqual(first['completedAt'], second['completedAt'])
        history = self.store.history(self.name, limit=100)['events']
        completed = [e['text'] for e in history if e['kind'] == 'finish']
        self.assertEqual(completed, ['First delivered result', 'Second delivered result'])
        self.assertIn('First delivered result', second['document'])
        self.assertNotIn('First delivered result', context_text(second['document'])[0])

    def test_record_appends_timestamp_updates_current_and_optional_summary(self):
        first = self.store.record_event(self.name, 'Implementation ready', stage='验证', summary='待验证')
        self.assertEqual(first['stage'], '验证')
        self.assertEqual(first['summary'], '待验证')
        self.store.record_event(self.name, 'Validation passed')
        events = self.store.history(self.name)['events']
        self.assertEqual(events[-2]['text'], 'Implementation ready')
        self.assertIn('time', events[-1])
        current = context_text(self.store.resume(self.name)['document'])[0]
        self.assertIn('Validation passed', current)
        self.assertNotIn('Implementation ready', current)
        self.assertEqual(self.store.resume(self.name)['summary'], '待验证')

    def test_duplicate_current_sections_are_rejected_without_losing_draft(self):
        draft = Path(self.store.edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text() + '\n## 当前推进\nA conflicting instruction\n')
        with self.assertRaises(InboxError):
            self.store.save(draft)
        self.assertIn('conflicting', draft.read_text())

    def save_plan(self, text):
        draft = Path(self.store.plan_edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text().split('\n', 1)[0] + '\n' + text)
        return self.store.plan_save(draft)

    def test_plan_versions_dependency_join_and_completion(self):
        self.save_plan('- [ ] 准备\n- [ ] 前端\n  依赖：准备\n- [ ] 后端\n  依赖：准备\n- [ ] 发布\n  依赖：前端、后端\n')
        self.assertIn('flowchart TD', self.store.plan_show(self.name, graph=True)['graph'])
        with self.assertRaises(InboxError):
            self.store.record_event(self.name, 'too soon', step='发布', status='done')
        with self.assertRaises(InboxError):
            self.store.finish(self.name, result='not really done')
        for step in ['准备', '前端', '后端']:
            self.store.record_event(self.name, step + '完成', step=step, status='done')
        draft = Path(self.store.plan_edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text() + '\n说明：发布前核对。\n')
        self.store.plan_save(draft, reason='补充发布检查')
        self.store.plan_save(draft, reason='补充发布检查')
        self.assertEqual(len(self.store.plan_show(self.name, history=True)['versions']), 2)
        self.assertIn('- [ ] 准备', self.store.plan_show(self.name, version=1)['plan'])
        self.assertIn('- [x] 准备', self.store.plan_show(self.name)['plan'])
        self.store.record_event(self.name, '发布已验证', step='发布', status='done')
        result = self.store.finish(self.name, result='功能可用')
        self.assertEqual(result['attention'], 'Done')
        self.assertEqual(self.store.history(self.name)['events'][-1]['planVersion'], 2)

    def test_stale_plan_save_preserves_both_writers_and_draft(self):
        self.save_plan('- [ ] 实现\n')
        draft = Path(self.store.plan_edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        self.store.record_event(self.name, '实现通过', step='实现', status='done')
        with self.assertRaises(StaleSourceError):
            self.store.plan_save(draft)
        self.assertTrue(draft.exists())
        self.assertIn('- [x] 实现', self.store.plan_show(self.name)['plan'])

    def test_plan_update_preserves_active_step_status(self):
        self.save_plan('- [ ] 实现\n')
        self.store.record_event(self.name, '正在实现', step='实现', status='active')
        self.save_plan('- [ ] 实现\n\n补充说明。\n')
        self.assertEqual(self.store.plan_show(self.name)['nodes'][0]['state'], 'active')

    def test_save_retry_does_not_append_history_or_overwrite_later_writer(self):
        draft = Path(self.store.edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text().replace('Build the agreed change', 'Revised goal'))
        self.store.save(draft)
        self.store.record_event(self.name, 'Later progress')
        before = self.store.history(self.name)
        self.store.save(draft)
        self.assertEqual(self.store.history(self.name), before)
        self.assertIn('Later progress', context_text(self.store.resume(self.name)['document'])[0])

    def test_release_updates_pending_work_and_archive_updates_material_path(self):
        item = self.store.resume(self.name)
        self.store.link(self.name, 'material', path=item['absoluteWorkPath'])
        self.store.link(self.name, 'thread', url='codex://threads/example')
        finished = self.store.finish(self.name, result='Done')
        self.assertEqual(finished['resources'][0]['path'], finished['absoluteWorkPath'])
        self.store.release(self.name, 'codex://threads/example')
        restored = self.store.resume(self.name)
        self.assertEqual(restored['closeoutPending'], [])
        self.assertNotIn('待收尾', context_text(restored['document'])[0])

    def test_legacy_outcome_is_retired_on_reopen(self):
        item = self.store.finish(self.name, result='Old result')
        state = json.loads(self.store.state_path.read_text())
        state['items'][item['id']]['outcomeHeading'] = '交付与收尾 · demo/Work'
        self.store.state_path.write_text(json.dumps(state))
        path = self.root / item['workPath']
        path.write_text(path.read_text() + '\n## 交付与收尾 · demo/Work\n\nOld release evidence\n')
        self.store.transition(self.name, 'reopen')
        doc = self.store.resume(self.name)['document']
        self.assertIn('Old release evidence', doc)
        self.assertNotIn('Old release evidence', context_text(doc)[0])

    def test_plan_explicit_rename_preserves_identity_and_history_is_immutable(self):
        self.save_plan('- [ ] 旧步骤\n- [ ] 发布\n  依赖：旧步骤\n')
        old_id = self.store.plan_show(self.name)['nodes'][0]['id']
        draft = Path(self.store.plan_edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text().replace('旧步骤', '新步骤'))
        self.store.plan_save(draft, renames={'旧步骤': '新步骤'})
        self.assertEqual(self.store.plan_show(self.name)['nodes'][0]['id'], old_id)
        self.assertIn('旧步骤', self.store.plan_show(self.name, version=1)['plan'])
        draft = Path(self.store.edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text().replace('### 1 ·', '### tampered ·'))
        with self.assertRaises(InboxError):
            self.store.save(draft)

    def test_cli_user_journey_and_compact_resume(self):
        import subprocess
        cli = ROOT / 'bin/work'
        def run(*args, ok=True):
            proc = subprocess.run([sys.executable, str(cli), '--root', str(self.root), *args], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0 if ok else 1, proc.stderr)
            return proc.stdout + proc.stderr
        draft = json.loads(run('--json', 'plan', 'edit', self.name))['draftPath']
        path = Path(draft)
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text(path.read_text().split('\n', 1)[0] + '\n- [ ] 实现\n- [ ] 验证\n  依赖：实现\n')
        run('plan', 'save', draft)
        run('record', self.name, '--text', '先前执行证据', '--step', '实现', '--status', 'done')
        run('pause', self.name, '--reason', '明天继续')
        self.assertEqual(self.store.resume(self.name)['attention'], 'Waiting')
        run('reopen', self.name)
        run('record', self.name, '--text', '验证通过', '--step', '验证', '--status', 'done')
        output = run('resume', self.name)
        self.assertIn('当前计划', output)
        self.assertNotIn('先前执行证据', output)
        self.assertNotIn('stepStates', output)
        self.assertIn('先前执行证据', run('history', self.name))
        run('finish', self.name, '--result', 'CLI 验收通过')
        self.assertEqual(self.store.resume(self.name)['attention'], 'Done')

    def make_legacy(self):
        import shutil
        item = self.store.resume(self.name)
        state = json.loads(self.store.state_path.read_text())
        for key in ['lastProgress', 'eventCount']:
            state['items'][item['id']].pop(key, None)
        self.store.state_path.write_text(json.dumps(state))
        shutil.rmtree(self.store.internal / 'items' / item['id'])
        (self.root / item['workPath']).write_text('# Legacy\n\n## 事项说明\n\nGoal\n\n## 当前推进\n\nLEGACY PROGRESS\n')

    def test_legacy_update_preserves_before_exactly_once(self):
        self.make_legacy()
        item = self.store.resume(self.name)
        self.store.update(self.name, progress='NEW PROGRESS', expected_revision=item['revision'])
        events = self.store.history(self.name)['events']
        self.assertEqual(events[0]['previousProgress'], 'LEGACY PROGRESS')
        self.assertEqual(events[0]['text'], 'NEW PROGRESS')
        doc = self.store.resume(self.name)['document']
        self.assertEqual(doc.count('LEGACY PROGRESS'), 1)
        self.assertNotIn('LEGACY PROGRESS', context_text(doc)[0])

    def test_legacy_save_preserves_before_and_plan_migration_does_not_repeat_it(self):
        self.make_legacy()
        self.save_plan('- [ ] 实现\n')
        draft = Path(self.store.edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text().replace('## 当前推进\n\nLEGACY PROGRESS', '## 当前推进\n\nNEW PROGRESS'))
        self.store.save(draft)
        events = self.store.history(self.name)['events']
        self.assertEqual([e.get('previousProgress') for e in events], ['LEGACY PROGRESS', None])
        self.assertEqual(self.store.resume(self.name)['document'].count('LEGACY PROGRESS'), 1)

    def test_first_start_and_update_do_not_duplicate_new_progress_in_history(self):
        self.assertNotIn('previousProgress', self.store.history(self.name)['events'][0])
        item = self.store.resume(self.name)
        self.store.update(self.name, progress='New progress', expected_revision=item['revision'])
        event = self.store.history(self.name)['events'][-1]
        self.assertNotIn('previousProgress', event)

    def test_legacy_plan_is_loaded_and_preserved_as_first_version(self):
        self.make_legacy()
        item = self.store.resume(self.name)
        path = self.root / item['workPath']
        path.write_text(path.read_text() + '\n## 当前计划\n\n- [ ] Legacy plan\n')
        draft = Path(self.store.plan_edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        self.assertIn('Legacy plan', draft.read_text())
        draft.write_text(draft.read_text().replace('Legacy plan', 'New plan'))
        self.store.plan_save(draft)
        self.assertIn('Legacy plan', self.store.plan_show(self.name, version=1)['plan'])
        self.assertEqual(self.store.plan_show(self.name)['planVersion'], 2)
        self.assertIn('New plan', self.store.plan_show(self.name)['plan'])

    def test_legacy_plan_cannot_be_overwritten_by_general_save(self):
        self.make_legacy()
        item = self.store.resume(self.name)
        path = self.root / item['workPath']
        path.write_text(path.read_text() + '\n## 当前计划\n\nOriginal plan\n')
        draft = Path(self.store.edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text().replace('Original plan', 'Overwritten plan'))
        with self.assertRaises(InboxError):
            self.store.save(draft)
        self.assertIn('Original plan', path.read_text())
        self.assertIn('Overwritten plan', draft.read_text())

    def test_invalid_new_plan_leaves_legacy_and_index_untouched(self):
        self.make_legacy()
        item = self.store.resume(self.name)
        path = self.root / item['workPath']
        # Old unconstrained Markdown may contain duplicate names; preserve it literally.
        path.write_text(path.read_text() + '\n## 当前计划\n\n- [ ] A\n- [ ] A\n')
        original = path.read_text()
        draft = Path(self.store.plan_edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        with self.assertRaises(ValueError):
            self.store.plan_save(draft)
        self.assertEqual(path.read_text(), original)
        self.assertFalse(self.store.resume(self.name).get('planVersion'))
        draft.write_text(draft.read_text().split('\n', 1)[0] + '\n- [ ] Fixed name\n')
        self.store.plan_save(draft)
        self.assertEqual(self.store.plan_show(self.name, version=1)['plan'].count('- [ ] A'), 2)

    def test_unindexed_legacy_history_is_also_append_only(self):
        self.make_legacy()
        path = self.root / self.store.resume(self.name)['workPath']
        path.write_text(path.read_text() + '\n## 历史记录\n\nOriginal history\n')
        draft = Path(self.store.edit(self.name)['draftPath'])
        self.addCleanup(draft.unlink, missing_ok=True)
        draft.write_text(draft.read_text().replace('Original history', 'Changed history'))
        with self.assertRaises(InboxError):
            self.store.save(draft)
        self.assertIn('Original history', path.read_text())

    def test_plan_and_history_whitespace_cannot_bypass_protection(self):
        self.save_plan('- [ ] A\n')
        for before, after in [('- [ ] A', '- [ ] A '), ('> 计划已更新。', '> 计划已更新。 ')]:
            with self.subTest(before=before):
                draft = Path(self.store.edit(self.name)['draftPath'])
                self.addCleanup(draft.unlink, missing_ok=True)
                draft.write_text(draft.read_text().replace(before, after))
                with self.assertRaises(InboxError):
                    self.store.save(draft)

    def test_append_preserves_existing_history_boundary_whitespace(self):
        self.make_legacy()
        path = self.root / self.store.resume(self.name)['workPath']
        history = '## 历史记录\n\n> Previous event  \n\n\n'
        path.write_text(path.read_text() + '\n' + history)
        self.store.record_event(self.name, 'Next event')
        self.assertIn(history + '### ', path.read_text())

    def test_legacy_plan_snapshot_retains_raw_markdown_whitespace(self):
        self.make_legacy()
        path = self.root / self.store.resume(self.name)['workPath']
        raw = '\n\n    indented code  \n\n\n'
        path.write_text(path.read_text() + '\n## 当前计划\n' + raw)
        self.save_plan('New plan\n')
        self.assertEqual(self.store.plan_show(self.name, version=1)['plan'], raw)


if __name__ == '__main__':
    unittest.main()
