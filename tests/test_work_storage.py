import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow.work_store import WorkStore


class WorkStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = WorkStore(self.root)
        self.internal = self.root / 'agent-data/workflow'

    def test_completed_write_retains_small_receipt_and_business_history(self):
        self.store.start('demo/Large', summary='Large document')
        text = '用户的完整业务内容\n' * 30000
        self.store.record_event('demo/Large', text)
        receipts = list((self.internal / 'operations').glob('*.json'))
        self.assertLess(sum(p.stat().st_size for p in receipts), 20000)
        latest = max(receipts, key=lambda p: p.stat().st_mtime_ns)
        receipt = json.loads(latest.read_text())
        state = next(x for x in receipt['writes'] if x['path'].endswith('state.json'))
        raw = (self.internal / 'state.json').read_bytes()
        self.assertEqual(state['after'], {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        self.assertEqual(self.store.history('demo/Large')['events'][-1]['text'], text)
        self.assertFalse((self.internal / 'pending.json').exists())

    def test_interruption_at_receipt_or_pending_removal_recovers_once(self):
        import os
        from unittest.mock import patch
        for stage in ('receipt', 'pending'):
            with self.subTest(stage=stage):
                name = 'demo/' + stage
                self.store.start(name, summary='Keep history')
                real_replace, real_unlink = os.replace, Path.unlink
                def replace(src, dst):
                    if stage == 'receipt' and Path(dst).parent.name == 'operations':
                        raise OSError('simulated receipt failure')
                    return real_replace(src, dst)
                def unlink(path, *args, **kwargs):
                    if stage == 'pending' and path.name == 'pending.json':
                        raise OSError('simulated pending cleanup failure')
                    return real_unlink(path, *args, **kwargs)
                with patch('os.replace', side_effect=replace), patch.object(Path, 'unlink', unlink):
                    with self.assertRaises(OSError):
                        self.store.record_event(name, 'Exactly once')
                pending = json.loads((self.internal / 'pending.json').read_text())
                self.assertTrue(all(isinstance(c['after'], (str, type(None))) for c in pending['writes']))
                self.store.recover()
                texts = [e['text'] for e in self.store.history(name)['events']]
                self.assertEqual(texts.count('Exactly once'), 1)
                self.assertFalse((self.internal / 'pending.json').exists())

    def test_legacy_pending_conflict_keeps_external_edit_and_recovery_payload(self):
        import os
        from unittest.mock import patch
        from workflow.inbox_model import InboxError
        self.store.start('demo/Conflict', summary='Original')
        real_replace = os.replace
        def interrupt(src, dst):
            if Path(dst).name == 'state.json':
                raise OSError('state write interrupted')
            return real_replace(src, dst)
        with patch('os.replace', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.store.record_event('demo/Conflict', 'Pending content')
        pending = self.internal / 'pending.json'
        original_pending = pending.read_bytes()
        operation = json.loads(original_pending)
        self.assertNotIn('version', operation)
        document_change = next(c for c in operation['writes'] if c['path'].endswith('.md') and 'projects/' in c['path'])
        document = self.root / document_change['path']
        document.write_text('External edit that must survive')
        with self.assertRaises(InboxError):
            self.store.recover()
        self.assertEqual(document.read_text(), 'External edit that must survive')
        self.assertEqual(pending.read_bytes(), original_pending)
