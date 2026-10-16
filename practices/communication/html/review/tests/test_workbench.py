from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


HTML_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = HTML_ROOT / "scripts" / "workbench.py"
SPEC = importlib.util.spec_from_file_location("workbench", SCRIPT)
workbench = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(workbench)


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True)
    if result.returncode != expect:
        raise AssertionError(f"expected {expect}, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True)
    return result.stdout.strip()


class WorkbenchTest(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory, Path, str]:
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "review@example.test")
        git(repo, "config", "user.name", "Review Test")
        (repo / "alpha.md").write_text("# Alpha\n\nOld rule.\n", encoding="utf-8")
        (repo / "beta.md").write_text("# Beta\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "base")
        return temp, repo, git(repo, "rev-parse", "HEAD")

    def test_canonical_hash_and_script_escape(self) -> None:
        self.assertEqual(workbench.text_sha256("a\r\nb\r\n"), workbench.text_sha256("a\nb\n"))
        encoded = workbench.safe_script_json({"value": "</script>\u2028"})
        self.assertNotIn("</script>", encoded)
        self.assertIn("\\u003c/script\\u003e", encoded)
        shell = "A __ONE__ B __TWO__ C"
        rendered = workbench.substitute_shell(shell, {"__ONE__": "contains __TWO__", "__TWO__": "done"})
        self.assertEqual(rendered, "A contains __TWO__ B done C")

    def test_git_bundle_pins_exact_commits_and_renders_offline(self) -> None:
        temp, repo, base = self.make_repo()
        self.addCleanup(temp.cleanup)
        (repo / "alpha.md").write_text("# Alpha\n\nNew rule.\n", encoding="utf-8")
        (repo / "added.md").write_text("new\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "head")
        head = git(repo, "rev-parse", "HEAD")
        story = repo / "story.json"
        story.write_text(json.dumps({
            "claims": [{
                "id": "claim-alpha", "label": "Prevent stale rules", "text": "Use the new rule.",
                "epistemicStatus": "recommendation", "filePath": "alpha.md", "evidenceIds": ["source-1"]
            }],
            "sources": [{"id": "source-1", "label": "Review finding", "excerpt": "Old rule failed."}],
            "fileIntents": {"alpha.md": "Replace the obsolete rule."}
        }), encoding="utf-8")
        bundle_path = repo / "bundle.json"
        html_path = repo / "report.html"
        run("git-bundle", "--repo", str(repo), "--base", base, "--head", head, "--story", str(story),
            "--output", str(bundle_path), "--title", "Exact review")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["comparison"]["base"], base)
        self.assertEqual(bundle["comparison"]["head"], head)
        self.assertEqual({item["path"] for item in bundle["files"]}, {"alpha.md", "added.md"})
        alpha = next(item for item in bundle["files"] if item["path"] == "alpha.md")
        self.assertEqual(alpha["diff"]["hunks"][0]["claimIds"], ["claim-alpha"])
        run("render", "--bundle", str(bundle_path), "--output", str(html_path))
        rendered = html_path.read_text(encoding="utf-8")
        shared_js = (HTML_ROOT.parent / "shared/components.js").read_text(encoding="utf-8")
        shared_css = (HTML_ROOT.parent / "shared/components.css").read_text(encoding="utf-8")
        self.assertEqual(rendered.count(shared_js), 1)
        self.assertEqual(rendered.count(shared_css), 1)
        self.assertLess(rendered.index(shared_js), rendered.index('const bundle = JSON.parse'))
        self.assertNotRegex(rendered, r'<script[^>]+src=')
        self.assertNotRegex(rendered, r'<link[^>]+rel=["\']stylesheet')
        self.assertIn("Exact review", rendered)
        self.assertIn('id="problem-list"', rendered)
        self.assertIn('id="problem-preview"', rendered)
        self.assertNotIn('class="problem-root"', rendered)
        self.assertNotIn("START HERE", rendered.upper())
        self.assertIn('id="enter-zen"', rendered)
        self.assertIn('id="review-back-to-problems"', rendered)
        self.assertIn('id="restore-issue"', rendered)
        self.assertIn('id="restore-files"', rendered)
        self.assertIn('id="restore-inspector"', rendered)
        self.assertIn('screen: "overview"', rendered)
        self.assertIn('window.addEventListener("popstate"', rendered)
        self.assertIn('button.addEventListener("mouseenter"', rendered)
        self.assertIn('button.addEventListener("focus"', rendered)
        self.assertIn('button.addEventListener("keydown"', rendered)
        self.assertIn('button.setAttribute("aria-current"', rendered)
        self.assertIn('"evidence-route"', rendered)
        self.assertIn('wrapper.addEventListener("keydown"', rendered)
        self.assertIn('row.addEventListener("keydown"', rendered)
        self.assertIn('file.applyPrecondition.sha256 : "absent"', rendered)
        self.assertRegex(rendered, r'(?s)\.problem-map\s*\{[^}]*overflow-x:\s*clip;')
        self.assertIn('--radius-md: 11px;', rendered)
        self.assertIn('--shadow-sm: 0 1px 2px rgba(24, 32, 35, .07)', rendered)
        self.assertIn('Primer Octicons v19.33.0 (MIT)', rendered)
        self.assertIn('id="octicon-arrow-left"', rendered)
        self.assertIn('href="#octicon-check-circle"', rendered)
        self.assertIn('setButtonLabel(edit, "pencil"', rendered)
        self.assertNotIn('#edit-toggle::before', rendered)
        self.assertRegex(rendered, r'(?s)\.problem-card\s*\{[^}]*border-radius:\s*var\(--radius-md\);')
        self.assertRegex(rendered, r'(?s)\.workbench\s*\{[^}]*gap:\s*8px;[^}]*padding:\s*0 8px 8px;')
        self.assertRegex(rendered, r'(?s)\.mapping-list code\s*\{[^}]*overflow-wrap:\s*anywhere;')
        self.assertRegex(rendered, r'(?s)\.content-surface\s*\{[^}]*max-width:\s*100%;')
        self.assertRegex(rendered, r'(?s)@media \(max-width: 620px\).*?\.review-panel\s*\{[^}]*max-width:\s*100%;')
        self.assertRegex(rendered, r'(?s)@media \(max-width: 620px\).*?\.code-view\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*100%;')
        self.assertIn('.apply-file { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr);', rendered)
        self.assertIn('button.dataset.view = value', rendered)
        self.assertIn('["markdown", "Markdown"]', rendered)
        self.assertIn('["source", "源文件"]', rendered)
        self.assertIn('["diff", "Diff"]', rendered)
        self.assertIn("proposalFingerprint", rendered)
        self.assertNotIn('id="coverage-list"', rendered)
        self.assertNotIn("trace-panel", rendered)
        self.assertNotIn("scrollIntoView", rendered)
        self.assertNotIn("https://", rendered)
        self.assertNotIn("http://", rendered)
        self.assertNotIn("__WORKBENCH_", rendered)

    def test_renderer_does_not_rescan_template_tokens_inside_bundle(self) -> None:
        temp, repo, _ = self.make_repo()
        self.addCleanup(temp.cleanup)
        proposal = repo / "tokens.json"
        token_text = "__WORKBENCH_CSS__ __WORKBENCH_JS__ __WORKBENCH_FALLBACK__ __WORKBENCH_DATA__"
        proposal.write_text(json.dumps({
            "title": "Token source review",
            "summary": token_text,
            "files": [{"path": "alpha.md", "proposedText": f"# Alpha\n\n{token_text}\n"}]
        }), encoding="utf-8")
        bundle_path = repo / "tokens-bundle.json"
        html_path = repo / "tokens.html"
        run("reflection-bundle", "--repo", str(repo), "--proposal", str(proposal), "--output", str(bundle_path))
        run("render", "--bundle", str(bundle_path), "--output", str(html_path))
        rendered = html_path.read_text(encoding="utf-8")
        match = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', rendered, re.DOTALL)
        self.assertIsNotNone(match)
        embedded = json.loads(match.group(1))
        self.assertEqual(embedded["summary"], token_text)
        self.assertEqual(embedded["files"][0]["proposed"]["text"], f"# Alpha\n\n{token_text}\n")

    def test_reflection_round_trip_check_and_apply(self) -> None:
        temp, repo, _ = self.make_repo()
        self.addCleanup(temp.cleanup)
        proposal = repo / "proposal.json"
        proposal.write_text(json.dumps({
            "bundleId": "reflection-test",
            "title": "Reflect on a stopped delivery",
            "question": "What rule should change?",
            "summary": "A proposed workflow correction.",
            "event": "Optional visual generation blocked delivery.",
            "sources": [{"id": "event", "label": "Event summary", "excerpt": "Delivery stopped."}],
            "claims": [{
                "id": "cause", "label": "False gate", "text": "A non-essential tool became a gate.",
                "epistemicStatus": "inference", "filePath": "alpha.md", "evidenceIds": ["event"]
            }],
            "fileIntents": {"alpha.md": "Keep optional visual tools non-blocking."},
            "files": [{"path": "alpha.md", "proposedText": "# Alpha\n\nVisual tools are optional.\n"}]
        }), encoding="utf-8")
        bundle_path = repo / "reflection.json"
        response_path = repo / "response.json"
        run("reflection-bundle", "--repo", str(repo), "--proposal", str(proposal), "--output", str(bundle_path))
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        response_path.write_text(json.dumps({
            "schemaVersion": "1.0",
            "bundleId": bundle["bundleId"],
            "bundleDigest": bundle["bundleDigest"],
            "files": [{"path": "alpha.md", "accepted": True, "desiredText": "# Alpha\n\nVisual drafts must not block delivery.\n"}],
            "comments": [{"filePath": "alpha.md", "body": "Keep this narrow."}],
            "decisions": [{"claimId": "cause", "decision": "reviewed"}]
        }), encoding="utf-8")
        checked = run("check", "--repo", str(repo), "--bundle", str(bundle_path), "--response", str(response_path))
        self.assertEqual(json.loads(checked.stdout)["status"], "ready")
        run("apply", "--repo", str(repo), "--bundle", str(bundle_path), "--response", str(response_path))
        self.assertEqual((repo / "alpha.md").read_text(encoding="utf-8"), "# Alpha\n\nVisual drafts must not block delivery.\n")

    def test_stale_file_blocks_entire_write_set(self) -> None:
        temp, repo, _ = self.make_repo()
        self.addCleanup(temp.cleanup)
        proposal = repo / "proposal.json"
        proposal.write_text(json.dumps({
            "files": [
                {"path": "alpha.md", "proposedText": "alpha proposal\n"},
                {"path": "beta.md", "proposedText": "beta proposal\n"}
            ]
        }), encoding="utf-8")
        bundle_path = repo / "bundle.json"
        response_path = repo / "response.json"
        run("reflection-bundle", "--repo", str(repo), "--proposal", str(proposal), "--output", str(bundle_path))
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        original_beta = (repo / "beta.md").read_text(encoding="utf-8")
        response_path.write_text(json.dumps({
            "schemaVersion": "1.0", "bundleId": bundle["bundleId"], "bundleDigest": bundle["bundleDigest"],
            "files": [
                {"path": "alpha.md", "accepted": True, "desiredText": "accepted alpha\n"},
                {"path": "beta.md", "accepted": True, "desiredText": "accepted beta\n"}
            ],
            "comments": [], "decisions": []
        }), encoding="utf-8")
        (repo / "alpha.md").write_text("external change\n", encoding="utf-8")
        failed = run("apply", "--repo", str(repo), "--bundle", str(bundle_path), "--response", str(response_path), expect=2)
        self.assertIn("Stale targets", failed.stderr)
        self.assertEqual((repo / "alpha.md").read_text(encoding="utf-8"), "external change\n")
        self.assertEqual((repo / "beta.md").read_text(encoding="utf-8"), original_beta)

    def test_unsafe_response_path_is_rejected(self) -> None:
        with self.assertRaises(workbench.WorkbenchError):
            workbench.safe_relative_path("../outside.md")
        with self.assertRaises(workbench.WorkbenchError):
            workbench.safe_relative_path("C:\\outside.md")

    def test_reflection_can_have_no_rule_proposal(self) -> None:
        temp, repo, _ = self.make_repo()
        self.addCleanup(temp.cleanup)
        proposal = repo / "no-change.json"
        proposal.write_text(json.dumps({
            "title": "No rule change",
            "summary": "The fix belongs in a test, not a workspace rule.",
            "claims": [{
                "id": "no-rule", "label": "Use a test", "text": "No rule edit is justified.",
                "epistemicStatus": "recommendation", "evidenceIds": []
            }],
            "files": []
        }), encoding="utf-8")
        bundle_path = repo / "no-change-bundle.json"
        html_path = repo / "no-change.html"
        run("reflection-bundle", "--repo", str(repo), "--proposal", str(proposal), "--output", str(bundle_path))
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["files"], [])
        run("render", "--bundle", str(bundle_path), "--output", str(html_path))
        self.assertIn("No rule change", html_path.read_text(encoding="utf-8"))

    def test_reflection_exact_replacement_builds_full_proposal(self) -> None:
        temp, repo, _ = self.make_repo()
        self.addCleanup(temp.cleanup)
        proposal = repo / "replacement.json"
        proposal.write_text(json.dumps({
            "files": [{
                "path": "alpha.md",
                "replacements": [{"old": "Old rule.", "new": "Replacement rule."}]
            }]
        }), encoding="utf-8")
        bundle_path = repo / "replacement-bundle.json"
        run("reflection-bundle", "--repo", str(repo), "--proposal", str(proposal), "--output", str(bundle_path))
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["files"][0]["proposed"]["text"], "# Alpha\n\nReplacement rule.\n")


if __name__ == "__main__":
    unittest.main()
