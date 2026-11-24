"""Tests for SQLite Inbox & workflow storage, legacy migration, and Markdown export."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from workflow.work_store import WorkStore
from workflow.inbox_model import StaleSourceError


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


class WorkDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(WORKSPACE_ROOT / "bin/work"), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_fresh_workspace_uses_sqlite_only_and_never_creates_personal_inbox(self) -> None:
        store = WorkStore(self.root)
        store.add("demo/Alpha", summary="First task")
        store.start("demo/Beta", summary="Second task")

        self.assertTrue((self.root / "agent-data/workflow/work.db").is_file())
        self.assertFalse((self.root / "personal/inbox/inbox.md").exists())
        self.assertFalse((self.root / "agent-data/workflow/state.json").exists())

        snap = store.snapshot()
        self.assertEqual(snap["sourcePath"], "agent-data/workflow/work.db")
        in_progress = next(s["items"] for s in snap["sections"] if s["status"] == "In progress")
        waiting = next(s["items"] for s in snap["sections"] if s["status"] == "Waiting")
        self.assertEqual([i["title"] for i in in_progress], ["demo/Beta"])
        self.assertEqual([i["title"] for i in waiting], ["demo/Alpha"])

    def test_lossless_one_time_migration_from_legacy_inbox_md_and_state_json(self) -> None:
        item_id = "0123456789abcdef0123456789abcdef"
        work_rel = "agent-data/projects/demo/work/active/Legacy.md"
        work_file = self.root / work_rel
        work_file.parent.mkdir(parents=True, exist_ok=True)
        work_file.write_text("# Legacy\n\n## 事项说明\n\nKeep me.\n\n## 当前推进\n\n进行中。\n", encoding="utf-8")

        legacy_state_path = self.root / "agent-data/workflow/state.json"
        legacy_state_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_state = {
            "version": 1,
            "items": {
                item_id: {
                    "id": item_id,
                    "name": "demo/Legacy",
                    "title": "demo/Legacy",
                    "summary": "Migrated summary",
                    "aliases": ["demo/OldName"],
                    "workPath": work_rel,
                    "issueUrl": "https://github.com/example/repo/issues/7",
                    "lifecycleStatus": "active",
                    "inbox": True,
                    "attention": "Needs you",
                    "pinned": True,
                    "owner": "chief",
                    "arrangement": "High priority",
                    "eventCount": 3,
                    "planVersion": 2,
                    "run": 2,
                    "createdAt": "2026-10-01T00:00:00+00:00",
                    "resources": [{"kind": "pr", "owned": False, "url": "https://github.com/example/repo/pull/9"}],
                    "closeoutPending": [],
                    "lastProgress": "进行中。",
                }
            },
        }
        legacy_state_bytes = (json.dumps(legacy_state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        legacy_state_path.write_bytes(legacy_state_bytes)

        legacy_inbox_path = self.root / "personal/inbox/inbox.md"
        legacy_inbox_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_inbox_text = (
            "# Personal Inbox\n\n"
            "## Needs you\n\n"
            f"- [ ] demo/Legacy — Migrated summary · [Issue](https://github.com/example/repo/issues/7)"
            f" · [Work](../../{work_rel}) <!-- pinned --> <!-- work:{item_id} -->\n"
            "- [ ] demo/InboxOnly — Only in markdown\n\n"
            "## In progress\n\n_暂无事项。_\n\n"
            "## Waiting\n\n_暂无事项。_\n\n"
            "## Done\n\n_暂无事项。_\n"
        )
        legacy_inbox_bytes = legacy_inbox_text.encode("utf-8")
        legacy_inbox_path.write_bytes(legacy_inbox_bytes)

        # Simulate interruption after creating the database, before committing migration.
        legacy_state_path.with_name("work.db").touch()
        store = WorkStore(self.root)
        snap = store.snapshot()
        needs_you = next(s["items"] for s in snap["sections"] if s["status"] == "Needs you")
        self.assertEqual([i["title"] for i in needs_you], ["demo/Legacy", "demo/InboxOnly"])
        self.assertTrue(needs_you[0]["pinned"])
        self.assertEqual(needs_you[0]["itemId"], item_id)

        resumed = store.resume("demo/OldName")
        self.assertEqual(resumed["id"], item_id)
        self.assertEqual(resumed["owner"], "chief")
        self.assertEqual(resumed["arrangement"], "High priority")
        self.assertEqual(resumed["eventCount"], 3)
        self.assertEqual(resumed["planVersion"], 2)
        self.assertEqual(resumed["run"], 2)
        self.assertEqual(resumed["resources"][0]["url"], "https://github.com/example/repo/pull/9")

        # Subsequent writes go strictly to SQLite and leave legacy files untouched backups.
        store.add("demo/AfterMigration", summary="Written to SQLite only")
        self.assertEqual(legacy_inbox_path.read_bytes(), legacy_inbox_bytes)
        self.assertEqual(legacy_state_path.read_bytes(), legacy_state_bytes)

        conn = sqlite3.connect(str(self.root / "agent-data/workflow/work.db"))
        try:
            migrated = conn.execute("SELECT value FROM meta WHERE key = 'migrated_from_legacy_at'").fetchone()
            self.assertIsNotNone(migrated)
        finally:
            conn.close()

    def test_inbox_export_command_outputs_markdown_snapshot(self) -> None:
        store = WorkStore(self.root)
        work = store.start("demo/Exported", summary="Snapshot check")

        stdout_run = self.run_cli("inbox", "--export")
        self.assertEqual(stdout_run.returncode, 0, stdout_run.stderr)
        self.assertIn("# Personal Inbox", stdout_run.stdout)
        self.assertIn("demo/Exported — Snapshot check", stdout_run.stdout)

        out_file = self.root / "agent-data/exported-inbox.md"
        file_run = self.run_cli("inbox", "--export", "--output", str(out_file))
        self.assertEqual(file_run.returncode, 0, file_run.stderr)
        self.assertTrue(out_file.is_file())
        exported = out_file.read_text(encoding="utf-8")
        self.assertIn("demo/Exported — Snapshot check", exported)
        self.assertIn("[Work](" + str(Path(work["workPath"]).relative_to("agent-data")) + ")", exported)
        for target in (store.db_path, store.state_path, store.inbox):
            self.assertNotEqual(self.run_cli("inbox", "--export", "--output", str(target)).returncode, 0)
        self.assertEqual(store.resume("demo/Exported")["id"], work["id"])

    def test_start_revision_can_be_used_for_immediate_update(self) -> None:
        store = WorkStore(self.root)
        created = store.start("demo/Revision", summary="Initial")
        updated = store.update("demo/Revision", summary="Changed", expected_revision=created["revision"])
        self.assertEqual(updated["revision"], store.resume("demo/Revision")["revision"])

    def test_optimistic_revision_rejects_stale_inbox_save(self) -> None:
        store = WorkStore(self.root)
        store.add("demo/One", summary="Initial")
        snap = store.snapshot()
        store.add("demo/Two", summary="Concurrent CLI add")

        payload = {
            "expectedRevision": snap["revision"],
            "sections": [
                {
                    "status": sec["status"],
                    "items": [
                        {k: item[k] for k in ("title", "summary", "issueUrl", "workPath", "itemId", "pinned") if k in item}
                        for item in sec["items"]
                    ],
                }
                for sec in snap["sections"]
            ],
        }
        with self.assertRaises(StaleSourceError):
            store.save_inbox(payload)


if __name__ == "__main__":
    unittest.main()
