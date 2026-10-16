from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow.work_resources import ResourceError, cleanup_resource, ensure_worktree, inspect_resource, plan_worktree
from workflow import work_resources


class WorkResourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.repo / "tracked.txt").write_text("initial\n")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.path = self.root / "trees" / "example"

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              text=True, capture_output=True, check=check)

    def create(self):
        return ensure_worktree(plan_worktree(self.repo, self.path, "codex/example"))

    def test_created_resource_can_be_reentered_and_cleaned_repeatedly(self):
        plan = plan_worktree(self.repo, self.path, "codex/example")
        self.assertFalse(plan["owned"])
        self.assertFalse(self.path.exists())
        resource = ensure_worktree(plan)
        self.assertTrue(resource["owned"])
        self.assertEqual(ensure_worktree(plan)["ownership_token"], resource["ownership_token"])
        self.assertEqual(inspect_resource(resource)["dirty"], False)
        result = cleanup_resource(resource)
        self.assertEqual(result["status"], "cleaned")
        self.assertFalse(self.path.exists())
        self.assertNotEqual(self.git("show-ref", "--verify", "refs/heads/codex/example", check=False).returncode, 0)
        self.assertEqual(cleanup_resource(resource)["status"], "cleaned")

    def test_changed_tracked_files_are_retained(self):
        resource = self.create()
        (self.path / "tracked.txt").write_text("keep my edit\n")
        result = cleanup_resource(resource)
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["dirty"])
        self.assertEqual((self.path / "tracked.txt").read_text(), "keep my edit\n")

    def test_untracked_files_are_retained(self):
        resource = self.create()
        (self.path / "notes.txt").write_text("only copy\n")
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertEqual((self.path / "notes.txt").read_text(), "only copy\n")

    def test_ignored_files_are_retained(self):
        (self.repo / ".gitignore").write_text("*.secret\n")
        self.git("add", ".gitignore")
        self.git("commit", "-m", "ignore generated files")
        resource = self.create()
        (self.path / "only.secret").write_text("valuable local material\n")
        self.assertEqual(self.git("status", "--porcelain", cwd=self.path).stdout, "")
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertEqual((self.path / "only.secret").read_text(), "valuable local material\n")

    def test_unique_commit_remains_until_merged_into_actual_base(self):
        resource = self.create()
        (self.path / "new.txt").write_text("new result\n")
        self.git("add", ".", cwd=self.path)
        self.git("commit", "-m", "unique work", cwd=self.path)
        facts = inspect_resource(resource)
        self.assertFalse(facts["dirty"])
        self.assertFalse(facts["merged"])
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.git("merge", "--ff-only", "codex/example")
        self.assertTrue(inspect_resource(resource)["merged"])
        self.assertEqual(cleanup_resource(resource)["status"], "cleaned")

    def test_shared_resource_is_retained(self):
        resource = self.create()
        self.assertEqual(cleanup_resource(resource, shared=True)["status"], "pending")
        self.assertTrue(self.path.exists())

    def test_existing_branch_and_worktree_are_never_adopted(self):
        self.git("branch", "codex/example")
        with self.assertRaises(ResourceError) as caught:
            plan_worktree(self.repo, self.path, "codex/example")
        self.assertEqual(caught.exception.code, "branch_exists")
        self.git("worktree", "add", str(self.path), "codex/example")
        with self.assertRaises(ResourceError):
            plan_worktree(self.repo, self.path, "codex/example")
        fabricated = {"kind": "worktree", "repo": str(self.repo), "path": str(self.path),
                      "branch": "codex/example", "owned": True}
        self.assertEqual(cleanup_resource(fabricated)["status"], "pending")
        self.assertTrue(self.path.exists())

    def test_branch_created_after_planning_is_not_adopted(self):
        plan = plan_worktree(self.repo, self.path, "codex/example")
        self.git("branch", "codex/example")
        with self.assertRaises(ResourceError) as caught:
            ensure_worktree(plan)
        self.assertEqual(caught.exception.code, "branch_exists")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.git("show-ref", "--verify", "refs/heads/codex/example").returncode, 0)

    def test_directory_created_after_planning_is_preserved(self):
        plan = plan_worktree(self.repo, self.path, "codex/example")
        self.path.mkdir(parents=True)
        (self.path / "notes").write_text("preserve")
        with self.assertRaises(ResourceError):
            ensure_worktree(plan)
        self.assertEqual((self.path / "notes").read_text(), "preserve")

    def test_changed_branch_is_retained(self):
        resource = self.create()
        self.git("switch", "-c", "other-work", cwd=self.path)
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertTrue(self.path.exists())

    def test_replaced_directory_is_not_deleted_even_with_original_git_pointer(self):
        resource = self.create()
        original = self.path.with_name("original")
        self.path.rename(original)
        self.path.mkdir()
        (self.path / ".git").write_text((original / ".git").read_text())
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertTrue(self.path.exists())
        self.assertTrue(original.exists())

    def test_symlink_replacement_is_preserved(self):
        resource = self.create()
        original = self.path.with_name("original")
        self.path.rename(original)
        self.path.symlink_to(original, target_is_directory=True)
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertTrue(self.path.is_symlink())

    def test_locked_and_busy_worktrees_are_retained(self):
        resource = self.create()
        self.git("worktree", "lock", str(self.path))
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.git("worktree", "unlock", str(self.path))
        lock = Path(resource["gitdir"]) / "index.lock"
        lock.touch()
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertTrue(self.path.exists())

    def test_reference_lock_retains_worktree_before_cleanup(self):
        resource = self.create()
        lock = self.repo / ".git" / "refs" / "heads" / "codex" / "example.lock"
        lock.touch()
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertTrue(self.path.exists())

    def test_git_directory_replacement_is_retained(self):
        resource = self.create()
        original = self.repo / ".original-git"
        (self.repo / ".git").rename(original)
        (self.repo / ".git").symlink_to(original, target_is_directory=True)
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertTrue(self.path.exists())

    def test_changing_resource_to_main_checkout_cannot_delete_it(self):
        resource = self.create()
        forged = dict(resource, path=str(self.repo))
        self.assertEqual(cleanup_resource(forged)["status"], "pending")
        self.assertTrue((self.repo / "tracked.txt").exists())
        self.assertTrue(self.path.exists())

    def test_deleted_receipt_prevents_cleanup(self):
        resource = self.create()
        receipt = self.repo / ".git" / "work-resource-receipts" / (resource["ownership_token"] + ".json")
        receipt.unlink()
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertTrue(self.path.exists())

    def test_recreated_resource_is_not_owned_by_completed_receipt(self):
        resource = self.create()
        self.assertEqual(cleanup_resource(resource)["status"], "cleaned")
        self.git("worktree", "add", "-b", "codex/example", str(self.path), "main")
        self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertTrue(self.path.exists())

    def test_cannot_target_main_checkout_or_its_parent(self):
        for path in (self.repo, self.root, self.repo / "nested"):
            with self.subTest(path=path), self.assertRaises(ResourceError):
                plan_worktree(self.repo, path, "codex/example")

    def test_shell_metacharacters_in_path_are_literal(self):
        self.path = self.root / "树 $(touch sentinel) spaces"
        resource = self.create()
        self.assertTrue(self.path.exists())
        self.assertFalse((self.repo / "sentinel").exists())
        self.assertEqual(cleanup_resource(resource)["status"], "cleaned")

    def test_cleanup_resumes_after_worktree_removal_before_checkpoint_write(self):
        resource = self.create()
        original_save = work_resources._save

        def interrupt_on_tree_removed(record):
            if record.get("phase") == "tree_removed":
                raise OSError("simulated interruption after Git worktree removal")
            original_save(record)

        with patch.object(work_resources, "_save", side_effect=interrupt_on_tree_removed):
            result = cleanup_resource(resource)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.git("show-ref", "--verify", "refs/heads/codex/example").returncode, 0)
        self.assertEqual(cleanup_resource(resource)["status"], "cleaned")

    def test_cleanup_resumes_after_branch_deletion_before_checkpoint_write(self):
        resource = self.create()
        original_save = work_resources._save

        def interrupt_on_cleaned(record):
            if record.get("phase") == "cleaned":
                raise OSError("simulated interruption after branch deletion")
            original_save(record)

        with patch.object(work_resources, "_save", side_effect=interrupt_on_cleaned):
            self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertFalse(self.path.exists())
        self.assertEqual(cleanup_resource(resource)["status"], "cleaned")

    def test_branch_advanced_during_cleanup_is_retained(self):
        resource = self.create()
        original_git = work_resources._git
        changed_head = None

        def advance_before_delete(repo, *args, **kwargs):
            nonlocal changed_head
            if args == ("update-ref", "--stdin"):
                tree = self.git("rev-parse", "main^{tree}").stdout.strip()
                changed_head = self.git("commit-tree", tree, "-p", "main", "-m", "concurrent work").stdout.strip()
                self.git("update-ref", "refs/heads/codex/example", changed_head)
            return original_git(repo, *args, **kwargs)

        with patch.object(work_resources, "_git", side_effect=advance_before_delete):
            self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertEqual(self.git("rev-parse", "codex/example").stdout.strip(), changed_head)
        self.assertEqual(cleanup_resource(resource)["status"], "pending")

    def test_base_moving_during_cleanup_prevents_branch_delete(self):
        resource = self.create()
        original_git = work_resources._git

        def move_before_delete(repo, *args, **kwargs):
            if args == ("update-ref", "--stdin"):
                tree = self.git("rev-parse", "main^{tree}").stdout.strip()
                other = self.git("commit-tree", tree, "-m", "unrelated replacement base").stdout.strip()
                self.git("update-ref", "refs/heads/main", other)
            return original_git(repo, *args, **kwargs)

        with patch.object(work_resources, "_git", side_effect=move_before_delete):
            self.assertEqual(cleanup_resource(resource)["status"], "pending")
        self.assertEqual(self.git("show-ref", "--verify", "refs/heads/codex/example").returncode, 0)

    def test_creation_interrupted_before_git_changes_can_retry(self):
        plan = plan_worktree(self.repo, self.path, "codex/example")
        original_git = work_resources._git

        def interrupt_before_create(repo, *args, **kwargs):
            if args[:2] == ("worktree", "add"):
                raise ResourceError("simulated interruption")
            return original_git(repo, *args, **kwargs)

        with patch.object(work_resources, "_git", side_effect=interrupt_before_create), self.assertRaises(ResourceError):
            ensure_worktree(plan)
        resource = ensure_worktree(plan)
        self.assertTrue(resource["owned"])
        self.assertEqual(cleanup_resource(resource)["status"], "cleaned")

    def test_unverified_creation_after_git_changes_is_retained(self):
        plan = plan_worktree(self.repo, self.path, "codex/example")
        original_save = work_resources._save

        def interrupt_before_receipt(record):
            if record.get("phase") == "ready":
                raise OSError("simulated interruption before ownership verification receipt")
            original_save(record)

        with patch.object(work_resources, "_save", side_effect=interrupt_before_receipt), self.assertRaises(OSError):
            ensure_worktree(plan)
        with self.assertRaises(ResourceError) as caught:
            ensure_worktree(plan)
        self.assertEqual(caught.exception.code, "incomplete_operation")
        self.assertTrue(self.path.exists())
        self.assertEqual(cleanup_resource(dict(plan, owned=True))["status"], "pending")

    def test_large_dirty_tree_has_bounded_observation_and_is_never_removed(self):
        import json
        (self.repo / '.gitignore').write_text('node_modules/\n')
        self.git('add', '.gitignore'); self.git('commit', '-m', 'ignore dependencies')
        resource = self.create()
        modules = self.path / 'node_modules'; modules.mkdir()
        for i in range(300):
            (modules / f'package-{i}.js').write_text('dependency')
        for i in range(80):
            (self.path / f'note-{i}.txt').write_text('unique user work')
        result = cleanup_resource(resource)
        self.assertEqual(result['status'], 'pending')
        self.assertTrue(result['dirty'])
        self.assertLess(len(json.dumps(result)), 1000)
        self.assertNotIn('changes', result)
        self.assertNotIn('note-79', json.dumps(result))
        self.assertNotIn('node_modules', json.dumps(result))
        self.assertEqual(len(list(modules.iterdir())), 300)
        self.assertTrue((self.path / 'note-79.txt').exists())


if __name__ == "__main__":
    unittest.main()
