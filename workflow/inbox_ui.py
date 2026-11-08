"""Open the optional Inbox UI, keeping server lifetime in the invoking terminal."""
from __future__ import annotations

import errno
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import webbrowser

from . import WORKSPACE_ROOT
from .inbox_model import InboxError
from .work_store import WorkStore


def _server_factory():
    script = WORKSPACE_ROOT / 'practices/communication/html/inbox/scripts/inbox_app.py'
    if not script.is_file():
        raise InboxError('此代码副本没有 Inbox 界面；请使用包含 practices/communication/html/inbox 的完整工作空间。',
                         code='ui_unavailable')
    spec = importlib.util.spec_from_file_location('work_inbox_app', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_server


def _matches_workspace(port, root):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=0.75)
    try:
        connection.request('GET', '/api/identity')
        response = connection.getresponse()
        data = response.read(16_385)
        if response.status != 200 or len(data) > 16_384:
            return False
        identity = json.loads(data)
        return (isinstance(identity, dict) and identity.get('application') == 'work-inbox'
                and isinstance(identity.get('workspace'), str)
                and Path(identity['workspace']) == root
                and isinstance(identity.get('sourcePath'), str)
                and Path(identity['sourcePath']) == root / 'personal/inbox/inbox.md')
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        connection.close()


def _announce(root, port, reused, open_browser, json_output):
    url = f'http://127.0.0.1:{port}/'
    if json_output:
        print(json.dumps({'url': url, 'workspace': str(root), 'reused': reused},
                         ensure_ascii=False), flush=True)
    else:
        print(('复用 Personal Inbox：' if reused else 'Personal Inbox：') + url, flush=True)
        print('工作空间：' + str(root), flush=True)
        if not reused:
            print('此终端正在运行服务；按 Ctrl+C 结束。', flush=True)
    if open_browser:
        try:
            opened = webbrowser.open(url)
        except (webbrowser.Error, OSError):
            opened = False
        if not opened:
            print('浏览器未自动打开，请手动访问：' + url, file=sys.stderr, flush=True)


def open_inbox(root, port=8765, open_browser=True, json_output=False):
    if not 0 <= port <= 65535:
        raise ValueError('--port 必须在 0–65535 之间。')
    root = Path(root).resolve()
    create_server = _server_factory()
    # Use the shared initializer: an empty Inbox needs no invented Work item.
    WorkStore(root).snapshot()
    try:
        server = create_server(root, port)
    except OSError as exc:
        if port == 0 or (exc.errno not in {errno.EADDRINUSE, errno.EACCES}
                         and getattr(exc, 'winerror', None) not in {10048, 10013}):
            raise
        if _matches_workspace(port, root):
            _announce(root, port, True, open_browser, json_output)
            return 0
        server = create_server(root, 0)
    try:
        _announce(root, server.server_address[1], False, open_browser, json_output)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
