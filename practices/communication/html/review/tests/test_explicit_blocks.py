from __future__ import annotations

import copy
import json
import unittest

import test_workbench as fixtures
from test_workbench import run, workbench


class ExplicitBlocksTest(unittest.TestCase):
    make_repo = fixtures.WorkbenchTest.make_repo

    def proposal(self):
        return {
            "claims": [{"id": "rules", "filePath": "alpha.md"}],
            "sources": [{"id": "source", "label": "User request"}],
            "blocks": [{
                "id": "rule", "filePath": "alpha.md", "title": "One rule",
                "reason": "Make the operation verifiable.", "claimId": "rules",
                "evidenceIds": ["source"], "oldRange": [2, 3], "newRange": [2, 3],
            }],
            "files": [{"path": "alpha.md", "proposedText": "# Alpha\n\nNew rule.\n"}],
        }

    def test_reflection_preserves_authored_blocks_and_rejects_invalid_ranges(self):
        temp, repo, _ = self.make_repo()
        self.addCleanup(temp.cleanup)
        spec = self.proposal()
        proposal = repo / "proposal.json"
        output = repo / "bundle.json"
        proposal.write_text(json.dumps(spec), encoding="utf-8")
        run("reflection-bundle", "--repo", str(repo), "--proposal", str(proposal), "--output", str(output))
        bundle = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(bundle["files"][0]["reviewBlocks"], spec["blocks"])
        workbench.validate_bundle(bundle)

        variants = [
            {"oldRange": [2, 9]}, {"newRange": [True, 3]},
            {"newRange": [0, 3]}, {"newRange": [2, 2]},
            {"evidenceIds": ["missing"]}, {"claimId": "missing"},
            {"reason": ""}, {"id": ""},
        ]
        for change in variants:
            with self.subTest(change=change):
                malformed = copy.deepcopy(bundle)
                malformed["files"][0]["reviewBlocks"][0].update(change)
                workbench.finalize_bundle(malformed)
                with self.assertRaises(workbench.WorkbenchError):
                    workbench.validate_bundle(malformed)

        overlap = copy.deepcopy(bundle)
        extra = copy.deepcopy(overlap["files"][0]["reviewBlocks"][0])
        extra["id"] = "another"
        overlap["files"][0]["reviewBlocks"].append(extra)
        workbench.finalize_bundle(overlap)
        with self.assertRaisesRegex(workbench.WorkbenchError, "overlapping"):
            workbench.validate_bundle(overlap)

    def test_unknown_block_path_and_uncovered_changes_fail_generation(self):
        temp, repo, _ = self.make_repo()
        self.addCleanup(temp.cleanup)
        for field, value in (("filePath", "missing.md"), ("oldRange", [0, 0])):
            with self.subTest(field=field):
                spec = self.proposal()
                spec["blocks"][0][field] = value
                proposal = repo / "proposal.json"
                proposal.write_text(json.dumps(spec), encoding="utf-8")
                result = run("reflection-bundle", "--repo", str(repo), "--proposal", str(proposal),
                             "--output", str(repo / "bad.json"), expect=2)
                self.assertIn("review block", (result.stderr + result.stdout).lower())

    def test_patch_preserves_an_unterminated_last_line(self):
        prefix = "# Alpha\n\nA\u2028B\fC\n"
        delta = workbench.make_unified("alpha.md", prefix + "Old rule.\n", prefix + "Old rule.")
        self.assertIn("+Old rule.\n\\ No newline at end of file\n", delta)
        temp, repo, _ = self.make_repo()
        self.addCleanup(temp.cleanup)
        (repo / "alpha.md").write_bytes((prefix + "Old rule.\n").encode("utf-8"))
        patch = repo / "review.patch"
        patch.write_bytes(delta.encode("utf-8"))
        fixtures.git(repo, "apply", str(patch))
        self.assertEqual((repo / "alpha.md").read_bytes().replace(b"\r\n", b"\n"), (prefix + "Old rule.").encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
