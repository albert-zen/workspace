"""Tests for Inbox Web App reading and live-tracking companion intent.md files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import quote
from urllib.request import Request, urlopen

WORKSPACE_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(WORKSPACE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import inbox_app  # noqa: E402
from workflow.work_store import WorkStore  # noqa: E402


class InboxIntentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = WorkStore(self.root)
        self.store.start("demo/Item", summary="Core goal")
        self.server = inbox_app.create_server(self.root, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get_json(self, path: str) -> dict:
        req = Request(f"http://127.0.0.1:{self.port}{path}")
        with urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read().decode("utf-8"))

    def test_work_document_returns_work_and_project_intents_and_updates_work_version(self) -> None:
        work_path = "agent-data/projects/demo/work/active/Item.md"
        before_snap = self.get_json("/api/snapshot?version=")
        before_doc = self.get_json(f"/api/work-document?path={quote(work_path, safe='')}")
        self.assertEqual(before_doc["intents"], [])

        self.store.intent_add(name="demo/Item", text="第一条事项原话", source="msg-1")
        self.store.intent_add(project="demo", text="项目级长期意图")

        after_snap = self.get_json(f"/api/snapshot?version={quote(before_snap['version'], safe='')}")
        self.assertFalse(after_snap.get("unchanged", False))
        self.assertNotEqual(before_snap["workVersions"][work_path], after_snap["workVersions"][work_path])

        after_doc = self.get_json(f"/api/work-document?path={quote(work_path, safe='')}")
        self.assertEqual([item["scope"] for item in after_doc["intents"]], ["work", "project"])
        self.assertEqual(after_doc["intents"][0]["label"], "事项原始意图")
        self.assertEqual(after_doc["intents"][1]["label"], "项目原始意图")
        rendered_text = json.dumps(after_doc["intents"], ensure_ascii=False)
        self.assertIn("第一条事项原话", rendered_text)
        self.assertIn("项目级长期意图", rendered_text)

    @unittest.skipIf(os.name == "nt", "POSIX symlink test")
    def test_symlink_intent_outside_projects_root_is_ignored(self) -> None:
        secret = self.root / "secret.md"
        secret.write_text("# Secret\n\nDo not expose\n", encoding="utf-8")
        project_intent = self.root / "agent-data/projects/demo/intent.md"
        project_intent.symlink_to(secret)

        work_path = "agent-data/projects/demo/work/active/Item.md"
        doc = self.get_json(f"/api/work-document?path={quote(work_path, safe='')}")
        self.assertEqual(doc["intents"], [])


if __name__ == "__main__":
    unittest.main()
