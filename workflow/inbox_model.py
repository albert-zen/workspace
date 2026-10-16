#!/usr/bin/env python3
"""Canonical Personal Inbox model and guarded atomic writer."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Iterable
from urllib.parse import urlsplit


SECTION_NAMES = ("Needs you", "In progress", "Waiting", "Done")
EMPTY_MARKER = "_暂无事项。_"
ENTRY_RE = re.compile(r"^- \[([ xX])\] (.+?) — (.+)$")
ASSOCIATION_RE = re.compile(r" · \[(Issue|Work)\]\(([^)\s]+)\)")
REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTITY_RE = re.compile(r" <!-- work:([0-9a-f]{32}) -->$")
PIN_MARKER = " <!-- pinned -->"


class InboxError(RuntimeError):
    def __init__(self, message: str, *, code: str = "invalid_inbox", field: str | None = None, line: int | None = None, action: str = "修正标出的内容后再保存。") -> None:
        super().__init__(message)
        self.code, self.field, self.line, self.action = code, field, line, action


class StaleSourceError(InboxError):
    def __init__(self) -> None:
        super().__init__(
            "Inbox 在你编辑期间发生了变化。未写入任何内容。",
            code="stale_source",
            action="保留当前输入，重新加载最新 Inbox 后再核对。",
        )


@dataclass(frozen=True)
class Entry:
    title: str
    summary: str
    issue_url: str | None = None
    work_path: str | None = None
    source_line: int | None = field(default=None, compare=False)
    item_id: str | None = None
    pinned: bool = False


@dataclass(frozen=True)
class InboxDocument:
    sections: dict[str, list[Entry]]
    newline: str = field(default="\n", compare=False)
    final_newline: str = field(default="\n", compare=False)


def revision_for(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _fail(message: str, *, line: int | None = None, field: str | None = None, code: str = "invalid_inbox") -> None:
    raise InboxError(message, line=line, field=field, code=code)


def _validate_issue(url: str, *, line: int | None = None, field: str = "issueUrl") -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None or parsed.password is not None:
        _fail("Issue 必须是不含凭据的完整 HTTPS URL。", line=line, field=field, code="invalid_issue")
    return url


def _work_path_from_markdown(raw: str, inbox_path: Path, repo_root: Path, allowed: set[str], line: int) -> str:
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or raw.startswith(("/", "\\")) or parsed.query or parsed.fragment:
        _fail("Work 关联必须使用允许目录中的相对 Markdown 路径。", line=line, field="workPath", code="invalid_work")
    try:
        resolved = (inbox_path.parent / parsed.path).resolve(strict=False)
        relative = resolved.relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        _fail("Work 关联不在允许目录中。", line=line, field="workPath", code="invalid_work")
    parts = Path(relative).parts
    valid_shape = (
        len(parts) >= 6
        and parts[0:2] == ("agent-data", "projects")
        and parts[-3] == "work"
        and parts[-2] in {"active", "archive"}
        and parts[-1].lower().endswith(".md")
    )
    if not valid_shape or (resolved.exists() and relative not in allowed):
        _fail("Work 关联不在允许的 active/archive Markdown 文档中。", line=line, field="workPath", code="invalid_work")
    return relative


def _parse_entry(raw: str, section: str, line: int, inbox_path: Path, repo_root: Path, allowed: set[str]) -> Entry:
    identity = IDENTITY_RE.search(raw)
    item_id = identity.group(1) if identity else None
    if identity:
        raw = raw[:identity.start()]
    pinned = raw.endswith(PIN_MARKER)
    if pinned:
        raw = raw[:-len(PIN_MARKER)]
    if raw.count(" — ") != 1:
        _fail("条目必须且只能使用一个“ — ”分隔标题与摘要。", line=line)
    match = ENTRY_RE.fullmatch(raw)
    if not match:
        _fail("无法识别条目；请使用单行 checkbox 条目。", line=line)
    checked, title, tail = match.groups()
    if section == "Done":
        if checked != "x":
            _fail("Done 条目必须使用小写 [x]。", line=line)
    elif checked != " ":
        _fail(f"{section} 条目必须使用 [ ]。", line=line)

    associations = list(ASSOCIATION_RE.finditer(tail))
    summary = tail[: associations[0].start()] if associations else tail
    rebuilt = summary + "".join(item.group(0) for item in associations)
    if rebuilt != tail:
        _fail("关联必须位于摘要末尾，格式为 Issue 后 Work。", line=line)
    kinds = [item.group(1) for item in associations]
    if len(kinds) != len(set(kinds)) or kinds not in ([], ["Issue"], ["Work"], ["Issue", "Work"]):
        _fail("Issue 与 Work 各至多一次，顺序固定为 Issue → Work。", line=line)
    title, summary = title.strip(), summary.strip()
    if any(marker in title or marker in summary for marker in ("<!-- work:", "<!-- pinned")):
        _fail("内部标识只能由工具放在条目末尾。", line=line)
    if not title or not summary:
        _fail("标题和摘要都不能为空。", line=line)
    if " · [" in title or any(ch in title for ch in "\r\n") or any(ch in summary for ch in "\r\n"):
        _fail("标题和摘要必须保持单行。", line=line)
    issue_url = work_path = None
    for item in associations:
        if item.group(1) == "Issue":
            issue_url = _validate_issue(item.group(2), line=line)
        else:
            work_path = _work_path_from_markdown(item.group(2), inbox_path, repo_root, allowed, line)
    return Entry(title, summary, issue_url, work_path, line, item_id, pinned)


def parse_inbox_text(text: str, inbox_path: Path, repo_root: Path, allowed_work_paths: Iterable[str] = ()) -> InboxDocument:
    newline = "\r\n" if "\r\n" in text else "\n"
    final_newline = "\r\n" if text.endswith("\r\n") else "\n" if text.endswith("\n") else ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    if not lines or lines[0] != "# Personal Inbox":
        _fail("首行必须是“# Personal Inbox”。", line=1)
    sections = {name: [] for name in SECTION_NAMES}
    empty: set[str] = set()
    current: str | None = None
    next_section = 0
    seen: dict[str, int] = {}
    identities: set[str] = set()
    allowed = set(allowed_work_paths)
    for number, raw in enumerate(lines[1:], 2):
        if not raw:
            continue
        if raw.startswith("## "):
            name = raw[3:]
            if next_section >= len(SECTION_NAMES) or name != SECTION_NAMES[next_section]:
                _fail(f"章节顺序必须是：{' → '.join(SECTION_NAMES)}。", line=number)
            current, next_section = name, next_section + 1
            continue
        if current is None:
            _fail("内容必须放在四个状态章节中。", line=number)
        if raw == EMPTY_MARKER:
            if sections[current] or current in empty:
                _fail("空章节标记不能与条目混用或重复。", line=number)
            empty.add(current)
            continue
        if raw.strip() != raw or not raw.startswith("- [") or current in empty:
            _fail("章节只允许 canonical 单行条目或唯一空标记。", line=number)
        entry = _parse_entry(raw, current, number, inbox_path, repo_root, allowed)
        if entry.item_id:
            if entry.item_id in identities:
                _fail("事项内部标识重复。", line=number)
            identities.add(entry.item_id)
        normalized = " ".join(entry.title.casefold().split())
        if normalized in seen:
            _fail(f"条目标题重复；首次出现在第 {seen[normalized]} 行。", line=number, field="title")
        seen[normalized] = number
        sections[current].append(entry)
    if next_section != len(SECTION_NAMES):
        _fail(f"缺少“## {SECTION_NAMES[next_section]}”章节。", line=len(lines) + 1)
    for name in SECTION_NAMES:
        if not sections[name] and name not in empty:
            _fail(f"“{name}”为空时必须写“{EMPTY_MARKER}”。", line=len(lines) + 1)
    return InboxDocument(sections, newline, final_newline)


def parse_inbox(path: Path, repo_root: Path, allowed_work_paths: Iterable[str] = ()) -> tuple[InboxDocument, bytes]:
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise InboxError("无法读取 Inbox；请检查文件是否存在且为 UTF-8。", code="read_failed", action="检查 inbox.md 的读取权限和编码后重试。") from exc
    return parse_inbox_text(text, path, repo_root, allowed_work_paths), data


def _validate_api_entry(raw: object, inbox_path: Path, repo_root: Path, allowed: set[str]) -> Entry:
    required = {"title", "summary", "issueUrl", "workPath"}
    if not isinstance(raw, dict) or not required <= set(raw) or set(raw) - required - {"itemId", "pinned"}:
        _fail("条目须包含 title、summary、issueUrl、workPath，可含 itemId 和 pinned。", code="invalid_request")
    if not all(isinstance(raw[key], str) for key in raw if key != "pinned"):
        _fail("条目字段必须是文字。", code="invalid_request")
    if "pinned" in raw and not isinstance(raw["pinned"], bool):
        _fail("pinned 必须是布尔值。", code="invalid_request")
    title, summary = raw["title"].strip(), raw["summary"].strip()
    if not title or not summary or "\n" in title or "\n" in summary or " — " in title or " · [" in summary:
        _fail("标题和摘要不能为空、换行或包含保留分隔符。", field="title")
    issue = _validate_issue(raw["issueUrl"], field="issueUrl") if raw["issueUrl"] else None
    work = raw["workPath"] or None
    if work:
        if work not in allowed:
            _fail("请选择当前可用的 Work 文档。", field="workPath", code="invalid_work")
        try:
            resolved = (repo_root / Path(work)).resolve(strict=True)
            resolved.relative_to((repo_root / "agent-data" / "projects").resolve())
        except (OSError, ValueError):
            _fail("Work 文档已失效或超出允许目录。", field="workPath", code="invalid_work")
    item_id = raw.get("itemId") or None
    if item_id and not re.fullmatch(r"[0-9a-f]{32}", item_id):
        _fail("事项内部标识无效。", code="invalid_request")
    return Entry(title, summary, issue, work, item_id=item_id, pinned=raw.get("pinned", False))


def document_from_payload(payload: object, inbox_path: Path, repo_root: Path, allowed_work_paths: Iterable[str]) -> InboxDocument:
    if not isinstance(payload, dict) or set(payload) != {"sections"} or not isinstance(payload["sections"], list):
        _fail("请求必须且只能包含 sections。", code="invalid_request")
    raw_sections = payload["sections"]
    if len(raw_sections) != len(SECTION_NAMES):
        _fail("请求必须包含固定四个状态章节。", code="invalid_request")
    allowed = set(allowed_work_paths)
    sections: dict[str, list[Entry]] = {}
    seen: set[str] = set()
    identities: set[str] = set()
    for expected, raw in zip(SECTION_NAMES, raw_sections):
        if not isinstance(raw, dict) or set(raw) != {"status", "items"} or raw.get("status") != expected or not isinstance(raw.get("items"), list):
            _fail("状态章节字段或顺序无效。", code="invalid_request")
        items = [_validate_api_entry(item, inbox_path, repo_root, allowed) for item in raw["items"]]
        for item in items:
            if item.item_id:
                if item.item_id in identities:
                    _fail("事项内部标识重复。", code="invalid_request")
                identities.add(item.item_id)
            key = " ".join(item.title.casefold().split())
            if key in seen:
                _fail("条目标题必须全局唯一。", field="title")
            seen.add(key)
        sections[expected] = items
    return InboxDocument(sections)


def serialize_inbox(document: InboxDocument, inbox_path: Path, repo_root: Path) -> str:
    out = ["# Personal Inbox", ""]
    for index, status in enumerate(SECTION_NAMES):
        out.extend([f"## {status}", ""])
        items = ordered_entries(document.sections[status])
        if not items:
            out.append(EMPTY_MARKER)
        else:
            for item in items:
                checked = "x" if status == "Done" else " "
                line = f"- [{checked}] {item.title} — {item.summary}"
                if item.issue_url:
                    line += f" · [Issue]({item.issue_url})"
                if item.work_path:
                    rel = os.path.relpath(repo_root / item.work_path, inbox_path.parent).replace("\\", "/")
                    line += f" · [Work]({rel})"
                if item.pinned:
                    line += PIN_MARKER
                if item.item_id:
                    line += f" <!-- work:{item.item_id} -->"
                out.append(line)
        out.append("")
    return document.newline.join(out[:-1]) + document.final_newline


def ordered_entries(items: list[Entry]) -> list[Entry]:
    """Pins are a persistent state; relative order within each band stays manual."""
    return [item for item in items if item.pinned] + [item for item in items if not item.pinned]


def document_to_api(document: InboxDocument) -> list[dict[str, object]]:
    return [
        {
            "status": status,
            "items": [
                {
                    "title": item.title,
                    "summary": item.summary,
                    "issueUrl": item.issue_url or "",
                    "workPath": item.work_path or "",
                    "pinned": item.pinned,
                    "entryKey": item.item_id or hashlib.sha256((status + "\0" + " ".join(item.title.casefold().split())).encode()).hexdigest()[:16],
                    **({"itemId": item.item_id} if item.item_id else {}),
                }
                for item in ordered_entries(document.sections[status])
            ],
        }
        for status in SECTION_NAMES
    ]


def atomic_write(path: Path, data: bytes, expected_revision: str, *, before_replace: Callable[[], None] | None = None) -> str:
    if not isinstance(expected_revision, str) or not REVISION_RE.fullmatch(expected_revision):
        _fail("expectedRevision 格式无效。", field="expectedRevision", code="invalid_request")
    try:
        current = path.read_bytes()
    except OSError as exc:
        raise InboxError("无法读取当前 Inbox。", code="read_failed", action="检查 inbox.md 的读取权限后重试。") from exc
    if revision_for(current) != expected_revision:
        raise StaleSourceError()
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".inbox-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if before_replace:
            before_replace()
        if revision_for(path.read_bytes()) != expected_revision:
            raise StaleSourceError()
        os.replace(temporary, path)
        temporary = None
        readback = path.read_bytes()
        if readback != data:
            raise InboxError("Inbox 写入后校验失败。", code="write_failed", action="检查磁盘状态并从 Git 恢复后重试。")
        return revision_for(readback)
    except StaleSourceError:
        raise
    except InboxError:
        raise
    except OSError as exc:
        raise InboxError("无法写入 Inbox；原文件未被部分覆盖。", code="write_failed", action="检查 inbox.md 的写入权限和磁盘状态后重试。") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
