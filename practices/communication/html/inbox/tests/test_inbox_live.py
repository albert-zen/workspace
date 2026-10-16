import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from _support import SCRIPTS
sys.path.insert(0, str(SCRIPTS))
from inbox_app import create_server
from workflow.work_store import WorkStore


class LiveInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkStore(self.root)
        self.item = self.store.start('demo/Live', summary='Original')
        self.server = create_server(self.root, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def get(self, route):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request('GET', route)
        return conn.getresponse()

    def snapshot(self, version=''):
        response = self.get('/api/snapshot?version=' + version)
        self.assertEqual(response.status, 200)
        return json.loads(response.read())

    def test_detects_cli_work_and_direct_edits_with_lightweight_unchanged_response(self):
        first = self.snapshot()
        self.assertEqual(self.snapshot(first['version']), {'version': first['version'], 'unchanged': True})
        self.store.transition('demo/Live', 'pause', reason='Later')
        second = self.snapshot(first['version'])
        waiting = next(s for s in second['inbox']['sections'] if s['status'] == 'Waiting')
        self.assertEqual(waiting['items'][0]['lifecycleStatus'], 'paused')
        self.store.record_event('demo/Live', 'Work body changed only')
        third = self.snapshot(second['version'])
        self.assertEqual(second['inbox']['revision'], third['inbox']['revision'])
        self.assertNotEqual(second['workVersions'], third['workVersions'])
        path = self.root / self.item['workPath']
        path.write_text(path.read_text() + '\nA direct edit\n')
        fourth = self.snapshot(third['version'])
        self.assertNotEqual(fourth['version'], third['version'])
        path.rename(path.with_name('renamed.md'))
        self.assertNotEqual(self.snapshot(fourth['version'])['workVersions'], fourth['workVersions'])

    def test_pending_transaction_does_not_serve_a_partial_snapshot(self):
        first = self.snapshot()
        self.store.pending.write_text('{}')
        response = self.get('/api/snapshot?version=' + first['version'])
        self.assertEqual(response.status, 503)
        self.assertEqual(json.loads(response.read())['error']['code'], 'pending_operation')
        self.store.pending.unlink()
        self.assertTrue(self.snapshot(first['version'])['unchanged'])

    def event(self, response):
        while True:
            line = response.readline()
            if not line:
                self.fail('Stream ended before notification')
            if line.startswith(b'data: '):
                return json.loads(line[6:])

    def test_shared_stream_reports_external_changes_and_reconnects_with_current_version(self):
        stream = self.get('/api/events')
        self.assertEqual(stream.status, 200)
        self.assertIn('text/event-stream', stream.getheader('Content-Type'))
        first = self.event(stream)
        self.store.transition('demo/Live', 'pause', reason='Later')
        changed = self.event(stream)
        self.assertNotEqual(first, changed)
        self.assertEqual(changed['version'], self.snapshot()['version'])
        path = self.root / self.item['workPath']
        path.write_text(path.read_text() + '\nExternal Work edit\n')
        changed = self.event(stream)
        self.assertEqual(changed['version'], self.snapshot()['version'])
        reconnect = self.get('/api/events')
        self.assertEqual(self.event(reconnect), changed)

    def test_restart_reuses_closed_stream_port_but_not_a_running_server(self):
        stream = self.get('/api/events')
        self.event(stream)
        with self.assertRaises(OSError):
            create_server(self.root, self.port)
        self.server.shutdown()
        self.server.server_close()
        stream.read()
        restarted = create_server(self.root, self.port)
        restarted.server_close()
