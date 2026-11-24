"""Exercise the human launcher against real loopback servers and isolated data."""
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class WorkInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data 空间'
        self.cli = ROOT / 'bin/work'

    def command(self, root, *args):
        return [sys.executable, '-X', 'utf8', str(self.cli), '--root', str(root),
                '--json', 'inbox', '--no-open', *map(str, args)]

    def start(self, root, port=0, *, command=None):
        proc = subprocess.Popen(command or self.command(root, '--port', port),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', cwd=tempfile.gettempdir())
        def stop():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
        self.addCleanup(stop)
        line = self.read_line(proc)
        self.assertTrue(line, 'launcher exited before announcing its URL')
        ready = json.loads(line)
        self.assertIsNone(proc.poll())
        return proc, ready

    @staticmethod
    def read_line(proc):
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True).start()
        return lines.get(timeout=10)

    def identity(self, url):
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        try:
            conn.request('GET', '/api/identity')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read())
        finally:
            conn.close()

    def test_fresh_root_starts_ui_without_creating_a_fake_work(self):
        _, ready = self.start(self.root)
        identity = self.identity(ready['url'])
        self.assertEqual(identity['application'], 'work-inbox')
        self.assertEqual(Path(identity['workspace']), self.root.resolve())
        db = self.root / 'agent-data/workflow/work.db'
        self.assertTrue(db.is_file())
        self.assertFalse((self.root / 'personal/inbox/inbox.md').exists())
        from workflow.work_store import WorkStore
        self.assertEqual(WorkStore(self.root).list(include_hidden=True), [])
        self.assertFalse(ready['reused'])

    def test_repeated_command_reuses_only_matching_workspace_and_returns(self):
        proc, ready = self.start(self.root)
        from workflow.work_db import read_version_token
        before = read_version_token(self.root / 'agent-data/workflow/work.db')
        from urllib.parse import urlsplit
        reused = subprocess.run(self.command(self.root, '--port', urlsplit(ready['url']).port),
                                text=True, encoding='utf-8', capture_output=True, timeout=8)
        self.assertEqual(reused.returncode, 0, reused.stderr)
        result = json.loads(reused.stdout)
        self.assertTrue(result['reused'])
        self.assertEqual(result['url'], ready['url'])
        self.assertIsNone(proc.poll())
        self.assertEqual(read_version_token(self.root / 'agent-data/workflow/work.db'), before)

    def test_other_workspaces_port_is_not_reused(self):
        _, other = self.start(self.root / 'other')
        from urllib.parse import urlsplit
        _, ready = self.start(self.root, urlsplit(other['url']).port)
        self.assertNotEqual(ready['url'], other['url'])
        self.assertEqual(Path(self.identity(ready['url'])['workspace']), self.root.resolve())
        self.assertEqual(Path(self.identity(other['url'])['workspace']), (self.root / 'other').resolve())

    def test_browser_fallback_keeps_working_url(self):
        from workflow.inbox_ui import open_inbox
        _, ready = self.start(self.root)
        from urllib.parse import urlsplit
        output, errors = io.StringIO(), io.StringIO()
        with patch('workflow.inbox_ui.webbrowser.open', return_value=False) as browser:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(open_inbox(self.root, urlsplit(ready['url']).port), 0)
        browser.assert_called_once_with(ready['url'])
        self.assertIn(ready['url'], output.getvalue())
        self.assertIn('浏览器', errors.getvalue())

    def test_fresh_server_responds_while_browser_opens(self):
        launcher = """
import sys
from urllib.request import urlopen

sys.path.insert(0, sys.argv[1])
from workflow import inbox_ui
from workflow.work_cli import main

def open_browser(url):
    try:
        with urlopen(url, timeout=3) as response:
            status = response.status
    except OSError:
        status = 0
    print(status, flush=True)
    return status == 200

inbox_ui.webbrowser.open = open_browser
raise SystemExit(main(sys.argv[2:]))
"""
        command = [sys.executable, '-X', 'utf8', '-c', launcher, str(ROOT),
                   '--root', str(self.root), '--json', 'inbox', '--port', '0']
        proc, ready = self.start(self.root, command=command)
        self.assertFalse(ready['reused'])
        self.assertEqual(self.read_line(proc).strip(), '200')

    def test_unrelated_http_server_is_not_reused(self):
        class OtherHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"application":"another-app"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), OtherHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        _, ready = self.start(self.root, server.server_address[1])
        self.assertNotEqual(ready['url'], f'http://127.0.0.1:{server.server_address[1]}/')
        self.assertEqual(Path(self.identity(ready['url'])['workspace']), self.root.resolve())

    def test_cli_without_ui_still_supports_core_and_explains_missing_ui(self):
        checkout = self.root / 'code'
        shutil.copytree(ROOT / 'workflow', checkout / 'workflow',
                        ignore=shutil.ignore_patterns('__pycache__'))
        (checkout / 'bin').mkdir()
        shutil.copy2(self.cli, checkout / 'bin/work')
        command = [sys.executable, '-X', 'utf8', str(checkout / 'bin/work'),
                   '--root', str(self.root / 'data')]
        result = subprocess.run([*command, 'inbox', '--no-open'], text=True,
                                encoding='utf-8', capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Inbox 界面', result.stderr)
        self.assertFalse((self.root / 'data').exists())
        result = subprocess.run([*command, 'list'], text=True, encoding='utf-8',
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_port_does_not_initialize_data(self):
        result = subprocess.run(self.command(self.root, '--port', 65536), text=True,
                                encoding='utf-8', capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
