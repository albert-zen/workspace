from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow.work_documents import WorkDocumentError, WorkDocuments, parse_work_markdown  # noqa: E402


class WorkDocumentsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.active = self.root / "agent-data/projects/demo/work/active/alpha.md"
        self.archive = self.root / "agent-data/projects/demo/work/archive/old.md"
        self.active.parent.mkdir(parents=True)
        self.archive.parent.mkdir(parents=True)
        self.active.write_text("# Alpha\n\n## Outcome\n\n- one\n  - nested\n\n```py\nprint('ok')\n```\n", encoding="utf-8")
        self.archive.write_text("# Old\n", encoding="utf-8")

    def test_enumerates_only_active_archive_markdown_with_metadata(self) -> None:
        (self.root / "agent-data/projects/demo/work/active/no.txt").write_text("x", encoding="utf-8")
        docs = WorkDocuments(self.root)
        listed = docs.list_documents()
        self.assertEqual([item["title"] for item in listed], ["Alpha", "Old"])
        self.assertEqual({item["state"] for item in listed}, {"active", "archive"})
        detail = docs.read("agent-data/projects/demo/work/active/alpha.md")
        self.assertEqual(detail["project"], "demo")
        self.assertEqual(detail["absolutePath"], str(self.active.resolve()))
        self.assertTrue(any(block["type"] == "code" for block in detail["blocks"]))

    def test_issue_sections_are_explicit_and_keep_nested_content(self):
        source = "# Work\n\nOld intro\n\n## 事项说明\n\nBackground\n\n### Scope\n\n```\n## Not a boundary\n```\n\n## 当前推进\n\n待讨论，尚未授权实施。\n\n## Evidence\n\nlogs"
        parsed = parse_work_markdown(source)
        self.assertIn("## Not a boundary", parsed["sections"]["description"]["markdown"])
        self.assertNotIn("当前推进", parsed["sections"]["description"]["markdown"])
        self.assertEqual(parsed["sections"]["progress"]["markdown"], "待讨论，尚未授权实施。")
        self.assertNotIn("Background", str(parsed["remainingBlocks"]))
        self.assertIn("logs", str(parsed["remainingBlocks"]))
        self.assertIsNone(parse_work_markdown("# Legacy\n\nIntro")["sections"]["description"])

    def test_fenced_examples_do_not_supply_issue_sections_or_picker_title(self):
        for marker in ("~~~", "````", "   ~~~~", "  ```"):
            with self.subTest(marker=marker):
                close = marker.strip()[0] * (len(marker.strip()) + 1)
                source = (f"{marker}markdown\n# Example title\n## 事项说明\nFake\n"
                          f"{close}\n# Real title\n## 事项说明\nReal description")
                self.active.write_text(source, encoding="utf-8")
                docs = WorkDocuments(self.root)
                parsed = docs.read("agent-data/projects/demo/work/active/alpha.md")
                self.assertEqual(parsed["sections"]["description"]["markdown"], "Real description")
                self.assertEqual(parsed["title"], "Real title")
                self.assertEqual(docs.list_documents()[0]["title"], "Real title")

    def test_fence_closer_must_match_character_length_and_have_no_info(self):
        for false_close in ("```", "~~~~", "```` trailing", "    ````"):
            with self.subTest(false_close=false_close):
                parsed = parse_work_markdown(
                    f"````md\n{false_close}\n## 事项说明\nFake\n   `````  \n"
                    "# Real\n## 事项说明\nReal")
                self.assertEqual(parsed["sections"]["description"]["markdown"], "Real")
                self.assertIn("## 事项说明", parsed["blocks"][0]["text"])

    def test_issue_sections_preserve_original_unique_heading_ids(self):
        parsed = parse_work_markdown(
            "# Work\n### 事项说明\nEarlier\n## 事项说明\nBackground\n## 当前推进\nNext")
        self.assertEqual(parsed["sections"]["description"]["id"], "事项说明-2")
        self.assertEqual(parsed["sections"]["progress"]["id"], "当前推进")

    def test_sections_end_at_next_top_heading_and_do_not_truncate_long_text(self):
        body = "long detail " * 3000
        parsed = parse_work_markdown("# Work\n\n## 事项说明\n" + body + "\n# Separate\nOther")
        self.assertEqual(parsed["sections"]["description"]["markdown"], body.strip())
        self.assertIn("Other", str(parsed["remainingBlocks"]))
        self.assertIsNone(parse_work_markdown("### 事项说明\nNot an issue section")["sections"]["description"])

    def test_rejects_traversal_absolute_unc_missing_and_non_md(self) -> None:
        docs = WorkDocuments(self.root)
        for value in ("../alpha.md", str(self.active), "//server/share/a.md", "agent-data/projects/demo/work/active/missing.md", "agent-data/projects/demo/work/active/no.txt"):
            with self.subTest(value=value), self.assertRaises(WorkDocumentError):
                docs.read(value)

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows developer mode")
    def test_outside_symlink_is_not_enumerated(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("# Outside", encoding="utf-8")
        (self.active.parent / "escape.md").symlink_to(outside)
        self.assertNotIn("escape.md", " ".join(item["path"] for item in WorkDocuments(self.root).list_documents()))

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows developer mode")
    def test_outside_and_non_work_symlinks_cannot_be_read(self) -> None:
        docs = WorkDocuments(self.root)
        docs.list_documents()
        self.active.unlink()
        outside = self.root / "outside.md"
        outside.write_text("# Outside", encoding="utf-8")
        self.active.symlink_to(outside)
        with self.assertRaises(WorkDocumentError):
            docs.read("agent-data/projects/demo/work/active/alpha.md")

    def test_single_read_does_not_enumerate_other_documents(self) -> None:
        class DirectWorkDocuments(WorkDocuments):
            def _mapping(self):
                raise AssertionError("single reads must not enumerate the portfolio")
        docs = DirectWorkDocuments(self.root)
        self.assertEqual(docs.read("agent-data/projects/demo/work/active/alpha.md")["title"], "Alpha")
        self.active.write_text("# Changed", encoding="utf-8")
        self.assertEqual(docs.read("agent-data/projects/demo/work/active/alpha.md")["title"], "Changed")

    def test_listing_ignores_unrelated_artifacts(self) -> None:
        from unittest.mock import patch
        artifact = self.root / "agent-data/projects/demo/artifacts/large"
        artifact.mkdir(parents=True)
        (artifact / "invalid.md").write_bytes(b"\xff")
        with patch("workflow.work_documents.parse_work_markdown", side_effect=AssertionError("listing must not parse bodies")):
            self.assertEqual(len(WorkDocuments(self.root).list_documents()), 2)

    def test_unclosed_frontmatter_title_matches_detail(self) -> None:
        self.active.write_text("---\n# Work title\nBody\n", encoding="utf-8")
        docs = WorkDocuments(self.root)
        self.assertEqual(docs.list_documents()[0]["title"], "Work title")
        self.assertEqual(docs.read("agent-data/projects/demo/work/active/alpha.md")["title"], "Work title")

    def test_safe_ast_keeps_raw_html_as_text_and_classifies_links(self) -> None:
        parsed = parse_work_markdown("""---
secret: no
---
# Heading

Text **bold** *em* `code` [safe](https://example.com/x) [bad](javascript:alert(1)) [fragment](#heading).

<script>alert(1)</script>

> quote
""")
        kinds = {block["type"] for block in parsed["blocks"]}
        self.assertTrue({"frontmatter", "heading", "paragraph", "blockquote"}.issubset(kinds))
        flattened = str(parsed)
        self.assertIn("<script>", flattened)
        self.assertNotIn("<script src", flattened)
        links = [node for block in parsed["blocks"] for node in block.get("inlines", []) if node["type"] == "link"]
        self.assertEqual([node["kind"] for node in links], ["external", "unsafe", "fragment"])


if __name__ == "__main__":
    unittest.main()
