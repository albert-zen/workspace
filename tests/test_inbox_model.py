from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))

from workflow.inbox_model import (  # noqa: E402
    InboxError,
    StaleSourceError,
    atomic_write,
    document_from_payload,
    parse_inbox_text,
    revision_for,
    serialize_inbox,
)


CANONICAL = """# Personal Inbox

## Needs you

- [ ] Decide scope — Next: choose one. · [Issue](https://example.com/issues/1) · [Work](../../agent-data/projects/demo/work/active/scope.md)

## In progress

_暂无事项。_

## Waiting

_暂无事项。_

## Done

- [x] Shipped — Result is available.
"""


class InboxModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = self.root / "personal" / "inbox" / "inbox.md"
        self.work = self.root / "agent-data" / "projects" / "demo" / "work" / "active" / "scope.md"
        self.inbox.parent.mkdir(parents=True)
        self.work.parent.mkdir(parents=True)
        self.work.write_text("# Scope\n", encoding="utf-8")
        self.allowed = {"agent-data/projects/demo/work/active/scope.md"}

    def test_canonical_parse_serialize_is_byte_stable(self) -> None:
        document = parse_inbox_text(CANONICAL, self.inbox, self.root, self.allowed)
        self.assertEqual(serialize_inbox(document, self.inbox, self.root), CANONICAL)
        item = document.sections["Needs you"][0]
        self.assertEqual(item.issue_url, "https://example.com/issues/1")
        self.assertEqual(item.work_path, "agent-data/projects/demo/work/active/scope.md")

    def test_associations_are_optional_but_ordered_and_safe(self) -> None:
        for replacement in (
            " · [Issue](https://example.com/issues/1)",
            " · [Work](../../agent-data/projects/demo/work/active/scope.md)",
            "",
        ):
            parse_inbox_text(CANONICAL.replace(" · [Issue](https://example.com/issues/1) · [Work](../../agent-data/projects/demo/work/active/scope.md)", replacement), self.inbox, self.root, self.allowed)
        bad = (
            CANONICAL.replace("https://example.com/issues/1", "http://example.com/1"),
            CANONICAL.replace("https://example.com/issues/1", "https://user:secret@example.com/1"),
            CANONICAL.replace(" · [Issue](https://example.com/issues/1) · [Work](../../agent-data/projects/demo/work/active/scope.md)", " · [Work](../../agent-data/projects/demo/work/active/scope.md) · [Issue](https://example.com/issues/1)"),
            CANONICAL.replace("../../agent-data/projects/demo/work/active/scope.md", "../../outside.md"),
        )
        for text in bad:
            with self.subTest(text=text), self.assertRaises(InboxError):
                parse_inbox_text(text, self.inbox, self.root, self.allowed)

    def test_missing_but_well_formed_work_association_remains_readable(self) -> None:
        self.work.unlink()
        document = parse_inbox_text(CANONICAL, self.inbox, self.root, set())
        self.assertEqual(document.sections["Needs you"][0].work_path, "agent-data/projects/demo/work/active/scope.md")

    def test_payload_create_edit_move_and_last_item_empty(self) -> None:
        payload = {
            "sections": [
                {"status": "Needs you", "items": []},
                {"status": "In progress", "items": [{"title": "Changed", "summary": "Moving.", "issueUrl": "", "workPath": ""}]},
                {"status": "Waiting", "items": []},
                {"status": "Done", "items": []},
            ]
        }
        document = document_from_payload(payload, self.inbox, self.root, self.allowed)
        text = serialize_inbox(document, self.inbox, self.root)
        self.assertIn("## Needs you\n\n_暂无事项。_", text)
        self.assertIn("- [ ] Changed — Moving.", text)

    def test_duplicate_titles_and_unknown_payload_fields_are_rejected(self) -> None:
        duplicate = CANONICAL.replace("- [x] Shipped — Result is available.", "- [x] decide   SCOPE — Result is available.")
        with self.assertRaises(InboxError):
            parse_inbox_text(duplicate, self.inbox, self.root, self.allowed)
        with self.assertRaises(InboxError):
            document_from_payload({"sections": [], "extra": True}, self.inbox, self.root, self.allowed)

    def test_atomic_write_success_and_stale_windows(self) -> None:
        self.inbox.write_text(CANONICAL, encoding="utf-8")
        before = self.inbox.read_bytes()
        expected = revision_for(before)
        updated = CANONICAL.replace("Result is available.", "Result retained.")
        result = atomic_write(self.inbox, updated.encode(), expected)
        self.assertEqual(self.inbox.read_text(encoding="utf-8"), updated)
        self.assertEqual(result, revision_for(updated.encode()))

        with self.assertRaises(StaleSourceError):
            atomic_write(self.inbox, before, expected)
        self.assertEqual(self.inbox.read_text(encoding="utf-8"), updated)

        current = self.inbox.read_bytes()
        def mutate() -> None:
            self.inbox.write_text(CANONICAL.replace("Shipped", "External"), encoding="utf-8")
        with self.assertRaises(StaleSourceError):
            atomic_write(self.inbox, updated.encode(), revision_for(current), before_replace=mutate)
        self.assertIn("External", self.inbox.read_text(encoding="utf-8"))
        self.assertEqual(list(self.inbox.parent.glob(".inbox-*.tmp")), [])

    def test_replace_failure_preserves_original_and_cleans_temp(self) -> None:
        self.inbox.write_text(CANONICAL, encoding="utf-8")
        original = self.inbox.read_bytes()
        with mock.patch.object(os, "replace", side_effect=PermissionError("blocked")):
            with self.assertRaises(InboxError) as raised:
                atomic_write(self.inbox, CANONICAL.replace("Shipped", "Changed").encode(), revision_for(original))
        self.assertEqual(raised.exception.code, "write_failed")
        self.assertEqual(self.inbox.read_bytes(), original)
        self.assertEqual(list(self.inbox.parent.glob(".inbox-*.tmp")), [])

    def test_fsync_failure_preserves_original_and_cleans_temp(self) -> None:
        self.inbox.write_text(CANONICAL, encoding="utf-8")
        original = self.inbox.read_bytes()
        with mock.patch.object(os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(InboxError) as raised:
                atomic_write(self.inbox, CANONICAL.replace("Shipped", "Changed").encode(), revision_for(original))
        self.assertEqual(raised.exception.code, "write_failed")
        self.assertEqual(self.inbox.read_bytes(), original)
        self.assertEqual(list(self.inbox.parent.glob(".inbox-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
