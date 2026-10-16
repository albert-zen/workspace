from __future__ import annotations

import http.client
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest

from _support import ASSETS, SCRIPTS
sys.path.insert(0, str(SCRIPTS))

from inbox_app import create_server  # noqa: E402


INBOX = """# Personal Inbox

## Needs you

_暂无事项。_

## In progress

_暂无事项。_

## Waiting

_暂无事项。_

## Done

- [x] Existing — Kept.
"""


class InboxAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        inbox = self.root / "personal/inbox/inbox.md"
        inbox.parent.mkdir(parents=True)
        inbox.write_text(INBOX, encoding="utf-8")
        work = self.root / "agent-data/projects/demo/work/active/work.md"
        work.parent.mkdir(parents=True)
        work.write_text("# Work\n\n## Outcome\n\nReady.\n", encoding="utf-8")
        self.server = create_server(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def request(self, method: str, path: str, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        request_headers = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        encoded = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=encoded, headers=request_headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, dict(response.getheaders()), data

    def test_static_and_get_apis_are_allowlisted(self) -> None:
        for path, content in (("/", b"Personal Inbox"), ("/assets/inbox.css", b":root"), ("/assets/inbox.js", b"fetch"), ("/api/inbox", b"revision"), ("/api/work-documents", b"work.md"), ("/api/work-document?path=agent-data%2Fprojects%2Fdemo%2Fwork%2Factive%2Fwork.md", b"Outcome")):
            status, _, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(content, body)
        self.assertEqual(self.request("GET", "/assets/../scripts/inbox_app.py")[0], 404)
        self.assertEqual(self.request("GET", "/api/issue")[0], 404)
        self.assertEqual(self.request("GET", "/api/inbox", headers={"Host": "attacker.example"})[0], 403)

    def test_inbox_exposes_actual_absolute_source_for_copy(self):
        status, _, body = self.request("GET", "/api/inbox")
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["absoluteSourcePath"], str((self.root / "personal/inbox/inbox.md").resolve()))
        self.assertEqual(result["sourcePath"], "personal/inbox/inbox.md")

    def update_payload(self, snapshot):
        return {"expectedRevision": snapshot["revision"], "sections": [
            {"status": section["status"], "items": [
                {key: item[key] for key in ("title", "summary", "issueUrl", "workPath", "itemId") if key in item}
                for item in section["items"]]} for section in snapshot["sections"]]}

    def test_rename_and_personal_category_keep_registered_identity(self):
        current = json.loads(self.request("GET", "/api/inbox")[2])
        item = current["sections"][-1]["items"][0]
        original_id = item["itemId"]
        self.assertTrue(original_id)
        self.assertEqual(item["lifecycleStatus"], "unverified")
        update = self.update_payload(current)
        changed = update["sections"][-1]["items"].pop()
        changed["title"] = "Renamed existing matter"
        update["sections"][1]["items"].append(changed)
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        status, _, body = self.request("PUT", "/api/inbox", update, headers)
        self.assertEqual(status, 200, body)
        renamed = json.loads(body)["sections"][1]["items"][0]
        self.assertEqual(renamed["itemId"], original_id)
        self.assertEqual(renamed["entryKey"], original_id)
        self.assertEqual(renamed["lifecycleStatus"], "unverified", "moving a personal category must not start or complete work")
        reloaded = json.loads(self.request("GET", "/api/inbox")[2])["sections"][1]["items"][0]
        self.assertEqual(reloaded["itemId"], original_id)
        command = shlex.split(reloaded["handoffCommand"])
        self.assertTrue(Path(command[0]).is_absolute())
        self.assertEqual(command[-2:], ["resume", reloaded["semanticName"]])
        self.assertEqual(command[command.index("--root") + 1], str(self.root.resolve()))

    def test_pin_is_persistent_and_manual_order_survives_cli_updates(self):
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        current = json.loads(self.request("GET", "/api/inbox")[2])
        payload = self.update_payload(current)
        payload['sections'][1]['items'] = [
            dict(title=title, summary='Keep this order.', issueUrl='', workPath='', pinned=pin)
            for title, pin in [('demo/Z first', False), ('demo/A second', False), ('demo/Pinned', True)]]
        status, _, body = self.request('PUT', '/api/inbox', payload, headers)
        self.assertEqual(status, 200, body)
        snapshot = json.loads(body)
        expected = ['demo/Pinned', 'demo/Z first', 'demo/A second']
        self.assertEqual([i['title'] for i in snapshot['sections'][1]['items']], expected)
        self.assertIn('<!-- pinned -->', (self.root / 'personal/inbox/inbox.md').read_text())
        # A legacy browser edits without knowing the optional pin field.
        status, _, body = self.request('PUT', '/api/inbox', self.update_payload(snapshot), headers)
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)['sections'][1]['items'][0]['pinned'])
        command = shlex.split(snapshot['sections'][1]['items'][0]['handoffCommand'])[:-2]
        result = subprocess.run(command + ['arrange', 'demo/Z first', '--before', 'demo/Pinned'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        reloaded = json.loads(self.request('GET', '/api/inbox')[2])
        self.assertEqual([i['title'] for i in reloaded['sections'][1]['items']], expected)
        # Explicit unpin and reorder is stored, including after another read.
        payload = self.update_payload(reloaded)
        first = payload['sections'][1]['items'].pop(0)
        first['pinned'] = False
        payload['sections'][1]['items'].append(first)
        status, _, body = self.request('PUT', '/api/inbox', payload, headers)
        self.assertEqual(status, 200, body)
        latest = json.loads(self.request('GET', '/api/inbox')[2])['sections'][1]['items']
        self.assertEqual([i['title'] for i in latest], ['demo/Z first', 'demo/A second', 'demo/Pinned'])
        self.assertFalse(any(i['pinned'] for i in latest))

    def test_pin_requires_boolean(self):
        snapshot = json.loads(self.request('GET', '/api/inbox')[2])
        payload = self.update_payload(snapshot)
        payload['sections'][-1]['items'][0]['pinned'] = 'true'
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        self.assertEqual(self.request('PUT', '/api/inbox', payload, headers)[0], 400)

    def test_handoff_command_resumes_exact_semantic_name_with_shell_characters(self):
        current = json.loads(self.request("GET", "/api/inbox")[2])
        update = self.update_payload(current)
        title = "demo/Bob's $(printf INJECTED) `echo nope`"
        update["sections"][0]["items"].append({"title": title, "summary": "Read only this matter.", "issueUrl": "", "workPath": ""})
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        status, _, body = self.request("PUT", "/api/inbox", update, headers)
        self.assertEqual(status, 200, body)
        item = json.loads(body)["sections"][0]["items"][0]
        self.assertEqual(shlex.split(item["handoffCommand"])[-1], item["semanticName"])
        result = subprocess.run(["/bin/sh", "-c", item["handoffCommand"]], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(title, result.stdout)

    def test_cli_change_rejects_stale_browser_save_without_losing_either_item(self):
        current = json.loads(self.request("GET", "/api/inbox")[2])
        original = current["sections"][-1]["items"][0]
        command = shlex.split(original["handoffCommand"])
        result = subprocess.run(command[:-2] + ["add", "demo/Added from CLI", "--summary", "Added from CLI."], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        stale = self.update_payload(current)
        stale["sections"][-1]["items"][0]["summary"] = "Unsaved browser draft."
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        status, _, body = self.request("PUT", "/api/inbox", stale, headers)
        self.assertEqual(status, 409, body)
        latest = json.loads(self.request("GET", "/api/inbox")[2])
        items = [item for section in latest["sections"] for item in section["items"]]
        self.assertEqual(len(items), 2)
        self.assertEqual(next(item for item in items if item["itemId"] == original["itemId"])["summary"], "Kept.")
        self.assertTrue(any(item["semanticName"] == "demo/Added from CLI" for item in items))

    def test_work_api_reads_sections_fresh_without_changing_source(self):
        work = self.root / "agent-data/projects/demo/work/active/work.md"
        original = "# Work\n\n## 事项说明\nBackground\n\n## 当前推进\n待讨论。\n"
        work.write_text(original, encoding="utf-8")
        endpoint = "/api/work-document?path=agent-data%2Fprojects%2Fdemo%2Fwork%2Factive%2Fwork.md"
        status, _, body = self.request("GET", endpoint)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["sections"]["description"]["markdown"], "Background")
        self.assertEqual(result["absolutePath"], str(work.resolve()))
        self.assertEqual(work.read_text(encoding="utf-8"), original)
        work.write_text(original.replace("Background", "Updated"), encoding="utf-8")
        self.assertEqual(json.loads(self.request("GET", endpoint)[2])["sections"]["description"]["markdown"], "Updated")

    def test_served_assets_bundle_shared_components_before_host_code(self) -> None:
        for extension in ("js", "css"):
            status, headers, body = self.request("GET", f"/assets/inbox.{extension}")
            self.assertEqual(status, 200)
            shared = (ASSETS.parent.parent / "shared" / f"components.{extension}").read_bytes()
            host = (ASSETS / f"inbox.{extension}").read_bytes()
            self.assertEqual(body, shared + b"\n" + host)
        self.assertEqual(self.request("GET", "/assets/components.js")[0], 404)

    def test_put_requires_json_safe_origin_known_shape_and_revision(self) -> None:
        _, _, current = self.request("GET", "/api/inbox")
        payload = json.loads(current)
        sections = [{"status": section["status"], "items": [{key: item[key] for key in ("title", "summary", "issueUrl", "workPath")} for item in section["items"]]} for section in payload["sections"]]
        # Change content so a repeated write really carries a stale revision.
        sections[-1]["items"][0]["summary"] = "Updated by the test."
        update = {"expectedRevision": payload["revision"], "sections": sections}
        self.assertEqual(self.request("PUT", "/api/inbox", update)[0], 403)
        origin = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        self.assertEqual(self.request("PUT", "/api/inbox", b"{}", {**origin, "Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request("PUT", "/api/inbox", {**update, "extra": True}, origin)[0], 400)
        status, _, changed = self.request("PUT", "/api/inbox", update, origin)
        self.assertEqual(status, 200, changed)
        self.assertEqual(self.request("PUT", "/api/inbox", update, origin)[0], 409)

    def test_errors_have_uniform_shape_and_body_limit(self) -> None:
        status, _, body = self.request("GET", "/api/work-document?path=..%2Fsecret.md")
        self.assertEqual(status, 404)
        error = json.loads(body)["error"]
        self.assertEqual(set(error), {"code", "message", "field", "line", "action"})
        origin = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json", "Content-Length": str(1_048_577)}
        self.assertEqual(self.request("PUT", "/api/inbox", b"x", origin)[0], 400)

    def test_occupied_port_is_rejected(self) -> None:
        with self.assertRaises(OSError):
            create_server(self.root, self.port)

    def test_personal_inbox_contains_no_checked_in_html(self) -> None:
        tracked_personal = Path(__file__).resolve().parents[5] / "personal" / "inbox"
        self.assertTrue((tracked_personal / "schema.md").is_file())
        self.assertFalse(list(tracked_personal.glob("*.html")))  # Personal data may be absent in a clean checkout.

    def test_ui_uses_the_quiet_ledger_structure(self) -> None:
        markup = (ASSETS / "index.html").read_text(encoding="utf-8")
        styles = (ASSETS / "inbox.css").read_text(encoding="utf-8")

        self.assertIn('class="app-bar"', markup)
        self.assertIn('class="app-title"', markup)
        self.assertNotIn('class="eyebrow"', markup)
        self.assertNotIn("Georgia", styles)
        self.assertNotIn("border:1px dashed", styles)
        # Shell height is a design token; do not pin a particular visual density.
        self.assertIn("--app-bar-height:", styles)
        self.assertIn("grid-template-rows:var(--app-bar-height)", styles)
        self.assertIn(".entry-card[aria-current=\"true\"]", styles)
        self.assertIn("max-width:68ch", styles)

    def test_ui_contract_covers_properties_feedback_and_adaptive_work_navigation(self) -> None:
        script = (ASSETS / "inbox.js").read_text(encoding="utf-8")

        self.assertIn('"事项说明"', script)
        self.assertIn('"local-feedback"', script)
        self.assertIn('"form-feedback"', script)
        self.assertIn('"work-evidence"', script)
        self.assertIn('matchMedia("(min-width: 721px)")', script)
        self.assertIn('history.replaceState', script)

    def test_inline_code_wraps_without_changing_fenced_code_scrolling(self) -> None:
        styles = (ASSETS / "inbox.css").read_text(encoding="utf-8")

        self.assertIn("overflow-wrap:anywhere", styles)
        self.assertIn(".work-document pre code", styles)
        self.assertIn("overflow-wrap:normal", styles)

    def test_work_chapters_preserve_and_restore_the_detail_route(self) -> None:
        script = (ASSETS / "inbox.js").read_text(encoding="utf-8")

        self.assertNotIn('history.replaceState({route:state.route,key:state.selected},"",target)', script)
        self.assertIn("function routeFromLocation()", script)
        self.assertIn("heading.focus({preventScroll:true})", script)
        self.assertIn("load({preserveRoute:true})", script)

    def test_load_reconciles_route_against_the_fresh_inbox_snapshot(self) -> None:
        script = (ASSETS / "inbox.js").read_text(encoding="utf-8")

        self.assertIn("function itemByKeyIn(sections,key)", script)
        self.assertIn("const intent={route:state.route,key:state.selected,draft:state.draft}", script)
        self.assertIn("itemByKeyIn(inbox.sections,intent.key)", script)
        self.assertIn("Object.assign(state,next)", script)

    def test_invalid_detail_route_is_canonicalized_without_pushing_history(self) -> None:
        script = (ASSETS / "inbox.js").read_text(encoding="utf-8")

        self.assertIn('history.replaceState({route:"list",key:null},"","#inbox")', script)
        self.assertIn('if(desktop()&&next.route==="list"&&!next.selected)', script)
        self.assertNotIn('history.pushState({route:"detail"', script)


if __name__ == "__main__":
    unittest.main()
