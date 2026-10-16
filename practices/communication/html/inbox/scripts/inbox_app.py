#!/usr/bin/env python3
"""Run the loopback-only Personal Inbox web application."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

# Direct-script startup resolves the shared package from the code checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from workflow.inbox_model import InboxError, StaleSourceError, revision_for
from workflow.work_documents import WorkDocumentError, WorkDocuments
from workflow.work_store import WorkStore


MAX_BODY = 1_048_576
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/inbox.css": ("inbox.css", "text/css; charset=utf-8"),
    "/assets/inbox.js": ("inbox.js", "text/javascript; charset=utf-8"),
}


def _error(code: str, message: str, *, field=None, line=None, action: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "field": field, "line": line, "action": action}}


class InboxServer(ThreadingHTTPServer):
    # Permit restart after SSE sockets enter TIME_WAIT; a live listener still owns its port.
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.inbox_path = self.repo_root / "personal" / "inbox" / "inbox.md"
        self.assets_root = Path(__file__).resolve().parent.parent / "assets"
        self.shared_root = self.assets_root.parent.parent / "shared"
        self.work_documents = WorkDocuments(self.repo_root)
        self.work_store = WorkStore(self.repo_root)
        self.changes = threading.Condition()
        self.stopping = threading.Event()
        self.change = None
        self.observer = None
        super().__init__(address, handler)
        self.observer = threading.Thread(target=self._observe, daemon=True)
        self.observer.start()

    def _observe(self):
        store = WorkStore(self.repo_root)
        while not self.stopping.is_set():
            try:
                with store._locked():
                    version = json.dumps(store._view_versions(), sort_keys=True).encode()
                change = {'version': revision_for(version)}
            except (InboxError, OSError, UnicodeError):
                change = {'unavailable': True}
            with self.changes:
                if change != self.change:
                    self.change = change
                    self.changes.notify_all()
            self.stopping.wait(0.75)

    def server_close(self):
        self.stopping.set()
        with self.changes:
            self.changes.notify_all()
        if self.observer is not None:
            self.observer.join(timeout=2)
        super().server_close()


class InboxHandler(BaseHTTPRequestHandler):
    server: InboxServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("inbox: " + format % args + "\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'none'; connect-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(), "application/json; charset=utf-8")

    def _api_error(self, status: int, code: str, message: str, *, field=None, line=None, action: str) -> None:
        self._json(status, _error(code, message, field=field, line=line, action=action))

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _events(self):
        self.close_connection = True
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        previous = None
        try:
            self.wfile.write(b'retry: 1500\n\n')
            self.wfile.flush()
            while not self.server.stopping.is_set():
                with self.server.changes:
                    self.server.changes.wait_for(
                        lambda: self.server.change != previous or self.server.stopping.is_set(), timeout=15)
                    change = self.server.change
                if self.server.stopping.is_set():
                    return
                if change is not None and change != previous:
                    body = 'data: ' + json.dumps(change) + '\n\n'
                    previous = change
                else:
                    body = ': heartbeat\n\n'
                self.wfile.write(body.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._api_error(403, "unsafe_origin", "只允许通过当前本机 Inbox 地址读取。", action="从服务打印的本机地址重新打开页面。")
            return
        parsed = urlsplit(self.path)
        if parsed.path == '/api/events' and not parsed.query:
            self._events()
            return
        if parsed.path in STATIC and not parsed.query:
            filename, content_type = STATIC[parsed.path]
            try:
                body = (self.server.assets_root / filename).read_bytes()
                if filename in {"inbox.css", "inbox.js"}:
                    shared = self.server.shared_root / ("components" + Path(filename).suffix)
                    body = shared.read_bytes() + b"\n" + body
                self._send(200, body, content_type)
            except OSError:
                self._api_error(500, "read_failed", "无法读取应用资源。", action="检查应用文件后重试。")
            return
        if parsed.path == "/api/snapshot":
            values = parse_qs(parsed.query, keep_blank_values=True)
            if set(values) - {'version'} or len(values.get('version', [''])) != 1:
                self._api_error(400, 'invalid_request', '版本参数无效。', action='重新打开 Inbox。')
                return
            try:
                self._json(200, self.server.work_store.live_snapshot(values.get('version', [''])[0]))
            except InboxError as exc:
                self._api_error(503, exc.code, str(exc), action=exc.action)
            except (OSError, UnicodeError):
                self._api_error(503, 'read_failed', '暂时无法读取最新数据。', action='稍后自动重试。')
            return
        if parsed.path == "/api/inbox" and not parsed.query:
            try:
                self._json(200, self.server.work_store.snapshot())
            except InboxError as exc:
                self._api_error(500 if exc.code == "read_failed" else 422, exc.code, str(exc), field=exc.field, line=exc.line, action=exc.action)
            return
        if parsed.path == "/api/work-documents" and not parsed.query:
            try:
                self._json(200, {"documents": self.server.work_documents.list_documents()})
            except (OSError, UnicodeError):
                self._api_error(500, "read_failed", "无法枚举 Work 文档。", action="检查 agent-data/projects 的读取权限后重试。")
            return
        if parsed.path == "/api/work-document":
            values = parse_qs(parsed.query, keep_blank_values=True)
            if set(values) != {"path"} or len(values["path"]) != 1:
                self._api_error(400, "invalid_request", "必须提供一个 Work 文档 path。", field="workPath", action="从当前 Work 列表重新选择。")
                return
            try:
                self._json(200, self.server.work_documents.read(values["path"][0]))
            except WorkDocumentError as exc:
                status = 403 if exc.code == "forbidden_path" else (500 if exc.code == "read_failed" else 404)
                self._api_error(status, exc.code, str(exc), field="workPath", action="编辑关联并从当前 Work 列表选择可用文档。")
            return
        self._api_error(404, "not_found", "找不到这个本机应用路径。", action="返回 Personal Inbox。")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        allowed_hosts = {f"127.0.0.1:{self.server.server_address[1]}", f"localhost:{self.server.server_address[1]}"}
        return self._host_ok() and origin in {f"http://{value}" for value in allowed_hosts}

    def do_PUT(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/api/inbox" or urlsplit(self.path).query:
            self._api_error(404, "not_found", "找不到这个本机应用路径。", action="返回 Personal Inbox。")
            return
        if not self._origin_ok():
            self._api_error(403, "unsafe_origin", "只允许从当前本机 Inbox 页面保存。", action="从服务打印的本机地址重新打开页面。")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._api_error(400, "invalid_request", "保存请求必须使用 JSON。", action="重新加载应用后再试。")
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._api_error(400, "invalid_request", "保存内容大小无效。", action="缩短条目内容或重新加载应用后再试。")
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeError):
            self._api_error(400, "invalid_request", "保存内容不是有效 JSON。", action="重新加载应用后再试。")
            return
        try:
            self._json(200, self.server.work_store.save_inbox(payload))
        except InboxError as exc:
            status = 409 if isinstance(exc, StaleSourceError) else (500 if exc.code in {"read_failed", "write_failed"} else 400 if exc.code == "invalid_request" else 422)
            self._api_error(status, exc.code, str(exc), field=exc.field, line=exc.line, action=exc.action)


def create_server(repo_root: Path, port: int = 8765) -> InboxServer:
    root = repo_root.resolve()
    inbox = root / "personal" / "inbox" / "inbox.md"
    if not inbox.is_file():
        raise InboxError("找不到 personal/inbox/inbox.md。", code="read_failed", action="使用 --repo-root 指向包含 Personal Inbox 的 workspace。")
    return InboxServer(("127.0.0.1", port), InboxHandler, root)


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise InboxError("无法找到 workspace 根目录。", code="read_failed", action="使用 --repo-root 明确指定 workspace。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=find_repo_root(Path(__file__).resolve()))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.port <= 65535:
        print("error: --port 必须在 0–65535 之间。", file=sys.stderr)
        return 2
    try:
        server = create_server(args.repo_root, args.port)
    except (InboxError, OSError) as exc:
        if isinstance(exc, OSError) and (getattr(exc, "winerror", None) == 10048 or exc.errno in {48, 98, 10048}):
            print(f"error: 127.0.0.1:{args.port} 已被占用；请使用 --port 选择其他端口。", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Personal Inbox: {url}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
