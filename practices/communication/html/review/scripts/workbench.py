#!/usr/bin/env python3
"""Build, render, check, and explicitly apply HTML review workbench bundles."""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import datetime as dt
import difflib
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import uuid


SCHEMA_VERSION = "1.0"
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


class WorkbenchError(RuntimeError):
    pass


def canonical_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def text_sha256(value: str) -> str:
    return hashlib.sha256(canonical_text(value).encode("utf-8")).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def bundle_digest(bundle: dict) -> str:
    unsigned = copy.deepcopy(bundle)
    unsigned.pop("bundleDigest", None)
    return hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()


def finalize_bundle(bundle: dict) -> dict:
    bundle["bundleDigest"] = bundle_digest(bundle)
    return bundle


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkbenchError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkbenchError(f"Expected a JSON object in {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise WorkbenchError(f"git {' '.join(args)} failed: {message}")
    return result


def exact_commit(repo: Path, revision: str) -> str:
    value = run_git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").stdout.decode().strip()
    if not re.fullmatch(r"[a-f0-9]{40}", value):
        raise WorkbenchError(f"Revision did not resolve to an exact commit: {revision}")
    return value


def safe_relative_path(raw: str) -> str:
    if not raw or "\\" in raw:
        raise WorkbenchError(f"Path must be a non-empty Git-style relative path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise WorkbenchError(f"Unsafe relative path: {raw!r}")
    return path.as_posix()


def decode_text(data: bytes | None, label: str) -> str | None:
    if data is None:
        return None
    if b"\x00" in data:
        raise WorkbenchError(f"Binary files are read-only in v1: {label}")
    try:
        return canonical_text(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise WorkbenchError(f"Non-UTF-8 files are read-only in v1: {label}") from exc


def git_file(repo: Path, revision: str, path: str) -> bytes | None:
    result = run_git(repo, "show", f"{revision}:{path}", check=False)
    return result.stdout if result.returncode == 0 else None


def snapshot(text: str | None) -> dict | None:
    if text is None:
        return None
    return {"text": text, "sha256": text_sha256(text)}


def make_unified(path: str, baseline: str | None, proposed: str | None) -> str:
    before = [] if baseline is None else canonical_text(baseline).splitlines(keepends=True)
    after = [] if proposed is None else canonical_text(proposed).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
            lineterm="\n",
        )
    )


def parse_hunks(raw_diff: str, path: str, claim_ids: list[str]) -> list[dict]:
    hunks: list[dict] = []
    current: dict | None = None
    old_line = new_line = 0
    for raw in raw_diff.splitlines():
        match = HUNK_RE.match(raw)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(3))
            current = {
                "id": f"{path.replace('/', '-')}-h{len(hunks) + 1}",
                "header": raw,
                "oldStart": old_line,
                "oldCount": int(match.group(2) or "1"),
                "newStart": new_line,
                "newCount": int(match.group(4) or "1"),
                "claimIds": list(claim_ids),
                "unexplained": not bool(claim_ids),
                "lines": [],
            }
            hunks.append(current)
            continue
        if current is None or not raw or raw.startswith(("---", "+++", "diff ", "index ")):
            continue
        prefix = raw[0]
        content = raw[1:] if prefix in (" ", "+", "-") else raw
        if prefix == "+":
            current["lines"].append({"kind": "add", "oldLine": None, "newLine": new_line, "text": content})
            new_line += 1
        elif prefix == "-":
            current["lines"].append({"kind": "remove", "oldLine": old_line, "newLine": None, "text": content})
            old_line += 1
        elif prefix == " ":
            current["lines"].append({"kind": "context", "oldLine": old_line, "newLine": new_line, "text": content})
            old_line += 1
            new_line += 1
    return hunks


def validate_bundle(bundle: dict) -> None:
    required = {
        "schemaVersion", "bundleId", "bundleDigest", "mode", "createdAt", "title",
        "question", "summary", "comparison", "sources", "story", "files",
    }
    missing = sorted(required - set(bundle))
    if missing:
        raise WorkbenchError(f"Bundle is missing: {', '.join(missing)}")
    if bundle["schemaVersion"] != SCHEMA_VERSION or bundle["mode"] not in ("git-review", "reflection"):
        raise WorkbenchError("Unsupported bundle schema or mode")
    if bundle["bundleDigest"] != bundle_digest(bundle):
        raise WorkbenchError("Bundle digest does not match its contents")
    seen: set[str] = set()
    for file in bundle["files"]:
        path = safe_relative_path(file.get("path", ""))
        if path in seen:
            raise WorkbenchError(f"Duplicate file in bundle: {path}")
        seen.add(path)
        if "applyPrecondition" not in file or "diff" not in file:
            raise WorkbenchError(f"Incomplete file record: {path}")


def story_for_path(story: dict, path: str) -> tuple[list[str], str]:
    ids: list[str] = []
    intents = story.get("fileIntents", {})
    for claim in story.get("claims", []):
        paths = claim.get("filePaths", [])
        if claim.get("filePath") == path or path in paths:
            ids.append(str(claim["id"]))
    return ids, str(intents.get(path, "Review the exact change and its evidence."))


def make_file_record(
    path: str,
    status: str,
    baseline: str | None,
    proposed: str | None,
    precondition: dict,
    story: dict,
    old_path: str | None = None,
) -> dict:
    claim_ids, intent = story_for_path(story, path)
    raw = make_unified(path, baseline, proposed)
    return {
        "path": path,
        "oldPath": old_path,
        "status": status,
        "textKind": "utf-8",
        "editable": True,
        "intent": intent,
        "baseline": snapshot(baseline),
        "proposed": snapshot(proposed),
        "applyPrecondition": precondition,
        "diff": {"rawUnified": raw, "hunks": parse_hunks(raw, path, claim_ids)},
    }


def load_story(path: Path | None) -> dict:
    if path is None:
        return {"claims": [], "fileIntents": {}, "sources": []}
    value = load_json(path)
    value.setdefault("claims", [])
    value.setdefault("fileIntents", {})
    value.setdefault("sources", [])
    return value


def command_git_bundle(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    base = exact_commit(repo, args.base)
    head = exact_commit(repo, args.head)
    story = load_story(Path(args.story) if args.story else None)
    status_text = run_git(repo, "diff", "--name-status", "--find-renames", base, head).stdout.decode("utf-8")
    files: list[dict] = []
    for line in status_text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0]
        old_path = None
        if code.startswith("R"):
            old_path = safe_relative_path(parts[1])
            path = safe_relative_path(parts[2])
            status = "renamed"
        else:
            path = safe_relative_path(parts[1])
            status = {"A": "added", "D": "deleted", "M": "modified"}.get(code[0], "modified")
        baseline_path = old_path or path
        baseline = decode_text(git_file(repo, base, baseline_path), f"{base}:{baseline_path}")
        proposed = decode_text(git_file(repo, head, path), f"{head}:{path}")
        if proposed is None:
            precondition = {"kind": "absent"}
        else:
            precondition = {"kind": "exists", "sha256": text_sha256(proposed)}
        files.append(make_file_record(path, status, baseline, proposed, precondition, story, old_path))

    bundle = {
        "schemaVersion": SCHEMA_VERSION,
        "bundleId": args.bundle_id or f"git-{head[:12]}-{uuid.uuid4().hex[:8]}",
        "bundleDigest": "",
        "mode": "git-review",
        "createdAt": utc_now(),
        "title": args.title,
        "question": args.question or "What changed, why, and what should be edited before applying?",
        "summary": args.summary or f"Exact comparison of {base[:12]} to {head[:12]}.",
        "comparison": {"repository": repo.name, "base": base, "head": head, "policy": "exact-commits"},
        "sources": story.get("sources", []),
        "story": {"claims": story.get("claims", [])},
        "files": files,
    }
    write_json(Path(args.output), finalize_bundle(bundle))


def command_reflection_bundle(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    spec = load_json(Path(args.proposal))
    story = {"claims": spec.get("claims", []), "fileIntents": spec.get("fileIntents", {})}
    files: list[dict] = []
    for proposal in spec.get("files", []):
        path = safe_relative_path(str(proposal.get("path", "")))
        target = resolve_target(repo, path)
        baseline = canonical_text(target.read_text(encoding="utf-8")) if target.exists() else None
        proposed = proposal.get("proposedText")
        replacements = proposal.get("replacements")
        if proposed is None and replacements is not None:
            if baseline is None:
                raise WorkbenchError(f"replacements require an existing UTF-8 target: {path}")
            proposed = baseline
            for replacement in replacements:
                old = replacement.get("old")
                new = replacement.get("new")
                if not isinstance(old, str) or not isinstance(new, str) or not old:
                    raise WorkbenchError(f"Each replacement needs non-empty old text and string new text: {path}")
                if proposed.count(canonical_text(old)) != 1:
                    raise WorkbenchError(f"Replacement old text must match exactly once: {path}")
                proposed = proposed.replace(canonical_text(old), canonical_text(new), 1)
        if proposed is not None and not isinstance(proposed, str):
            raise WorkbenchError(f"proposedText must be text or null: {path}")
        proposed = canonical_text(proposed) if isinstance(proposed, str) else None
        precondition = {"kind": "absent"} if baseline is None else {"kind": "exists", "sha256": text_sha256(baseline)}
        files.append(make_file_record(path, str(proposal.get("status", "modified")), baseline, proposed, precondition, story))
    try:
        revision = exact_commit(repo, "HEAD")
    except WorkbenchError:
        revision = None
    bundle = {
        "schemaVersion": SCHEMA_VERSION,
        "bundleId": spec.get("bundleId") or f"reflection-{uuid.uuid4().hex[:12]}",
        "bundleDigest": "",
        "mode": "reflection",
        "createdAt": spec.get("createdAt") or utc_now(),
        "title": spec.get("title", "Reflection rule proposal"),
        "question": spec.get("question", "What should change so this event does not repeat?"),
        "summary": spec.get("summary", "A reflection-derived proposal; no workspace rule has been changed."),
        "comparison": {
            "repository": repo.name,
            "workspaceRevision": revision,
            "event": spec.get("event", "User-provided reflection event"),
            "sourceMode": spec.get("sourceMode", "event-description"),
        },
        "sources": spec.get("sources", []),
        "story": {"claims": spec.get("claims", [])},
        "files": files,
    }
    write_json(Path(args.output), finalize_bundle(bundle))


def static_fallback(bundle: dict) -> str:
    pieces = [
        "<section class=\"static-report\">",
        f"<h1>{html.escape(bundle['title'])}</h1>",
        f"<p>{html.escape(bundle['summary'])}</p>",
        "<h2>Files and exact diff</h2>",
    ]
    for file in bundle["files"]:
        pieces.append(f"<h3>{html.escape(file['path'])}</h3>")
        pieces.append(f"<p>{html.escape(file.get('intent', ''))}</p>")
        pieces.append(f"<pre>{html.escape(file['diff']['rawUnified'])}</pre>")
    pieces.append("</section>")
    return "".join(pieces)


def safe_script_json(value: object) -> str:
    return canonical_json(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def substitute_shell(shell: str, values: dict[str, str]) -> str:
    """Replace tokens found in the original shell exactly once without rescanning values."""
    for token in values:
        if shell.count(token) != 1:
            raise WorkbenchError(f"Template token must appear exactly once: {token}")
    pattern = re.compile("|".join(re.escape(token) for token in sorted(values, key=len, reverse=True)))
    return pattern.sub(lambda match: values[match.group(0)], shell)


def command_render(args: argparse.Namespace) -> None:
    bundle = load_json(Path(args.bundle))
    validate_bundle(bundle)
    html_root = Path(__file__).resolve().parent.parent
    shell = (html_root / "assets" / "workbench.html").read_text(encoding="utf-8")
    css = (html_root / "assets" / "workbench.css").read_text(encoding="utf-8")
    js = (html_root / "assets" / "workbench.js").read_text(encoding="utf-8")
    shared_root = html_root.parent / "shared"
    css = (shared_root / "components.css").read_text(encoding="utf-8") + "\n" + css
    js = (shared_root / "components.js").read_text(encoding="utf-8") + "\n" + js
    rendered = substitute_shell(shell, {
        "__WORKBENCH_TITLE__": html.escape(bundle["title"]),
        "__WORKBENCH_CSS__": css,
        "__WORKBENCH_DATA__": safe_script_json(bundle),
        "__WORKBENCH_JS__": js,
        "__WORKBENCH_FALLBACK__": static_fallback(bundle),
    })
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def resolve_target(root: Path, relative: str) -> Path:
    relative = safe_relative_path(relative)
    root = root.resolve()
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    resolved = candidate.resolve(strict=False)
    try:
        common = Path(os.path.commonpath([str(root), str(resolved)]))
    except ValueError as exc:
        raise WorkbenchError(f"Target escapes root: {relative}") from exc
    if common != root:
        raise WorkbenchError(f"Target escapes root: {relative}")
    return resolved


def validate_response(bundle: dict, response: dict) -> None:
    required = {"schemaVersion", "bundleId", "bundleDigest", "files", "comments", "decisions"}
    if not isinstance(response, dict) or set(response) != required:
        raise WorkbenchError("Response must contain exactly the schema fields")
    if response["schemaVersion"] != SCHEMA_VERSION:
        raise WorkbenchError("Unsupported response schema")
    if response["bundleId"] != bundle["bundleId"] or response["bundleDigest"] != bundle["bundleDigest"]:
        raise WorkbenchError("Response does not belong to this exact bundle")
    for field in ("files", "comments", "decisions"):
        if not isinstance(response[field], list) or not all(isinstance(item, dict) for item in response[field]):
            raise WorkbenchError(f"{field} must be an array of objects")
    for item in response["files"]:
        if set(item) != {"path", "accepted", "desiredText"}:
            raise WorkbenchError("Each file requires path, accepted and explicit desiredText")
        if not isinstance(item["path"], str) or not item["path"]:
            raise WorkbenchError("File path must be a non-empty string")
        if type(item["accepted"]) is not bool:
            raise WorkbenchError("accepted must be a boolean")
        if item["desiredText"] is not None and not isinstance(item["desiredText"], str):
            raise WorkbenchError("desiredText must be text or explicit null")


def prepare_application(repo: Path, bundle: dict, response: dict) -> tuple[list[dict], str]:
    validate_bundle(bundle)
    validate_response(bundle, response)
    bundle_files = {file["path"]: file for file in bundle["files"]}
    seen: set[str] = set()
    prepared: list[dict] = []
    stale: list[dict] = []
    patches: list[str] = []
    for item in response["files"]:
        path = safe_relative_path(str(item.get("path", "")))
        if path in seen or path not in bundle_files:
            raise WorkbenchError(f"Unknown or duplicate response file: {path}")
        seen.add(path)
        if not item.get("accepted", False):
            continue
        desired = item.get("desiredText")
        if desired is not None and not isinstance(desired, str):
            raise WorkbenchError(f"desiredText must be text or null: {path}")
        desired = canonical_text(desired) if isinstance(desired, str) else None
        target = resolve_target(repo, path)
        current = canonical_text(target.read_text(encoding="utf-8")) if target.exists() else None
        expected = bundle_files[path]["applyPrecondition"]
        actual = {"kind": "absent"} if current is None else {"kind": "exists", "sha256": text_sha256(current)}
        if actual != expected:
            stale.append({"path": path, "expected": expected, "actual": actual})
        prepared.append({"path": path, "target": target, "current": current, "desired": desired})
        patches.append(make_unified(path, current, desired))
    if stale:
        raise WorkbenchError("Stale targets; nothing was written:\n" + json.dumps(stale, ensure_ascii=False, indent=2))
    return prepared, "".join(patches)


def command_check(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    bundle = load_json(Path(args.bundle))
    response = load_json(Path(args.response))
    prepared, patch = prepare_application(repo, bundle, response)
    result = {"status": "ready", "files": [item["path"] for item in prepared], "patch": patch}
    print(json.dumps(result, ensure_ascii=False, indent=2))


@contextmanager
def application_lock(repo: Path):
    # A stable lock file is never unlinked: simultaneous apply processes share its inode.
    with (repo / ".work-review.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0); handle.write(b"0"); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def assert_unchanged(repo: Path, item: dict) -> None:
    target = resolve_target(repo, item["path"])
    if target != item["target"]:
        raise WorkbenchError(f"Target location changed: {item['path']}")
    current = canonical_text(target.read_text(encoding="utf-8")) if target.exists() else None
    if current != item["current"]:
        raise WorkbenchError(f"Target changed before write: {item['path']}")


def command_apply(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    bundle = load_json(Path(args.bundle))
    response = load_json(Path(args.response))
    # Invalid responses fail before acquiring the lock or creating any files.
    validate_bundle(bundle)
    validate_response(bundle, response)
    staged = []
    applied = []
    backup = None
    with application_lock(repo):
        prepared, patch = prepare_application(repo, bundle, response)
        try:
            for item in prepared:
                target, desired = item["target"], item["desired"]
                temp = None
                if desired is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    handle, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".review", dir=target.parent)
                    os.close(handle)
                    temp = Path(name)
                    staged.append((item, temp))
                    temp.write_bytes(desired.encode("utf-8"))
                else:
                    staged.append((item, None))
            # Reject edits made during staging before any target is touched.
            for item, _ in staged:
                assert_unchanged(repo, item)
            if staged:
                backup = Path(tempfile.mkdtemp(prefix="work-review-backup-"))
                for index, (item, _) in enumerate(staged):
                    if item["target"].exists():
                        (backup / str(index)).write_bytes(item["target"].read_bytes())
                write_json(backup / "manifest.json", {
                    "root": str(repo), "files": [
                        {"path": item["path"], "backup": str(index) if item["current"] is not None else None}
                        for index, (item, _) in enumerate(staged)
                    ]
                })
            for item, temp in staged:
                assert_unchanged(repo, item)
                if item["desired"] is None:
                    if item["target"].exists():
                        item["target"].unlink()
                else:
                    os.replace(temp, item["target"])
                applied.append(item["path"])
            for item, _ in staged:
                target = item["target"]
                actual = canonical_text(target.read_text(encoding="utf-8")) if target.exists() else None
                if actual != item["desired"]:
                    raise WorkbenchError(f"Post-write verification failed: {item['path']}")
        except (WorkbenchError, OSError) as exc:
            raise WorkbenchError(f"{exc}; applied files: {applied}; recovery copy: {backup}") from exc
        finally:
            for _, temp in staged:
                if temp is not None and temp.exists():
                    temp.unlink()
    print(json.dumps({"status": "applied", "files": applied, "patch": patch,
                      "recoveryCopy": str(backup) if backup else None}, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    git = commands.add_parser("git-bundle", help="Build a bundle from exact Git commits")
    git.add_argument("--repo", required=True)
    git.add_argument("--base", required=True)
    git.add_argument("--head", required=True)
    git.add_argument("--story")
    git.add_argument("--output", required=True)
    git.add_argument("--title", required=True)
    git.add_argument("--question")
    git.add_argument("--summary")
    git.add_argument("--bundle-id")
    git.set_defaults(func=command_git_bundle)

    reflection = commands.add_parser("reflection-bundle", help="Build a reflection proposal bundle")
    reflection.add_argument("--repo", required=True)
    reflection.add_argument("--proposal", required=True)
    reflection.add_argument("--output", required=True)
    reflection.set_defaults(func=command_reflection_bundle)

    render = commands.add_parser("render", help="Render an offline single-file HTML workbench")
    render.add_argument("--bundle", required=True)
    render.add_argument("--output", required=True)
    render.set_defaults(func=command_render)

    check = commands.add_parser("check", help="Validate response, path boundary, and stale preconditions")
    check.add_argument("--repo", required=True)
    check.add_argument("--bundle", required=True)
    check.add_argument("--response", required=True)
    check.set_defaults(func=command_check)

    apply = commands.add_parser("apply", help="Explicitly apply an accepted review response")
    apply.add_argument("--repo", required=True)
    apply.add_argument("--bundle", required=True)
    apply.add_argument("--response", required=True)
    apply.set_defaults(func=command_apply)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
        return 0
    except WorkbenchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
