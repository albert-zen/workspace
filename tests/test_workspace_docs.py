"""Deterministic checks for workspace rule docs, links, reachability, and tooling contracts."""
from __future__ import annotations

import configparser
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent

INLINE_CODE_OR_LINK_RE = re.compile(
    r"`+[^`]*`+|(?<!!)\[(?:`[^`]*`|[^\]`])+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)"
)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

# Private/runtime paths that intentionally live outside the tracked rule set.
RUNTIME_ONLY_PREFIXES = (
    "personal/inbox/inbox.md",
    "personal/captures/",
    "personal/syntheses/",
    "personal/concepts/",
    "agent-data/",
    "memory/",
    "dev/",
)


def strip_fenced_blocks(text: str) -> list[str]:
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
                continue
            if marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
                continue
        if fence is None:
            lines.append(line)
    return lines


def github_slug(heading: str) -> str:
    text = re.sub(r"[`*~]", "", heading).strip().lower()
    # Keep word chars (including CJK), spaces, and hyphens; drop punctuation.
    text = re.sub(r"[^\w\u4e00-\u9fff -]", "", text)
    # GitHub replaces each space with a hyphen without collapsing adjacent spaces.
    return text.replace(" ", "-")


def heading_anchors(path: Path) -> set[str]:
    counts: dict[str, int] = {}
    anchors: set[str] = set()
    for line in strip_fenced_blocks(path.read_text(encoding="utf-8")):
        match = HEADING_RE.match(line)
        if not match:
            continue
        base = github_slug(match.group(2))
        if not base:
            continue
        seen = counts.get(base, 0)
        counts[base] = seen + 1
        anchors.add(base if seen == 0 else f"{base}-{seen}")
    return anchors


def extract_relative_links_from_text(text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for line in strip_fenced_blocks(text):
        for match in INLINE_CODE_OR_LINK_RE.finditer(line):
            raw = match.group(1)
            if not raw:
                continue
            parsed = urlsplit(raw)
            if parsed.scheme in {"http", "https", "mailto", "tel"} or raw.startswith("chatgpt-conversation:"):
                continue
            target_path = unquote(parsed.path)
            fragment = unquote(parsed.fragment)
            results.append((target_path, fragment))
    return results


def extract_relative_links(path: Path) -> list[tuple[str, str]]:
    return extract_relative_links_from_text(path.read_text(encoding="utf-8"))


def submodule_paths() -> list[Path]:
    gitmodules = ROOT / ".gitmodules"
    if not gitmodules.is_file():
        return []
    parser = configparser.ConfigParser()
    parser.read(gitmodules, encoding="utf-8")
    paths: list[Path] = []
    for section in parser.sections():
        rel = parser.get(section, "path", fallback="").strip()
        if rel:
            paths.append((ROOT / rel).resolve())
    return paths


def tracked_rule_docs() -> list[Path]:
    docs: list[Path] = []
    for name in (
        "AGENTS.md",
        "README.md",
        "design.md",
        "garden.md",
        "workspace.md",
        "personal/personal.md",
    ):
        p = ROOT / name
        if p.is_file():
            docs.append(p)
    for folder in (
        ROOT / "practices",
        ROOT / "adapters",
        ROOT / "references",
        ROOT / "workflow",
    ):
        if not folder.exists():
            continue
        submodules = submodule_paths()
        for p in sorted(folder.rglob("*.md")):
            resolved = p.resolve()
            if any(resolved == sm or sm in resolved.parents for sm in submodules):
                continue
            if "tests" in p.relative_to(ROOT).parts:
                continue
            docs.append(p)
    return docs


class WorkspaceDocsTests(unittest.TestCase):
    def test_no_broken_relative_links_or_anchors(self) -> None:
        submodules = submodule_paths()
        errors: list[str] = []
        anchor_cache: dict[Path, set[str]] = {}

        for doc in tracked_rule_docs():
            for rel_target, fragment in extract_relative_links(doc):
                if not rel_target:
                    target = doc.resolve()
                else:
                    target = (doc.parent / rel_target).resolve()

                try:
                    repo_rel = target.relative_to(ROOT).as_posix()
                except ValueError:
                    errors.append(f"{doc.relative_to(ROOT)} -> {rel_target} escapes workspace root")
                    continue

                if any(repo_rel.startswith(prefix) for prefix in RUNTIME_ONLY_PREFIXES):
                    continue

                # If a submodule isn't checked out locally (empty directory), skip internal file checks.
                inside_uninitialized_submodule = False
                for sm in submodules:
                    if target != sm and sm in target.parents:
                        if not sm.exists() or not any(sm.iterdir()):
                            inside_uninitialized_submodule = True
                            break
                if inside_uninitialized_submodule:
                    continue

                if not target.exists():
                    errors.append(f"{doc.relative_to(ROOT)} -> broken link '{rel_target}'")
                    continue

                if fragment and target.is_file() and target.suffix == ".md":
                    anchors = anchor_cache.setdefault(target, heading_anchors(target))
                    if fragment not in anchors:
                        errors.append(
                            f"{doc.relative_to(ROOT)} -> missing anchor '#{fragment}' in {repo_rel}"
                        )

        self.assertEqual(errors, [], "\n".join(errors))

    def test_all_top_level_practice_modules_reachable_from_agents(self) -> None:
        agents = (ROOT / "AGENTS.md").resolve()
        self.assertTrue(agents.is_file(), "AGENTS.md must exist")

        visited: set[Path] = set()
        queue: list[Path] = [agents]
        while queue:
            current = queue.pop()
            if current in visited or not current.is_file():
                continue
            visited.add(current)
            for rel_target, _ in extract_relative_links(current):
                if not rel_target:
                    continue
                nxt = (current.parent / rel_target).resolve()
                if nxt.is_file() and nxt.suffix == ".md" and nxt not in visited:
                    try:
                        nxt.relative_to(ROOT)
                    except ValueError:
                        continue
                    queue.append(nxt)

        expected = sorted((ROOT / "practices").glob("*.md"))
        self.assertTrue(expected, "Expected at least one top-level module in practices/")
        unreachable = [
            p.relative_to(ROOT).as_posix() for p in expected if p.resolve() not in visited
        ]
        self.assertEqual(unreachable, [], f"Orphan practice modules not reachable from AGENTS.md: {unreachable}")

    def test_workflow_cli_doc_links_exist(self) -> None:
        source = (ROOT / "workflow/work_interface.py").read_text(encoding="utf-8")
        refs = re.findall(r"document_link\(\s*store\s*,\s*['\"]([^'\"]+)['\"]\s*\)", source)
        self.assertTrue(refs, "Expected document_link references in workflow/work_interface.py")
        missing = [ref for ref in refs if not (ROOT / ref).is_file()]
        self.assertEqual(missing, [], f"Missing docs referenced by work_interface.py: {missing}")

    def test_gitmodules_paths_exist(self) -> None:
        for sm in submodule_paths():
            self.assertTrue(sm.exists(), f"Submodule path missing: {sm.relative_to(ROOT)}")

    def test_inline_code_link_labels_are_checked_while_code_examples_are_ignored(self) -> None:
        sample = (
            "Normal [`practices/collaboration.md`](practices/collaboration.md#常用入口) "
            "and literal `[example](missing.md)` inside code."
        )
        self.assertEqual(
            extract_relative_links_from_text(sample),
            [("practices/collaboration.md", "常用入口")],
        )

    def test_github_slug_preserves_double_hyphen_after_removed_punctuation(self) -> None:
        self.assertEqual(
            github_slug("工程环境与自动化开发（Gardening & 验证）"),
            "工程环境与自动化开发gardening--验证",
        )


if __name__ == "__main__":
    unittest.main()
