from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from test_workbench import SCRIPT, run, workbench


class LiveAppTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.target = self.repo / "rules.md"
        self.target.write_text("# A\nold A\n# B\nold B\n")
        self.bundle_path = self.repo / "review.json"
        proposal = self.repo / "proposal.json"
        proposal.write_text(json.dumps({"files": [
            {"path": "rules.md", "proposedText": "# A\nnew A\n# B\nnew B\n"}]}))
        run("reflection-bundle", "--repo", str(self.repo), "--proposal", str(proposal),
            "--output", str(self.bundle_path))
        self.bundle = json.loads(self.bundle_path.read_text())

    def start(self, *options):
        process = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "serve", "--repo", str(self.repo),
                                    "--bundle", str(self.bundle_path), "--port", "0", "--no-open", *options],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def stop():
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
        self.addCleanup(stop)
        self.url = process.stdout.readline().strip()
        if not self.url:
            self.fail(process.stderr.read())
        self.assertEqual(urlsplit(self.url).hostname, "127.0.0.1")
        return process

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(urlsplit(self.url).netloc, timeout=5)
        hdr = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        hdr.update(headers or {})
        try:
            conn.request(method, path, None if body is None else json.dumps(body), hdr)
            result = conn.getresponse()
            raw = result.read().decode()
            return result.status, json.loads(raw) if result.getheader("Content-Type").startswith("application/json") else raw
        finally:
            conn.close()

    def test_serves_only_review_page_and_assets(self):
        self.start()
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('src="/assets/workbench.js"', page)
        for asset in ("workbench.js", "workbench.css", "live.js"):
            self.assertEqual(self.request("GET", "/assets/" + asset)[0], 200)
        for path in ("/assets/../rules.md", "/rules.md", "/api/unknown"):
            self.assertEqual(self.request("GET", path)[0], 404)
        status, session = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(session["revision"], 0)
        self.assertEqual(session["bundleDigest"], self.bundle["bundleDigest"])

    def identity(self):
        status, session = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        return {key: session[key] for key in ("revision", "bundleDigest", "sessionId")}

    def payload(self):
        comments = [{"id": "line", "body": "line comment", "anchor": {"type": "line", "startLine": 2}},
                    {"id": "selection", "body": "selected", "anchor": {"type": "text", "quote": "old A"}},
                    {"id": "theme", "themeId": "rules", "body": "theme comment"}]
        decisions = [{"blockId": "A", "decision": "accepted"}, {"blockId": "B", "decision": "rejected"}]
        return {**self.identity(), "state": {"decisions": [["A", "accepted"], ["B", "rejected"]],
                "edits": [["rules.md", "# A\nhand edit\n# B\nnew B\n"]], "comments": comments,
                "blockEdits": [], "themeDrafts": [], "manualMarks": [], "log": [], "lastEditUndo": None,
                "editTarget": None, "editDraft": "", "commentTarget": None, "commentDraft": ""},
                "response": {"schemaVersion": "1.0", "bundleDigest": self.bundle["bundleDigest"],
                             "bundleId": self.bundle["bundleId"], "comments": comments, "decisions": decisions,
                             "files": [{"path": "rules.md", "accepted": True,
                                        "desiredText": "# A\nhand edit\n# B\nold B\n"}]}}

    def test_origin_host_and_content_type_before_any_mutation(self):
        self.start()
        payload = self.payload()
        for method, path in (("PUT", "/api/state"), ("POST", "/api/check"), ("POST", "/api/apply")):
            for headers in ({"Host": "example.test"}, {"Origin": "https://example.test"}, {"Origin": "null"},
                            {"Origin": ""}, {"Origin": self.url.rstrip("/") + "0"}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(method, path, payload, headers)[0], 403)
        self.assertEqual(self.request("PUT", "/api/state", payload, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "example.test"})[0], 403)
        self.assertFalse(self.bundle_path.with_suffix(".state.json").exists())

    def test_saved_draft_reopens_and_concurrent_window_cannot_overwrite_it(self):
        custom = self.repo / "drafts" / "mine.json"
        self.start("--state", str(custom))
        first_url = self.url
        payload = self.payload()
        self.start("--state", str(custom))
        self.assertEqual(self.request("PUT", "/api/state", payload), (200, {"revision": 1}))
        self.url = first_url
        self.assertEqual(self.request("PUT", "/api/state", payload), (200, {"revision": 1}))
        older = {**payload, "state": {}}
        self.assertEqual(self.request("PUT", "/api/state", older)[0], 409)
        self.assertEqual(self.request("POST", "/api/apply", older)[0], 409)
        self.assertEqual(json.loads(custom.read_text())["state"], payload["state"])
        self.start("--state", str(custom))
        _, restored = self.request("GET", "/api/session")
        self.assertEqual(restored["revision"], 1)
        self.assertEqual(restored["state"], payload["state"])
        self.assertIn("hand edit", self.request("GET", "/")[1])

    def test_check_apply_and_retry_return_verified_files_and_original_backup(self):
        self.start()
        payload = self.payload()
        original = self.target.read_bytes()
        self.assertEqual(self.request("PUT", "/api/state", payload)[0], 200)
        version = self.identity()
        status, check = self.request("POST", "/api/check", version)
        self.assertEqual(status, 200)
        self.assertEqual(check["files"], ["rules.md"])
        self.assertIn("+hand edit", check["patch"])
        self.assertEqual(self.target.read_bytes(), original)
        status, result = self.request("POST", "/api/apply", version)
        self.assertEqual(status, 200)
        self.assertEqual(result["files"], ["rules.md"])
        self.assertEqual(self.target.read_bytes(), b"# A\nhand edit\n# B\nold B\n")
        backup = Path(result["recoveryCopy"])
        self.addCleanup(shutil.rmtree, backup)
        self.assertEqual((backup / "0").read_bytes(), original)
        self.assertTrue((self.repo / ".work-review.lock").is_file())
        self.assertEqual(self.request("POST", "/api/apply", version), (200, result))
        self.assertEqual(self.request("GET", "/api/session")[1]["applied"], result)
        self.target.write_text("later edit")
        status, error = self.request("POST", "/api/apply", version)
        self.assertEqual(status, 409)
        self.assertIn("rules.md", error["error"])
        self.assertEqual(self.target.read_text(), "later edit")

    def test_stale_target_keeps_review_and_external_file(self):
        self.start()
        payload = self.payload()
        self.assertEqual(self.request("PUT", "/api/state", payload)[0], 200)
        self.target.write_text("outside editor\n")
        for action in ("check", "apply"):
            status, result = self.request("POST", "/api/" + action, self.identity())
            self.assertEqual(status, 409)
            for detail in ("Stale", "expected", "actual", "rules.md"):
                self.assertIn(detail, result["error"])
        self.assertEqual(self.target.read_text(), "outside editor\n")
        self.assertEqual(self.request("GET", "/api/session")[1]["state"], payload["state"])

    def test_pending_wrong_bundle_and_symlink_escape_cannot_apply(self):
        self.start()
        payload = self.payload()
        self.assertEqual(self.request("POST", "/api/apply", self.identity())[0], 400)
        payload["response"]["decisions"][0]["decision"] = "pending"
        self.assertEqual(self.request("PUT", "/api/state", payload)[0], 200)
        self.assertEqual(self.request("POST", "/api/apply", self.identity())[0], 400)
        payload = self.payload()
        payload["response"]["bundleDigest"] = "wrong"
        self.assertEqual(self.request("PUT", "/api/state", payload)[0], 400)
        self.assertEqual(self.request("PUT", "/api/state", self.payload())[0], 200)
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "rules.md"
            self.target.rename(external)
            self.target.symlink_to(external)
            status, error = self.request("POST", "/api/apply", self.identity())
            self.assertEqual(status, 400)
            self.assertIn("escapes root", error["error"])
            self.assertEqual(external.read_text(), "# A\nold A\n# B\nold B\n")

    def test_replaced_bundle_or_reserved_state_path_is_rejected(self):
        self.start()
        self.assertEqual(self.request("PUT", "/api/state", self.payload())[0], 200)
        self.bundle["title"] = "changed since startup"
        self.bundle_path.write_text(json.dumps(self.bundle))
        status, error = self.request("POST", "/api/apply", self.identity())
        self.assertEqual(status, 400)
        self.assertIn("digest", error["error"])
        self.assertEqual(self.target.read_text(), "# A\nold A\n# B\nold B\n")
        proposal = self.repo / "proposal.json"
        run("reflection-bundle", "--repo", str(self.repo), "--proposal", str(proposal),
            "--output", str(self.bundle_path))
        for path in (self.target, self.bundle_path, self.repo / ".work-review.lock"):
            result = run("serve", "--repo", str(self.repo), "--bundle", str(self.bundle_path),
                         "--state", str(path), "--no-open", expect=2)
            self.assertRegex(result.stderr, "Reserved|different paths")


    def test_simultaneous_saves_have_only_one_winner(self):
        self.start()
        a, b = self.payload(), self.payload()
        b["state"]["editDraft"] = "other edit"
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda body: self.request("PUT", "/api/state", body)[0], (a, b)))
        self.assertEqual(sorted(codes), [200, 409])
        latest = self.request("GET", "/api/session")[1]
        self.assertEqual(latest["revision"], 1)
        self.assertEqual(latest["state"], (a, b)[codes.index(200)]["state"])

    def test_interrupted_atomic_replace_leaves_last_saved_snapshot_intact(self):
        self.start()
        payload = self.payload()
        self.assertEqual(self.request("PUT", "/api/state", payload)[0], 200)
        state_path = self.bundle_path.with_suffix(".state.json")
        session = workbench.ReviewSession(self.repo, self.bundle_path, state_path)
        original = state_path.read_bytes()
        payload["revision"] = 1
        payload["state"]["commentDraft"] = "unfinished"
        with patch.object(workbench.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                session.mutate("state", payload)
        self.assertEqual(state_path.read_bytes(), original)
        self.assertEqual(list(state_path.parent.glob(".review-state-*")), [])
        self.assertEqual(self.request("GET", "/api/session")[1]["revision"], 1)
