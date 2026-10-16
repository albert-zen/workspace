"""Create and retire explicitly owned Git worktrees without adopting existing work.

Persist the complete plan before calling ensure_worktree, and persist its returned
resource afterwards. The ownership receipt in Git's common directory supplies the
independent creation evidence required by cleanup; a branch name is never enough.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import uuid


class ResourceError(Exception):
    def __init__(self, message: str, code: str = "resource_error"):
        super().__init__(message)
        self.code = code


def _git(repo: Path, *args: str, check: bool = True, input: str | None = None):
    env = os.environ.copy()
    # A caller's Git override must not redirect operations away from this resource.
    for key in list(env):
        if key.startswith("GIT_"):
            del env[key]
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(["git", "-C", str(repo), *args], input=input,
                            capture_output=True, text=True, env=env)
    if check and result.returncode:
        raise ResourceError(result.stderr.strip() or "Git operation failed", "git_error")
    return result


def _identity(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def _no_symlinks(path: Path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ResourceError(f"Resource path contains a symbolic link: {path}", "path_changed")


def _common(repo: Path) -> Path:
    _no_symlinks(repo)
    result = Path(_git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    raw = repo / result if not result.is_absolute() else result
    _no_symlinks(raw)
    common = raw.resolve()
    _no_symlinks(common)
    return common


def _worktrees(repo: Path) -> list[dict]:
    records = []
    current = {}
    for field in _git(repo, "worktree", "list", "--porcelain", "-z").stdout.split("\0"):
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = field.partition(" ")
        current[key] = value if value else True
    if current:
        records.append(current)
    return records


def _ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def _commit(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").stdout.strip()


def _record_path(resource: dict) -> Path:
    token = resource.get("ownership_token", "")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ResourceError("Resource has no valid creation receipt", "not_owned")
    common = _common(Path(resource["repo"]))
    directory = common / "work-resource-receipts"
    _no_symlinks(directory)
    return directory / f"{token}.json"


def _load(resource: dict) -> dict | None:
    path = _record_path(resource)
    _no_symlinks(path)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ResourceError("Cannot read resource creation receipt", "invalid_receipt") from exc
    for field in ("kind", "repo", "path", "branch", "base", "base_commit", "ownership_token",
                  "repo_identity", "common_dir", "common_identity"):
        if record.get(field) != resource.get(field):
            raise ResourceError(f"Resource creation receipt differs at {field}", "identity_changed")
    return record


def _save(resource: dict):
    path = _record_path(resource)
    path.parent.mkdir(exist_ok=True)
    _no_symlinks(path)
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(resource, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def _locked(resource: dict):
    path = _common(Path(resource["repo"])) / "work-resource-operation.lock"
    _no_symlinks(path)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResourceError("Another resource operation is in progress", "busy") from exc
        yield
    finally:
        os.close(descriptor)


def repository_root(repo: Path) -> Path:
    """Identify the primary checkout even when the caller supplies a worktree."""
    repo = Path(repo).expanduser().resolve()
    return Path(_worktrees(repo)[0]['worktree']).resolve()


def resolve_commit(repo: Path, ref: str) -> str:
    return _commit(Path(repo), ref)


def resolve_base(repo: Path, ref: str) -> str:
    result = _git(Path(repo), 'rev-parse', '--symbolic-full-name', '--verify', '--end-of-options', ref)
    name = result.stdout.strip()
    if not name.startswith(('refs/heads/', 'refs/remotes/')):
        raise ResourceError('Worktree base must identify a live branch', 'invalid_base')
    return name


def plan_worktree(repo: Path, path: Path, branch: str, base: str | None = None, at: str | None = None) -> dict:
    """Return a read-only plan. No directory, ref or ownership marker is created."""
    repo = Path(repo).expanduser().resolve()
    repo = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    common = _common(repo)
    primary = _worktrees(repo)[0]
    # Always anchor the receipt to the primary checkout, never a disposable one.
    repo = Path(primary["worktree"]).resolve()
    _no_symlinks(repo)
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ResourceError("Worktree target is a symbolic link", "path_changed")
    path = path.resolve()
    if path == repo or repo in path.parents or path in repo.parents or common == path or common in path.parents:
        raise ResourceError("A managed worktree must be outside the repository checkout", "unsafe_path")
    if path.exists() or any(Path(item["worktree"]) == path for item in _worktrees(repo)):
        raise ResourceError("Worktree target already exists; existing work is not adopted", "path_exists")
    if not isinstance(branch, str) or not branch.startswith("codex/"):
        raise ResourceError("Managed worktree branches must start with codex/", "invalid_branch")
    if _git(repo, "check-ref-format", "--branch", branch, check=False).returncode:
        raise ResourceError("Invalid worktree branch", "invalid_branch")
    if _ref_exists(repo, f"refs/heads/{branch}"):
        raise ResourceError("Branch already exists; existing work is not adopted", "branch_exists")
    if base is None:
        base = next((ref for ref in ("refs/heads/main", "refs/heads/master") if _ref_exists(repo, ref)), None)
        if base is None:
            base = _git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip()
    if not isinstance(base, str) or base.startswith("-"):
        raise ResourceError("Invalid worktree base", "invalid_base")
    full_ref = _git(repo, "rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", base, check=False)
    full_name = full_ref.stdout.strip()
    if full_ref.returncode or not full_name.startswith(("refs/heads/", "refs/remotes/")):
        raise ResourceError("Worktree base must identify a live local or remote tracking branch", "invalid_base")
    return {"kind": "worktree", "repo": str(repo), "path": str(path), "branch": branch,
            "base": full_name, "base_commit": _commit(repo, at if at is not None else full_name), "owned": False,
            "ownership_token": uuid.uuid4().hex, "repo_identity": _identity(repo),
            "common_dir": str(common), "common_identity": _identity(common)}


def _validate_root(resource: dict):
    if resource.get("kind") != "worktree":
        raise ResourceError("Unsupported resource kind", "invalid_resource")
    repo, path = Path(resource["repo"]), Path(resource["path"])
    if not repo.is_absolute() or not path.is_absolute():
        raise ResourceError("Resource paths must be absolute", "invalid_resource")
    _no_symlinks(repo)
    _no_symlinks(path)
    if _identity(repo) != resource.get("repo_identity"):
        raise ResourceError("Repository directory has been replaced", "identity_changed")
    common = _common(repo)
    if str(common) != resource.get("common_dir") or _identity(common) != resource.get("common_identity"):
        raise ResourceError("Repository Git directory has been replaced", "identity_changed")
    if path == repo or repo in path.parents or path in repo.parents:
        raise ResourceError("Refusing to act on repository checkout or its parents", "unsafe_path")
    if not str(resource.get("branch", "")).startswith("codex/"):
        raise ResourceError("Resource has no managed branch", "invalid_branch")


def ensure_worktree(resource: dict) -> dict:
    """Create exactly the planned resource or re-enter its verified creation."""
    _validate_root(resource)
    with _locked(resource):
        record = _load(resource)
        if record:
            if record.get("phase") == "creating":
                # A failure before any Git side effect may be retried. Once a
                # directory or branch exists without a verified receipt, retain
                # it for inspection rather than inferring ownership by name.
                repo, path = Path(resource["repo"]), Path(resource["path"])
                if (path.exists() or _ref_exists(repo, f"refs/heads/{resource['branch']}") or
                        any(Path(item["worktree"]) == path for item in _worktrees(repo))):
                    raise ResourceError("Creation is incomplete; retain and inspect existing resources", "incomplete_operation")
                record = None
            elif record.get("phase") != "ready":
                raise ResourceError("Creation or cleanup is incomplete; inspect the retained resource", "incomplete_operation")
            else:
                inspected = _inspect(record)
                if inspected["status"] != "ready":
                    raise ResourceError(inspected["reason"], "identity_changed")
                return record
        repo, path = Path(resource["repo"]), Path(resource["path"])
        if path.exists() or any(Path(item["worktree"]) == path for item in _worktrees(repo)):
            raise ResourceError("Target was created after planning; refusing to adopt it", "path_exists")
        if _ref_exists(repo, f"refs/heads/{resource['branch']}"):
            raise ResourceError("Branch was created after planning; refusing to adopt it", "branch_exists")
        # Save intent before Git changes. An interrupted unverified create remains
        # explicit and cannot be mistaken for independently created work.
        record = dict(resource, phase="creating")
        _save(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        _no_symlinks(path)
        _git(repo, "worktree", "add", "-b", resource["branch"], "--", str(path), resource["base_commit"])
        gitdir = Path(_git(path, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
        record.update(owned=True, phase="ready", path_identity=_identity(path),
                      gitdir=str(gitdir), gitdir_identity=_identity(gitdir))
        _save(record)
        return record


def _pending(reason: str, **facts) -> dict:
    return dict(status="pending", reason=reason, details=[reason], head=None, dirty=None, merged=None, **facts)


def _inspect(resource: dict) -> dict:
    _validate_root(resource)
    repo, path = Path(resource["repo"]), Path(resource["path"])
    record = _load(resource)
    if not record or not record.get("owned"):
        return _pending("No verified creation receipt; resource is not owned")
    entries = _worktrees(repo)
    matches = [item for item in entries if Path(item["worktree"]) == path]
    branch_ref = f"refs/heads/{record['branch']}"
    if record.get("phase") == "cleaned":
        if path.exists() or matches or _ref_exists(repo, branch_ref):
            return _pending("A resource exists after cleanup; it is not owned by the old receipt")
        return dict(status="cleaned", details=[], head=None, dirty=False, merged=True)
    if not path.exists() or not matches:
        return _pending("Worktree is missing or no longer registered; inspect before removing remaining resources")
    if matches[0] == entries[0]:
        return _pending("The main checkout cannot be cleaned as a managed resource")
    if len(matches) != 1 or _identity(path) != record.get("path_identity"):
        return _pending("Worktree directory identity has changed")
    entry = matches[0]
    if entry.get("branch") != branch_ref:
        return _pending("Worktree branch has changed")
    gitdir = Path(_git(path, "rev-parse", "--absolute-git-dir").stdout.strip())
    _no_symlinks(gitdir)
    if str(gitdir) != record.get("gitdir") or _identity(gitdir) != record.get("gitdir_identity"):
        return _pending("Worktree Git directory identity has changed")
    if entry.get("locked") or entry.get("prunable"):
        return _pending("Git worktree is locked or prunable")
    # Git lock and ongoing-operation files are evidence of active work. Their
    # absence is not a process-liveness claim; no force removal is ever used.
    busy_names = ("index.lock", "HEAD.lock", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                  "rebase-merge", "rebase-apply", "BISECT_LOG", "sequencer")
    if any((gitdir / name).exists() for name in busy_names):
        return _pending("Git worktree has an operation in progress")
    common = Path(record["common_dir"])
    if any((common / name).exists() for name in
           (f"{branch_ref}.lock", f"{record['base']}.lock", "packed-refs.lock")):
        return _pending("Git resource references have an operation in progress")
    changes = _git(path, "status", "--porcelain=v1", "--untracked-files=normal", "--ignored=matching", "-z").stdout
    head = _commit(path, "HEAD")
    base_head = _commit(repo, record["base"])
    merged = _git(repo, "merge-base", "--is-ancestor", head, base_head, check=False).returncode == 0
    return dict(status="ready", details=[], head=head, base_head=base_head,
                dirty=bool(changes), merged=merged)


def inspect_resource(resource: dict) -> dict:
    """Report fresh facts; malformed or replaced resources are explicit pending."""
    try:
        return _inspect(resource)
    except (ResourceError, OSError, KeyError, ValueError) as exc:
        return _pending(str(exc))


def cleanup_resource(resource: dict, *, shared: bool = False) -> dict:
    """Remove only evidenced, private, clean, merged local resources.

    Pending is an honest retained-resource result, not a completed cleanup. The
    receipt records each completed step so retry never adopts a recreated path.
    """
    if shared:
        return _pending("Resource is shared by another work item")
    if not resource.get("owned"):
        return _pending("Resource is not marked owned")
    try:
        _validate_root(resource)
        with _locked(resource):
            record = _load(resource)
            if not record or not record.get("owned"):
                return _pending("No verified creation receipt; resource is not owned")
            repo, path = Path(record["repo"]), Path(record["path"])
            if record.get("phase") == "removing" and not path.exists():
                if any(Path(item["worktree"]) == path for item in _worktrees(repo)):
                    return _pending("Interrupted worktree removal still has a Git registration; inspect it first")
                record["phase"] = "tree_removed"
                _save(record)
            if record.get("phase") == "tree_removed":
                if path.exists() or any(Path(item["worktree"]) == path for item in _worktrees(repo)):
                    return _pending("Worktree target exists after removal; it is not owned by this cleanup")
                return _remove_branch(record)
            facts = _inspect(resource)
            if facts["status"] != "ready":
                return facts
            if facts["dirty"]:
                return dict(facts, status="pending", reason="Worktree contains changed, untracked or ignored files")
            if not facts["merged"]:
                return dict(facts, status="pending", reason="Worktree has commits not reachable from the current base branch")
            # Revalidate immediately before the non-forced Git operation.
            check = _inspect(resource)
            if check != facts:
                return _pending("Worktree changed during cleanup; retry after checking the current state")
            record.update(phase="removing", cleanup_head=facts["head"], cleanup_base_head=facts["base_head"])
            _save(record)
            removed = _git(repo, "worktree", "remove", "--", str(path), check=False)
            if removed.returncode:
                return _pending(removed.stderr.strip() or "Git retained the worktree")
            record["phase"] = "tree_removed"
            _save(record)
            return _remove_branch(record)
    except (ResourceError, OSError, KeyError, ValueError) as exc:
        return _pending(str(exc))


def _remove_branch(record: dict) -> dict:
    repo = Path(record["repo"])
    branch_ref = f"refs/heads/{record['branch']}"
    if _ref_exists(repo, branch_ref):
        head = _commit(repo, branch_ref)
        base_head = _commit(repo, record["base"])
        if head != record.get("cleanup_head"):
            return _pending("Branch changed after worktree removal; preserving it")
        if any(item.get("branch") == branch_ref for item in _worktrees(repo)):
            return _pending("Branch is now checked out by another worktree")
        if _git(repo, "merge-base", "--is-ancestor", head, base_head, check=False).returncode:
            return _pending("Branch is not reachable from the current base; preserving it")
        # Compare-and-delete both refs atomically: unlike a force branch delete,
        # this cannot erase a newly advanced branch or rely on a moving base.
        transaction = f"start\nverify {record['base']} {base_head}\ndelete {branch_ref} {head}\nprepare\ncommit\n"
        result = _git(repo, "update-ref", "--stdin", input=transaction, check=False)
        if result.returncode:
            return _pending(result.stderr.strip() or "Branch changed during cleanup")
    record["phase"] = "cleaned"
    _save(record)
    return dict(status="cleaned", details=[], head=record.get("cleanup_head"), dirty=False, merged=True)
