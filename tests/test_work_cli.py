from pathlib import Path
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow.work_store import WorkStore


class WorkJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cli = ROOT / 'bin/work'
        self.store = WorkStore(self.root)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.cli), '--root', str(self.root), *args],
                              text=True, capture_output=True, cwd='/tmp')

    def test_standalone_checkout_and_installed_link_need_no_inbox_ui(self):
        checkout = self.root / 'code checkout'
        checkout.mkdir()
        shutil.copytree(ROOT / 'workflow', checkout / 'workflow', ignore=shutil.ignore_patterns('__pycache__'))
        (checkout / 'bin').mkdir()
        shutil.copy2(ROOT / 'bin/work', checkout / 'bin/work')
        launcher = self.root / 'installed-work'
        launcher.symlink_to(checkout / 'bin/work')

        def run(*args):
            result = subprocess.run([sys.executable, str(launcher), *args],
                                    cwd='/tmp', text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        run('start', 'demo/Default root', '--summary', 'No UI required')
        self.assertTrue((checkout / 'personal/inbox/inbox.md').is_file())
        data = self.root / 'separate data'
        run('--root', str(data), 'start', 'demo/Independent', '--summary', 'Separate data')
        self.assertIn('Separate data', run('--root', str(data), 'resume', 'demo/Independent'))
        run('--root', str(data), 'record', 'demo/Independent', '--text', 'Verified')
        run('--root', str(data), 'finish', 'demo/Independent', '--result', 'Complete')
        self.assertTrue((data / 'agent-data/projects/demo/work/archive/Independent.md').is_file())
        self.assertFalse((checkout / 'practices').exists())

    def test_new_work_starts_in_one_action_and_retry_reuses_context(self):
        result = self.run_cli('start', 'demo/One', '--summary', 'Read a document')
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.store.resume('demo/One')
        self.assertIn('Read a document', result.stdout)
        self.assertIn('edit', result.stdout)
        again = self.run_cli('start', 'demo/One', '--summary', 'Do not overwrite existing context')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.resume('demo/One')['document'], first['document'])
        self.assertNotIn('Do not overwrite', self.store.resume('demo/One')['summary'])

    def test_read_and_misspelled_start_do_not_create_work(self):
        self.store.add('demo/One')
        for action in ('resume', 'start'):
            result = self.run_cli(action, 'demo/Typo')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('list', result.stderr)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.resume('demo/One')['lifecycleStatus'], 'planned')

    def test_invalid_new_start_does_not_leave_half_registered_item(self):
        result = self.run_cli('start', 'demo/New', '--summary', 'Goal', '--worktree')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.store.list(), [])

    def test_entry_is_read_only_and_offers_small_action_list(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ('start', 'resume', 'edit', 'finish'):
            self.assertIn(command, result.stdout)
        self.assertIn('workflow.md', result.stdout)
        self.assertFalse((self.root / 'personal/inbox/inbox.md').exists())
        self.assertFalse((self.root / 'agent-data/workflow').exists())

    def test_edit_supplies_save_command_and_conflict_keeps_both_versions(self):
        self.store.start('demo/One', summary='Original')
        drafts = []
        for _ in range(2):
            result = self.run_cli('edit', 'demo/One')
            self.assertEqual(result.returncode, 0, result.stderr)
            cmd = next(line.split('：', 1)[1] for line in result.stdout.splitlines() if line.startswith('编辑上面的副本后保存：'))
            args = shlex.split(cmd)
            draft = Path(args[-1])
            self.addCleanup(lambda p=draft: (p.unlink(missing_ok=True), p.parent.rmdir()))
            drafts.append((draft, args))
        first, first_cmd = drafts[0]
        second, second_cmd = drafts[1]
        self.assertNotEqual(first, second)
        first.write_text(first.read_text().replace('Original', 'Saved first'))
        second.write_text(second.read_text().replace('Original', 'Unsubmitted second'))
        saved = subprocess.run([sys.executable, shutil.which(first_cmd[0]) or first_cmd[0], *first_cmd[1:]], text=True, capture_output=True)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        conflict = subprocess.run([sys.executable, shutil.which(second_cmd[0]) or second_cmd[0], *second_cmd[1:]], text=True, capture_output=True)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn('resume demo/One', conflict.stderr)
        self.assertIn('edit demo/One', conflict.stderr)
        self.assertIn('Unsubmitted second', second.read_text())
        self.assertIn('Saved first', self.store.resume('demo/One')['document'])

    def test_current_context_preserves_constraints_and_explicit_history_can_expand(self):
        item = self.store.start('demo/One', summary='Goal')
        path = self.root / item['workPath']
        text = ('# One\n\n## 事项说明\nGoal\n\n## 当前推进\nNext step\n\n'
                '## 特别约束\nNever publish before approval\n\n'
                '## 相关材料\n[Source](../source.md)\n\n'
                '## 历史记录\nOld discussion\n')
        path.write_text(text)
        brief = self.run_cli('resume', 'demo/One')
        self.assertIn('Never publish before approval', brief.stdout)
        self.assertIn('../source.md', brief.stdout)
        self.assertNotIn('Old discussion', brief.stdout)
        self.assertIn('历史记录未展开', brief.stdout)
        self.assertIn('--full', brief.stdout)
        full = self.run_cli('resume', 'demo/One', '--full')
        self.assertIn(text, full.stdout)
        path.write_text('# Legacy\n\n## 历史记录\nMight contain a current constraint\n')
        self.assertIn('Might contain a current constraint', self.run_cli('resume', 'demo/One').stdout)

    def test_persistent_design_survives_progress_in_description_or_custom_section(self):
        for heading in ('事项说明', '当前设计'):
            with self.subTest(heading=heading):
                name = 'demo/' + heading
                started = self.run_cli('start', name, '--summary', 'Goal')
                self.assertEqual(started.returncode, 0, started.stderr)
                draft = self.root / (heading + '-draft.md')
                edited = self.run_cli('edit', name, '--output', str(draft))
                self.assertEqual(edited.returncode, 0, edited.stderr)
                design = 'Reuse the existing boundary because callers need one owner.'
                text = draft.read_text()
                if heading == '事项说明':
                    text = text.replace('Goal', 'Goal\n\n' + design)
                else:
                    text = text.replace('## 当前推进', '## 当前设计\n\n' + design + '\n\n## 当前推进', 1)
                draft.write_text(text)
                saved = self.run_cli('save', str(draft))
                self.assertEqual(saved.returncode, 0, saved.stderr)
                for progress in ('Verified persistence', 'Ready for review'):
                    recorded = self.run_cli('record', name, '--text', progress)
                    self.assertEqual(recorded.returncode, 0, recorded.stderr)
                brief = self.run_cli('resume', name)
                self.assertEqual(brief.returncode, 0, brief.stderr)
                self.assertIn(design, brief.stdout)
                self.assertIn('Ready for review', brief.stdout)
                self.assertNotIn('Verified persistence', brief.stdout)
                full = self.run_cli('resume', name, '--full')
                self.assertEqual(full.returncode, 0, full.stderr)
                self.assertIn('Verified persistence', full.stdout)

    def test_design_replacement_needs_explicit_record_to_retain_old_reason(self):
        name = 'demo/Design change'
        original = 'Choose cache A because the upstream accepts stale reads.'
        replacement = 'Choose cache B because the upstream now requires fresh reads.'
        started = self.run_cli('start', name, '--summary', original)
        self.assertEqual(started.returncode, 0, started.stderr)

        def replace_design(old, new, filename):
            draft = self.root / filename
            edited = self.run_cli('edit', name, '--output', str(draft))
            self.assertEqual(edited.returncode, 0, edited.stderr)
            draft.write_text(draft.read_text().replace(old, new, 1))
            saved = self.run_cli('save', str(draft))
            self.assertEqual(saved.returncode, 0, saved.stderr)

        # Saving current text is not a body-version archive.
        replace_design(original, replacement, 'first.md')
        full = self.run_cli('resume', name, '--full')
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertNotIn(original, full.stdout)
        history = self.run_cli('--json', 'history', name)
        self.assertEqual(history.returncode, 0, history.stderr)
        self.assertNotIn(original, history.stdout)

        change = 'Superseded: ' + replacement + ' Replaced by cache C after the API changed again.'
        recorded = self.run_cli('record', name, '--text', change)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        current = 'Choose cache C because the API exposes conditional reads.'
        replace_design(replacement, current, 'second.md')
        progress = self.run_cli('record', name, '--text', 'Validated conditional reads')
        self.assertEqual(progress.returncode, 0, progress.stderr)
        brief = self.run_cli('resume', name)
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn(current, brief.stdout)
        self.assertNotIn(replacement, brief.stdout)
        full = self.run_cli('resume', name, '--full')
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertIn(change, full.stdout)
        history = self.run_cli('--json', 'history', name)
        self.assertEqual(history.returncode, 0, history.stderr)
        events = json.loads(history.stdout)['events']
        self.assertEqual(sum(event['text'] == change for event in events), 1)

    def test_plan_versions_and_project_material_survive_finish_and_reopen(self):
        name = 'demo/Linked design'
        material = self.root / 'agent-data/projects/demo/work/active/linked-design/design.md'
        material.parent.mkdir(parents=True)
        material.write_text('# Shared project design\n\nOne maintained source.\n')
        original_stat = material.stat()
        started = self.run_cli('start', name, '--summary', 'Implement shared project design')
        self.assertEqual(started.returncode, 0, started.stderr)
        draft = self.root / 'work-draft.md'
        edited = self.run_cli('edit', name, '--output', str(draft))
        self.assertEqual(edited.returncode, 0, edited.stderr)
        link = '[Project design](linked-design/design.md)'
        draft.write_text(draft.read_text().replace('## 当前推进', '## 相关材料\n\n' + link + '\n\n## 当前推进', 1))
        saved = self.run_cli('save', str(draft))
        self.assertEqual(saved.returncode, 0, saved.stderr)

        for version, plan in enumerate(('# Plan\n\n- [ ] Verify persistence\n',
                                        '# Plan\n\n- [x] Verify persistence\n- [x] Review links\n'), 1):
            plan_draft = self.root / ('plan-' + str(version) + '.md')
            edited = self.run_cli('plan', 'edit', name, '--output', str(plan_draft))
            self.assertEqual(edited.returncode, 0, edited.stderr)
            metadata = plan_draft.read_text().splitlines(keepends=True)[0]
            plan_draft.write_text(metadata + plan)
            saved = self.run_cli('plan', 'save', str(plan_draft), '--reason', 'Plan change ' + str(version))
            self.assertEqual(saved.returncode, 0, saved.stderr)

        for command, options, directory in (('finish', ('--result', 'Delivered'), 'archive'),
                                             ('reopen', ('--reason', 'Follow up'), 'active')):
            moved = self.run_cli(command, name, *options)
            self.assertEqual(moved.returncode, 0, moved.stderr)
            resumed = self.run_cli('--json', 'resume', name, '--full')
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            item = json.loads(resumed.stdout)
            work = self.root / item['workPath']
            self.assertEqual(work.parent.name, directory)
            relative = '../active/linked-design/design.md' if directory == 'archive' else 'linked-design/design.md'
            self.assertIn('[Project design](' + relative + ')', item['document'])
            self.assertEqual((work.parent / relative).resolve(), material)
            self.assertTrue((work.parent / relative).is_file())
            self.assertEqual(material.stat().st_ino, original_stat.st_ino)
            self.assertEqual(material.read_text(), '# Shared project design\n\nOne maintained source.\n')
            self.assertEqual(list(self.root.rglob('design.md')), [material])
            old = self.run_cli('plan', 'show', name, '--version', '1')
            self.assertEqual(old.returncode, 0, old.stderr)
            self.assertIn('- [ ] Verify persistence', old.stdout)
            self.assertNotIn('Review links', old.stdout)
            latest = self.run_cli('plan', 'show', name)
            self.assertEqual(latest.returncode, 0, latest.stderr)
            self.assertIn('- [x] Review links', latest.stdout)
            history = self.run_cli('--json', 'plan', 'history', name)
            self.assertEqual(history.returncode, 0, history.stderr)
            self.assertEqual([entry['version'] for entry in json.loads(history.stdout)['versions']], [1, 2])

    def test_fenced_headings_and_ambiguous_sections_never_hide_constraints(self):
        item = self.store.start('demo/One', summary='Goal')
        path = self.root / item['workPath']
        text = ('# One\n\n## 事项说明\n~~~md\n## 历史记录\nKeep code\n~~~\n'
                '## 当前推进\nCurrent\n## 历史记录\nOld\n## 当前推进\nAnother current\n')
        path.write_text(text)
        self.assertIn(text, self.run_cli('resume', 'demo/One').stdout)

    def test_install_is_cwd_independent_and_preserves_existing_program(self):
        bindir = self.root / 'my bin'
        result = self.run_cli('install', '--bin-dir', str(bindir))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(bindir / 'work'), '--root', str(self.root)],
                                capture_output=True, text=True, cwd='/tmp')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.root), result.stdout)
        (bindir / 'work').unlink()
        (bindir / 'work').write_text('Existing program')
        blocked = self.run_cli('install', '--bin-dir', str(bindir))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertEqual((bindir / 'work').read_text(), 'Existing program')

    def test_completed_work_requires_explicit_reopen(self):
        item = self.store.start('demo/One', summary='Goal')
        self.store.finish('demo/One', result='Done')
        attempt = self.run_cli('start', 'demo/One', '--summary', 'Another goal')
        self.assertNotEqual(attempt.returncode, 0)
        self.assertIn('reopen demo/One', attempt.stderr)
        self.assertEqual(self.store.resume('demo/One')['lifecycleStatus'], 'completed')

    def test_new_start_reuses_real_tree_and_keeps_other_work_out_of_context(self):
        repo = self.root / 'repo'; repo.mkdir()
        for args in [('init', '-b', 'main'), ('config', 'user.name', 'Test'),
                     ('config', 'user.email', 'test@example.invalid'), ('commit', '--allow-empty', '-m', 'initial')]:
            subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        self.store.add('demo/Other', 'Unrelated private context')
        first = self.run_cli('start', 'demo/Tree', '--summary', 'Goal', '--worktree', '--repo', str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        item = self.store.resume('demo/Tree')
        tree = Path(item['resources'][0]['path'])
        (tree / 'AGENTS.md').write_text('Project rules')
        second = self.run_cli('start', 'demo/Tree', '--worktree', '--repo', str(repo))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.store.resume('demo/Tree')['resources']), 1)
        self.assertIn(str(tree / 'AGENTS.md'), second.stdout)
        self.assertNotIn('Unrelated private context', second.stdout)
        finished = self.run_cli('finish', 'demo/Tree', '--result', 'Delivered')
        self.assertEqual(finished.returncode, 0, finished.stderr)
        self.assertIn('待收尾', finished.stdout)
        self.assertIn('finish demo/Tree', finished.stdout)
        self.assertTrue(tree.exists())
        (tree / 'AGENTS.md').unlink()
        self.assertEqual(self.run_cli('finish', 'demo/Tree').returncode, 0)
        self.assertFalse(tree.exists())

    def test_linked_pr_is_reported_until_platform_verification_and_release(self):
        self.store.start('demo/One', summary='Goal')
        self.store.link('demo/One', 'pr', url='https://github.com/example/repo/pull/1')
        finished = self.run_cli('finish', 'demo/One', '--result', 'Implementation complete')
        self.assertEqual(finished.returncode, 0, finished.stderr)
        self.assertIn('待收尾', finished.stdout)
        self.assertIn('https://github.com/example/repo/pull/1', finished.stdout)
        self.assertIn('finish demo/One', finished.stdout)
        self.assertEqual(self.store.resume('demo/One')['lifecycleStatus'], 'completed')
        self.store.release('demo/One', 'https://github.com/example/repo/pull/1')
        retried = self.run_cli('finish', 'demo/One')
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertFalse(self.store.resume('demo/One')['closeoutPending'])

    def test_programmatic_update_and_finish_conflicts_provide_precise_recovery(self):
        self.store.start('demo/One', summary='Goal')
        stale = self.store.resume('demo/One')['revision']
        self.store.update('demo/One', summary='Newer summary', expected_revision=stale)
        for action in [('update', 'demo/One', '--summary', 'Stale change'),
                       ('finish', 'demo/One', '--result', 'Stale completion')]:
            result = self.run_cli(*action, '--expected-revision', stale)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('resume demo/One', result.stderr)
            self.assertIn('--json', result.stderr)
        current = self.store.resume('demo/One')
        self.assertEqual(current['summary'], 'Newer summary')
        self.assertEqual(current['lifecycleStatus'], 'active')


if __name__ == '__main__':
    unittest.main()
