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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import webbrowser
import uuid


SCHEMA_VERSION = "1.0"
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


class WorkbenchError(RuntimeError):
    pass


class StaleTargets(WorkbenchError):
    pass


def canonical_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def text_lines(value: str) -> list[str]:
    return re.findall(r"[^\n]*\n|[^\n]+\Z", canonical_text(value))


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
    before = text_lines(baseline or "")
    after = text_lines(proposed or "")
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
            lineterm="\n",
        )
    )


def claim_applies(claim: dict, added: list[str], removed: list[str], new_start: int, new_end: int, old_start: int, old_end: int) -> bool:
    anchor = claim.get("anchorText")
    if isinstance(anchor, str) and anchor.strip():
        return any(anchor in line for line in added + removed)
    line_range = claim.get("lineRange")
    if isinstance(line_range, dict):
        start = line_range.get("start")
        end = line_range.get("end")
        if isinstance(start, int) and isinstance(end, int):
            first, last = (old_start, old_end) if not added else (new_start, new_end)
            return not (last < start or first > end)
    return True


def parse_hunks(raw_diff: str, path: str, claims: list[dict]) -> list[dict]:
    hunks: list[dict] = []
    current: dict | None = None
    old_line = new_line = 0
    for raw in raw_diff.split("\n"):
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
                "lines": [],
            }
            hunks.append(current)
            continue
        if current is None or not raw:
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
    for hunk in hunks:
        added = [line["text"] for line in hunk["lines"] if line["kind"] == "add"]
        removed = [line["text"] for line in hunk["lines"] if line["kind"] == "remove"]
        new_start = hunk["newStart"]
        new_end = new_start + max(0, hunk["newCount"] - 1)
        old_start = hunk["oldStart"]
        old_end = old_start + max(0, hunk["oldCount"] - 1)
        ids = [
            str(claim["id"]) for claim in claims
            if claim.get("id") is not None and claim_applies(claim, added, removed, new_start, new_end, old_start, old_end)
        ]
        hunk["claimIds"] = ids
        hunk["unexplained"] = not bool(ids)
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
    blocks = []
    for file in bundle["files"]:
        path = safe_relative_path(file.get("path", ""))
        if path in seen:
            raise WorkbenchError(f"Duplicate file in bundle: {path}")
        seen.add(path)
        if "applyPrecondition" not in file or "diff" not in file:
            raise WorkbenchError(f"Incomplete file record: {path}")
        if "reviewBlocks" in file:
            validate_review_blocks(file, bundle["story"].get("claims", []), bundle["sources"])
            blocks.extend(file["reviewBlocks"])
    validate_story_blocks({"blocks": blocks})


def validate_review_blocks(file: dict, claims: list[dict], sources: list[dict]) -> None:
    path = file["path"]
    before = text_lines((file.get("baseline") or {}).get("text", ""))
    after = text_lines((file.get("proposed") or {}).get("text", ""))
    claim_ids = {claim.get("id") for claim in claims if isinstance(claim, dict)}
    source_ids = {source.get("id") for source in sources if isinstance(source, dict)}
    old_cursor = new_cursor = 0
    seen: set[str] = set()
    blocks = file["reviewBlocks"]
    if not isinstance(blocks, list) or not blocks:
        raise WorkbenchError(f"reviewBlocks must be a non-empty array: {path}")
    for block in blocks:
        if not isinstance(block, dict) or block.get("filePath") != path:
            raise WorkbenchError(f"Invalid review block: {path}")
        for field in ("id", "title", "reason", "claimId"):
            if not isinstance(block.get(field), str) or not block[field].strip():
                raise WorkbenchError(f"Review block needs {field}: {path}")
        if block["id"] in seen:
            raise WorkbenchError(f"Duplicate review block id: {path}: {block['id']}")
        seen.add(block["id"])
        if block["claimId"] not in claim_ids:
            raise WorkbenchError(f"Unknown block claimId: {path}: {block['id']}")
        evidence = block.get("evidenceIds")
        if not isinstance(evidence, list) or any(not isinstance(value, str) or value not in source_ids for value in evidence):
            raise WorkbenchError(f"Unknown or missing block evidenceIds: {path}: {block['id']}")
        for field, count, cursor in (("oldRange", len(before), old_cursor), ("newRange", len(after), new_cursor)):
            span = block.get(field)
            if (not isinstance(span, list) or len(span) != 2
                    or any(type(value) is not int for value in span)
                    or not cursor <= span[0] <= span[1] <= count):
                raise WorkbenchError(f"Invalid or overlapping {field}: {path}: {block['id']}")
        old_start, old_end = block["oldRange"]
        new_start, new_end = block["newRange"]
        if before[old_cursor:old_start] != after[new_cursor:new_start]:
            raise WorkbenchError(f"Uncovered change before review block: {path}: {block['id']}")
        if before[old_start:old_end] == after[new_start:new_end]:
            raise WorkbenchError(f"Review block contains no change: {path}: {block['id']}")
        old_cursor, new_cursor = old_end, new_end
    if before[old_cursor:] != after[new_cursor:]:
        raise WorkbenchError(f"Uncovered change after review blocks: {path}")


def story_for_path(story: dict, path: str) -> tuple[list[dict], str]:
    claims: list[dict] = []
    intents = story.get("fileIntents", {})
    for claim in story.get("claims", []):
        paths = claim.get("filePaths", [])
        if claim.get("filePath") == path or path in paths:
            claims.append(claim)
    return claims, str(intents.get(path, "Review the exact change and its evidence."))


def make_file_record(
    path: str,
    status: str,
    baseline: str | None,
    proposed: str | None,
    precondition: dict,
    story: dict,
    old_path: str | None = None,
) -> dict:
    claims, intent = story_for_path(story, path)
    raw = make_unified(path, baseline, proposed)
    record = {
        "path": path,
        "oldPath": old_path,
        "status": status,
        "textKind": "utf-8",
        "editable": True,
        "intent": intent,
        "baseline": snapshot(baseline),
        "proposed": snapshot(proposed),
        "applyPrecondition": precondition,
        "diff": {"rawUnified": raw, "hunks": parse_hunks(raw, path, claims)},
    }
    blocks = [block for block in story.get("blocks", []) if block.get("filePath") == path]
    if blocks:
        record["reviewBlocks"] = blocks
        validate_review_blocks(record, story.get("claims", []), story.get("sources", []))
    return record


def load_story(path: Path | None) -> dict:
    if path is None:
        return {"claims": [], "fileIntents": {}, "sources": []}
    value = load_json(path)
    value.setdefault("claims", [])
    value.setdefault("fileIntents", {})
    value.setdefault("sources", [])
    return value


def validate_block_paths(story: dict, files: list[dict]) -> None:
    paths = {file["path"] for file in files}
    for block in story.get("blocks", []):
        if block.get("filePath") not in paths:
            raise WorkbenchError(f"Review block has no changed target: {block.get('filePath')}")


def validate_story_blocks(story: dict) -> None:
    blocks = story.get("blocks", [])
    if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
        raise WorkbenchError("Review blocks must be an array of objects")
    ids = [block.get("id") for block in blocks]
    if any(not isinstance(value, str) or not value.strip() for value in ids) or len(ids) != len(set(ids)):
        raise WorkbenchError("Review block ids must be unique and non-empty")
    for block in blocks:
        related = block.get("dependsOn", [])
        if not isinstance(related, list) or any(value not in ids or value == block["id"] for value in related):
            raise WorkbenchError(f"Invalid related review blocks: {block['id']}")
        if "effect" in block and not isinstance(block["effect"], str):
            raise WorkbenchError(f"Invalid review block effect: {block['id']}")


def command_git_bundle(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    base = exact_commit(repo, args.base)
    head = exact_commit(repo, args.head)
    story = load_story(Path(args.story) if args.story else None)
    validate_story_blocks(story)
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

    validate_block_paths(story, files)
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
    story = {"claims": spec.get("claims", []), "fileIntents": spec.get("fileIntents", {}),
             "blocks": spec.get("blocks", []), "sources": spec.get("sources", [])}
    validate_story_blocks(story)
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
    validate_block_paths(story, files)
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


ASSETS = Path(__file__).resolve().parent.parent / "assets"


def render_page(bundle: dict, session: dict | None = None) -> str:
    shell = (ASSETS / "workbench.html").read_text(encoding="utf-8")
    if session is None:
        styles = "<style>" + (ASSETS / "workbench.css").read_text(encoding="utf-8") + "</style>"
        scripts = "<script>" + (ASSETS / "workbench.js").read_text(encoding="utf-8") + "</script>"
    else:
        styles = '<link rel="stylesheet" href="/assets/workbench.css">'
        scripts = (f'<script id="live-session" type="application/json">{safe_script_json(session)}</script>'
                   '<script src="/assets/live.js"></script><script src="/assets/workbench.js"></script>')
    return substitute_shell(shell, {
        "__WORKBENCH_TITLE__": html.escape(bundle["title"]),
        "__WORKBENCH_CSS__": styles,
        "__WORKBENCH_DATA__": safe_script_json(bundle),
        "__WORKBENCH_JS__": scripts,
        "__WORKBENCH_FALLBACK__": static_fallback(bundle),
    })


def command_render(args: argparse.Namespace) -> None:
    bundle = load_json(Path(args.bundle))
    validate_bundle(bundle)
    rendered = render_page(bundle)
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
        raise StaleTargets("Stale targets; nothing was written:\n" + json.dumps(stale, ensure_ascii=False, indent=2))
    return prepared, "".join(patches)


def command_check(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    bundle = load_json(Path(args.bundle))
    response = load_json(Path(args.response))
    print(json.dumps(check_application(repo, bundle, response), ensure_ascii=False, indent=2))


def check_application(repo: Path, bundle: dict, response: dict) -> dict:
    prepared, patch = prepare_application(repo, bundle, response)
    return {"status": "ready", "files": [item["path"] for item in prepared], "patch": patch}


@contextmanager
def file_lock(path: Path):
    # A stable lock file is never unlinked: simultaneous apply processes share its inode.
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if not os.fstat(handle.fileno()).st_size:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
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
    print(json.dumps(apply_application(repo, bundle, response), ensure_ascii=False, indent=2))


def apply_application(repo: Path, bundle: dict, response: dict) -> dict:
    # Invalid responses fail before acquiring the lock or creating any files.
    validate_bundle(bundle)
    validate_response(bundle, response)
    staged = []
    applied = []
    backup = None
    with file_lock(repo / ".work-review.lock"):
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
    return {"status": "applied", "files": applied, "patch": patch,
            "recoveryCopy": str(backup) if backup else None}


class RevisionConflict(WorkbenchError):
    pass


def validate_draft(state: dict):
    if not isinstance(state, dict):
        raise WorkbenchError("Expected a review draft")
    for key in ("decisions", "blockEdits", "edits", "themeDrafts"):
        pairs = state.get(key)
        if not isinstance(pairs, list) or not all(
            isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
            and (isinstance(pair[1], str) or (key == "edits" and pair[1] is None)) for pair in pairs
        ):
            raise WorkbenchError(f"Invalid review draft field: {key}")
    for key, kind in (("comments", dict), ("log", dict), ("manualMarks", str)):
        if not isinstance(state.get(key), list) or not all(isinstance(v, kind) for v in state[key]):
            raise WorkbenchError(f"Invalid review draft field: {key}")
    for key in ("editTarget", "commentTarget", "lastEditUndo"):
        if key not in state or state[key] is not None and not isinstance(state[key], dict):
            raise WorkbenchError(f"Invalid review draft field: {key}")
    if not all(isinstance(state.get(key), str) for key in ("commentDraft", "editDraft")):
        raise WorkbenchError("Invalid editor draft")


class ReviewSession:
    def __init__(self, repo: Path, bundle_path: Path, state_path: Path):
        self.repo = repo.resolve(strict=True)
        if not self.repo.is_dir():
            raise WorkbenchError("Review repository must be a directory")
        self.bundle_path = bundle_path.resolve(strict=True)
        self.bundle = load_json(self.bundle_path)
        validate_bundle(self.bundle)
        self.state_path = state_path.parent.resolve() / state_path.name
        self.lock = self.state_path.with_name(self.state_path.name + ".lock")
        self.identity = {
            "bundleDigest": self.bundle["bundleDigest"],
            "sessionId": text_sha256(canonical_json([str(self.repo), str(self.state_path.resolve())])),
        }
        self.check_paths()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot()  # Refuse corrupt or unrelated state before advertising the URL.

    def check_paths(self, *, saving=False):
        reserved = [self.state_path, self.lock, self.repo / ".work-review.lock", self.bundle_path]
        if any(path.is_symlink() for path in reserved[:3]):
            raise WorkbenchError("A session file or lock is a symbolic link")
        reserved = {path.resolve() for path in reserved}
        if len(reserved) != 4:
            raise WorkbenchError("Bundle, state and locks must use different paths")
        for file in self.bundle["files"]:
            target = (self.repo / file["path"]).resolve() if saving else resolve_target(self.repo, file["path"])
            if target in reserved or ".git" in PurePosixPath(file["path"]).parts or (
                target.is_relative_to(self.repo) and ".git" in target.relative_to(self.repo).parts
            ):
                raise WorkbenchError(f"Reserved review target: {file['path']}")

    def read(self) -> dict:
        if not self.state_path.exists():
            return {**self.identity, "revision": 0, "state": None, "response": None, "applied": None}
        doc = load_json(self.state_path)
        if any(doc.get(key) != value for key, value in self.identity.items()):
            raise WorkbenchError("Saved state belongs to a different bundle or review session")
        if type(doc.get("revision")) is not int or not {"state", "response", "applied"} <= doc.keys():
            raise WorkbenchError("Invalid saved review state")
        validate_draft(doc["state"])
        validate_response(self.bundle, doc["response"])
        return doc

    def snapshot(self) -> dict:
        return {key: value for key, value in self.read().items() if key != "response"}

    def persist(self, doc: dict):
        fd, name = tempfile.mkstemp(prefix=".review-state-", dir=self.state_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp:
                json.dump(doc, temp, ensure_ascii=False, allow_nan=False)
                temp.write("\n")
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(name, self.state_path)
        finally:
            Path(name).unlink(missing_ok=True)

    def mutate(self, action: str, payload: dict) -> dict:
        if not isinstance(payload, dict) or type(payload.get("revision")) is not int:
            raise WorkbenchError("Expected a JSON object with an integer revision")
        if any(payload.get(key) != value for key, value in self.identity.items()):
            raise RevisionConflict("Review session changed. Reopen the original review.")
        self.check_paths(saving=action == "state")
        with file_lock(self.lock):
            doc = self.read()
            if doc["revision"] != payload["revision"]:
                # Retry a committed PUT whose reply was lost; different content still conflicts.
                if (action == "state" and doc["revision"] == payload["revision"] + 1
                        and doc["state"] == payload.get("state") and doc["response"] == payload.get("response")):
                    return {"revision": doc["revision"]}
                raise RevisionConflict("另一个窗口已保存新版本。请保留本页草稿，再载入最新版本。")
            if action == "state":
                state = payload.get("state")
                validate_draft(state)
                response = payload.get("response")
                validate_response(self.bundle, response)
                paths = [entry["path"] for entry in response["files"]]
                if sorted(paths) != sorted(file["path"] for file in self.bundle["files"]):
                    raise WorkbenchError("Saved response must cover the bundle files exactly")
                doc.update(revision=doc["revision"] + 1, state=state, response=response)
                self.persist(doc)
                return {"revision": doc["revision"]}

            response = doc["response"]
            if not response or not response["decisions"] or any(
                item.get("decision") not in ("accepted", "rejected") for item in response["decisions"]
            ):
                raise WorkbenchError("仍有待定块，或尚未保存审阅决定。")
            current_bundle = load_json(self.bundle_path)
            validate_bundle(current_bundle)
            if current_bundle["bundleDigest"] != self.identity["bundleDigest"]:
                raise RevisionConflict("Bundle changed on disk. Open a new review.")
            if action == "check":
                return check_application(self.repo, current_bundle, response)
            if doc["applied"] and doc["applied"]["revision"] == doc["revision"]:
                # A retried request gets its original receipt only while its result still exists.
                for file in response["files"]:
                    if not file["accepted"]:
                        continue
                    path = resolve_target(self.repo, file["path"])
                    current = path.read_text(encoding="utf-8") if path.exists() else None
                    desired = file["desiredText"]
                    if current != (canonical_text(desired) if desired is not None else None):
                        raise StaleTargets(f"Target changed after application: {file['path']}; "
                                           f"recovery copy: {doc['applied']['recoveryCopy']}")
                return doc["applied"]
            result = apply_application(self.repo, current_bundle, response)
            receipt = {key: result[key] for key in ("status", "files", "recoveryCopy")}
            receipt["revision"] = doc["revision"]
            doc["applied"] = receipt
            try:
                self.persist(doc)
            except OSError as exc:
                receipt["warning"] = f"文件已写入，应用记录保存失败：{exc}"
            return receipt


class ReviewServer(ThreadingHTTPServer):
    # Let in-flight writes finish when the user stops the server.
    daemon_threads = False


class ReviewHandler(BaseHTTPRequestHandler):
    timeout = 10
    assets = {"workbench.css": "text/css", "workbench.js": "text/javascript", "live.js": "text/javascript"}

    def reply(self, status, value, media="application/json"):
        body = (json.dumps(value, ensure_ascii=False).encode() if media == "application/json" else value)
        self.send_response(status)
        self.send_header("Content-Type", media + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, mutate=False):
        port = self.server.server_port
        hosts = self.headers.get_all("Host") or []
        suffix = f":{port}" if port != 80 else ""
        if len(hosts) == 1 and hosts[0] in ("127.0.0.1" + suffix, "localhost" + suffix):
            if not mutate or self.headers.get_all("Origin") == ["http://" + hosts[0]]:
                return True
        self.reply(403, {"error": "Expected the local review origin"})
        return False

    def do_GET(self):
        if not self.allowed():
            return
        session = self.server.session
        try:
            if self.path == "/":
                self.reply(200, render_page(session.bundle, session.snapshot()).encode(), "text/html")
            elif self.path == "/api/session":
                self.reply(200, session.snapshot())
            elif self.path in {"/assets/" + name for name in self.assets}:
                name = self.path[len("/assets/"):]
                self.reply(200, (ASSETS / name).read_bytes(), self.assets[name])
            else:
                self.reply(404, {"error": "Unknown path"})
        except (WorkbenchError, OSError, ValueError) as exc:
            self.reply(500, {"error": str(exc)})

    def mutate(self):
        if not self.allowed(mutate=True):
            return
        routes = {("PUT", "/api/state"): "state", ("POST", "/api/check"): "check", ("POST", "/api/apply"): "apply"}
        action = routes.get((self.command, self.path))
        if action is None:
            self.reply(404, {"error": "Unknown action"})
            return
        if self.headers.get_content_type() != "application/json":
            self.reply(415, {"error": "Expected application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= 128 * 1024 * 1024:
                self.reply(413, {"error": "Invalid JSON request size"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = self.server.session.mutate(action, payload)
        except RevisionConflict as exc:
            self.reply(409, {"error": str(exc), "code": "revision_conflict"})
        except StaleTargets as exc:
            self.reply(409, {"error": str(exc), "code": "stale_targets"})
        except (WorkbenchError, ValueError, RecursionError) as exc:
            self.reply(400, {"error": str(exc)})
        except OSError as exc:
            self.reply(500, {"error": str(exc)})
        else:
            self.reply(200, result)

    do_PUT = mutate
    do_POST = mutate


def command_serve(args: argparse.Namespace):
    bundle = Path(args.bundle)
    state = Path(args.state) if args.state else bundle.with_suffix(".state.json")
    try:
        session = ReviewSession(Path(args.repo), bundle, state)
        if not 0 <= args.port <= 65535:
            raise WorkbenchError("Port must be between 0 and 65535")
        with ReviewServer(("127.0.0.1", args.port), ReviewHandler) as server:
            server.session = session
            url = f"http://127.0.0.1:{server.server_port}/"
            print(url, flush=True)
            if args.open:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except OSError as exc:
        raise WorkbenchError(str(exc)) from exc


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

    serve = commands.add_parser("serve", help="Open a local, autosaving review workbench")
    serve.add_argument("--repo", required=True)
    serve.add_argument("--bundle", required=True)
    serve.add_argument("--state")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--open", action=argparse.BooleanOptionalAction, default=True)
    serve.set_defaults(func=command_serve)

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
