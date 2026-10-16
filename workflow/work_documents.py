#!/usr/bin/env python3
"""Allowlisted Work document discovery and safe Markdown AST parsing."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit


class WorkDocumentError(RuntimeError):
    def __init__(self, message: str, *, code: str = "work_not_found") -> None:
        super().__init__(message)
        self.code = code


HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
LIST_RE = re.compile(r"^(\s*)([-+*]|\d+[.)])\s+(.+)$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
INLINE_RE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|(?<!\*)\*[^*]+\*(?!\*)|\[[^\]]+\]\([^)]+\))")


def _open_fence(line: str) -> tuple[str, str] | None:
    match = FENCE_RE.match(line)
    if not match:
        return None
    marker, info = match.groups()
    if marker[0] == "`" and "`" in info:
        return None
    return marker, info.strip()


def _closes_fence(line: str, marker: str) -> bool:
    return re.fullmatch(r" {0,3}" + re.escape(marker[0]) +
                        "{" + str(len(marker)) + r",}[ \t]*", line) is not None


def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text.casefold()).strip("-") or "section"
    candidate, number = base, 2
    while candidate in used:
        candidate, number = f"{base}-{number}", number + 1
    used.add(candidate)
    return candidate


def _link_kind(target: str) -> str:
    parsed = urlsplit(target)
    if target.startswith("#") and len(target) > 1:
        return "fragment"
    if parsed.scheme == "https" and parsed.netloc and parsed.username is None and parsed.password is None:
        return "external"
    path = PurePosixPath(parsed.path)
    if not parsed.scheme and not parsed.netloc and parsed.path and not target.startswith(("/", "\\", "//")) and ".." not in path.parts and not parsed.query:
        return "reference"
    return "unsafe"


def parse_inlines(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    cursor = 0
    for match in INLINE_RE.finditer(text):
        if match.start() > cursor:
            result.append({"type": "text", "text": text[cursor:match.start()]})
        token = match.group(0)
        if token.startswith("`"):
            result.append({"type": "code", "text": token[1:-1]})
        elif token.startswith("**"):
            result.append({"type": "strong", "text": token[2:-2]})
        elif token.startswith("*"):
            result.append({"type": "emphasis", "text": token[1:-1]})
        else:
            label, target = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token).groups()  # type: ignore[union-attr]
            result.append({"type": "link", "text": label, "target": target, "kind": _link_kind(target)})
        cursor = match.end()
    if cursor < len(text):
        result.append({"type": "text", "text": text[cursor:]})
    return result or [{"type": "text", "text": ""}]


def split_work_sections(text: str) -> list[dict]:
    """Lossless top-level sections for CLI context; fences and frontmatter remain opaque."""
    lines = text.splitlines(keepends=True)
    boundaries = [(0, 0, '')]
    fence = None
    metadata_end = -1
    if lines and lines[0].strip() == '---':
        metadata_end = next((i for i in range(1, len(lines)) if lines[i].strip() == '---'), -1)
    for index, line in enumerate(lines):
        if index <= metadata_end:
            continue
        if fence:
            if _closes_fence(line.rstrip('\r\n'), fence):
                fence = None
            continue
        opened = _open_fence(line)
        if opened:
            fence = opened[0]
            continue
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) <= 2:
            boundaries.append((index, len(heading.group(1)), heading.group(2).strip()))
    result = []
    for i, (start, level, title) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(lines)
        if start < end:
            result.append({'title': title, 'level': level, 'line': start + 1,
                           'markdown': ''.join(lines[start:end])})
    return result


def parse_work_markdown(text: str) -> dict[str, object]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[dict[str, object]] = []
    outline: list[dict[str, object]] = []
    used: set[str] = set()
    index = 0
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
        except ValueError:
            end = -1
        if end > 0:
            blocks.append({"type": "frontmatter", "text": "\n".join(lines[1:end])})
            index = end + 1
    headings: list[tuple[int, int, int, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            text_value = "\n".join(paragraph)
            blocks.append({"type": "paragraph", "inlines": parse_inlines(text_value)})
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        fence = _open_fence(line)
        if fence:
            flush()
            marker, language = fence
            index += 1
            body: list[str] = []
            while index < len(lines) and not _closes_fence(lines[index], marker):
                body.append(lines[index]); index += 1
            if index < len(lines): index += 1
            blocks.append({"type": "code", "language": language, "text": "\n".join(body)})
            continue
        heading = HEADING_RE.match(line)
        if heading:
            flush()
            level, title = len(heading.group(1)), heading.group(2).strip()
            headings.append((index, len(blocks), level, title))
            anchor = _slug(re.sub(r"[`*_]", "", title), used)
            blocks.append({"type": "heading", "level": level, "id": anchor, "inlines": parse_inlines(title)})
            if level in (2, 3): outline.append({"level": level, "id": anchor, "title": title})
            index += 1; continue
        listed = LIST_RE.match(line)
        if listed:
            flush()
            items: list[dict[str, object]] = []
            ordered = listed.group(2)[0].isdigit()
            while index < len(lines):
                found = LIST_RE.match(lines[index])
                if not found: break
                item_ordered = found.group(2)[0].isdigit()
                if item_ordered != ordered and len(found.group(1).expandtabs(2)) == 0: break
                items.append({"depth": len(found.group(1).expandtabs(2)) // 2, "inlines": parse_inlines(found.group(3))})
                index += 1
            blocks.append({"type": "list", "ordered": ordered, "items": items})
            continue
        if line.startswith(">"):
            flush(); quote: list[str] = []
            while index < len(lines) and lines[index].startswith(">"):
                quote.append(lines[index][1:].lstrip()); index += 1
            blocks.append({"type": "blockquote", "inlines": parse_inlines("\n".join(quote))}); continue
        if re.fullmatch(r"\s*(\*{3,}|-{3,}|_{3,})\s*", line):
            flush(); blocks.append({"type": "thematicBreak"}); index += 1; continue
        if not line.strip():
            flush(); index += 1; continue
        paragraph.append(line); index += 1
    flush()
    title = "Untitled Work document"
    for block in blocks:
        if block["type"] == "heading" and block["level"] == 1:
            title = "".join(node["text"] for node in block["inlines"]); break
    sections = {"description": None, "progress": None}
    consumed = set()
    for position, (line_index, block_index, level, heading_title) in enumerate(headings):
        key = {"事项说明": "description", "当前推进": "progress"}.get(heading_title)
        if level != 2 or not key or sections[key] is not None:
            continue
        end_line, end_block = len(lines), len(blocks)
        for next_line, next_block, next_level, _ in headings[position + 1:]:
            if next_level <= 2:
                end_line, end_block = next_line, next_block
                break
        sections[key] = {"id": blocks[block_index]["id"], "blocks": blocks[block_index + 1:end_block],
                         "markdown": "\n".join(lines[line_index + 1:end_line]).strip()}
        consumed.update(range(block_index, end_block))
    remaining = [block for index, block in enumerate(blocks)
                 if index not in consumed and not (block["type"] == "heading" and block["level"] == 1)]
    return {"title": title, "outline": outline, "blocks": blocks,
            "sections": sections, "remainingBlocks": remaining}


class WorkDocuments:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.projects_root = (self.repo_root / "agent-data" / "projects").resolve()

    def _resolve_document(self, path: str) -> Path:
        key = PurePosixPath(path)
        parts = key.parts
        if (key.is_absolute() or "\\" in path or ".." in parts
                or key.as_posix() != path or len(parts) < 6
                or parts[:2] != ("agent-data", "projects")
                or parts[-3] != "work" or parts[-2] not in {"active", "archive"}
                or not parts[-1].endswith(".md")):
            raise WorkDocumentError("找不到允许的 Work 文档。")
        try:
            resolved = (self.repo_root / path).resolve(strict=True)
            relative = resolved.relative_to(self.projects_root)
            target = relative.parts
            if (len(target) < 4 or target[-3] != "work"
                    or target[-2] not in {"active", "archive"}
                    or resolved.suffix != ".md" or not resolved.is_file()):
                raise ValueError("Not a Work document")
        except (OSError, ValueError) as exc:
            raise WorkDocumentError("Work 文档已失效或超出允许目录。", code="forbidden_path") from exc
        return resolved

    def _mapping(self) -> dict[str, Path]:
        result: dict[str, Path] = {}
        # The workspace stores documents at projects/<context>/work/<state>.
        # Never recurse through a project's unrelated material or app bundles.
        for work in self.projects_root.glob("*/work"):
            for state in ("active", "archive"):
                for candidate in (work / state).glob("*.md"):
                    key = candidate.relative_to(self.repo_root).as_posix()
                    try:
                        resolved = self._resolve_document(key)
                    except WorkDocumentError:
                        continue
                    result[resolved.relative_to(self.repo_root).as_posix()] = resolved
        return result

    def allowed_paths(self) -> set[str]:
        return set(self._mapping())

    def list_documents(self) -> list[dict[str, str]]:
        rows = []
        for path, resolved in self._mapping().items():
            title = read_work_title(resolved)
            parts = PurePosixPath(path).parts
            work_index = parts.index("work")
            rows.append({"path": path, "title": title, "project": "/".join(parts[2:work_index]), "state": parts[work_index + 1]})
        return sorted(rows, key=lambda row: (row["state"] != "active", row["project"].casefold(), row["title"].casefold()))

    def read(self, path: str) -> dict[str, object]:
        resolved = self._resolve_document(path)
        try:
            text = resolved.read_text(encoding="utf-8")
            # Revalidate only this path; a portfolio rescan adds no protection.
            if self._resolve_document(path) != resolved:
                raise WorkDocumentError("Work 文档路径已变化。", code="forbidden_path")
            parsed = parse_work_markdown(text)
        except (OSError, UnicodeError) as exc:
            raise WorkDocumentError("无法读取 Work 文档。", code="read_failed") from exc
        canonical = resolved.relative_to(self.repo_root).as_posix()
        parts = PurePosixPath(canonical).parts
        work_index = parts.index("work")
        return {**parsed, "path": canonical, "absolutePath": str(resolved), "project": "/".join(parts[2:work_index]), "state": parts[work_index + 1]}


def read_work_title(path: Path) -> str:
    # Only the heading is needed by the picker, not a full Markdown AST.
    with path.open(encoding="utf-8") as source:
        fenced = None
        frontmatter = False
        for index, raw in enumerate(source):
            line = raw.rstrip("\r\n")
            if index == 0 and line == "---":
                frontmatter = True
                continue
            if frontmatter:
                if line == "---":
                    frontmatter = False
                continue
            if fenced:
                if _closes_fence(line, fenced):
                    fenced = None
                continue
            fence = _open_fence(line)
            if fence:
                fenced = fence[0]
                continue
            heading = HEADING_RE.match(line)
            if heading and len(heading.group(1)) == 1:
                return "".join(node["text"] for node in parse_inlines(heading.group(2).strip()))
    if frontmatter:
        return str(parse_work_markdown(path.read_text(encoding="utf-8"))["title"])
    return "Untitled Work document"
