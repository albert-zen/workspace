"""SQLite-backed storage for Personal Inbox and workflow metadata (stdlib only)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

from .inbox_model import Entry, InboxDocument, InboxError, SECTION_NAMES, StaleSourceError, ordered_entries, revision_for


SCHEMA_VERSION = "1"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    work_path TEXT NOT NULL DEFAULT '',
    issue_url TEXT NOT NULL DEFAULT '',
    lifecycle_status TEXT NOT NULL DEFAULT 'planned',
    in_inbox INTEGER NOT NULL DEFAULT 0,
    attention TEXT NOT NULL DEFAULT 'Waiting',
    pinned INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    owner TEXT,
    arrangement TEXT NOT NULL DEFAULT '',
    stage TEXT,
    plan_version INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    run_number INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    result TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    resources_json TEXT NOT NULL DEFAULT '[]',
    closeout_pending_json TEXT NOT NULL DEFAULT '[]',
    step_states_json TEXT NOT NULL DEFAULT '{}',
    extra_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_items_inbox_order
    ON items (in_inbox, attention, pinned DESC, position ASC);
"""

KNOWN_RECORD_KEYS = {
    "id", "name", "title", "summary", "workPath", "issueUrl",
    "lifecycleStatus", "inbox", "attention", "pinned",
    "owner", "arrangement", "stage", "planVersion", "eventCount", "run",
    "createdAt", "completedAt", "result",
    "aliases", "resources", "closeoutPending", "stepStates",
}


def inbox_revision_for(doc: InboxDocument) -> str:
    """Deterministic revision derived solely from visible Inbox sections and order."""
    canonical = [
        [
            status,
            [
                [
                    entry.item_id or "",
                    entry.title,
                    entry.summary,
                    entry.issue_url or "",
                    entry.work_path or "",
                    bool(entry.pinned),
                ]
                for entry in ordered_entries(doc.sections[status])
            ],
        ]
        for status in SECTION_NAMES
    ]
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return revision_for(payload)


def serialize_doc_sections(doc: InboxDocument) -> dict[str, list[dict[str, object]]]:
    return {
        status: [
            {
                "id": entry.item_id,
                "title": entry.title,
                "summary": entry.summary,
                "issueUrl": entry.issue_url or "",
                "workPath": entry.work_path or "",
                "pinned": bool(entry.pinned),
            }
            for entry in ordered_entries(doc.sections[status])
        ]
        for status in SECTION_NAMES
    }


def deserialize_doc_sections(raw_sections: dict[str, list[dict[str, object]]]) -> InboxDocument:
    sections: dict[str, list[Entry]] = {}
    for status in SECTION_NAMES:
        entries = [
            Entry(
                title=item["title"],
                summary=item["summary"],
                issue_url=item.get("issueUrl") or None,
                work_path=item.get("workPath") or None,
                item_id=item.get("id") or None,
                pinned=bool(item.get("pinned", False)),
            )
            for item in raw_sections.get(status, [])
        ]
        sections[status] = ordered_entries(entries)
    return InboxDocument(sections)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.close(os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600))
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA_SQL)
    return conn


def _read_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}


def read_version_token(db_path: Path) -> str:
    """Fast metadata token for SSE polling without reading item rows."""
    if not db_path.is_file():
        return "missing"
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        rows = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM meta WHERE key IN ('change_seq', 'inbox_revision')")
        }
        return f"{rows.get('change_seq', '0')}:{rows.get('inbox_revision', '')}"
    except sqlite3.Error as exc:
        raise InboxError("无法读取工作流数据库版本，请检查 work.db。", code="read_failed") from exc
    finally:
        conn.close()


def load_state_and_doc(db_path: Path) -> tuple[dict, InboxDocument, str, int]:
    """Load full workflow state and InboxDocument from SQLite."""
    conn = _connect(db_path)
    try:
        meta = _read_meta(conn)
        schema_version = meta.get("schema_version")
        if schema_version is not None and schema_version != SCHEMA_VERSION:
            raise InboxError(f"工作流数据库版本不受支持：{schema_version}。", code="invalid_state")

        items: dict[str, dict] = {}
        sections: dict[str, list[Entry]] = {status: [] for status in SECTION_NAMES}

        rows = conn.execute(
            """
            SELECT * FROM items
            ORDER BY in_inbox DESC, attention ASC, pinned DESC, position ASC, created_at ASC, id ASC
            """
        ).fetchall()

        for row in rows:
            extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
            record: dict[str, object] = {
                "id": row["id"],
                "name": row["name"],
                "title": row["title"],
                "summary": row["summary"],
                "workPath": row["work_path"],
                "issueUrl": row["issue_url"],
                "lifecycleStatus": row["lifecycle_status"],
                "inbox": bool(row["in_inbox"]),
                "attention": row["attention"],
                "owner": row["owner"],
                "createdAt": row["created_at"],
                "aliases": json.loads(row["aliases_json"]),
                "resources": json.loads(row["resources_json"]),
                "closeoutPending": json.loads(row["closeout_pending_json"]),
            }
            if row["pinned"]:
                record["pinned"] = True
            if row["arrangement"]:
                record["arrangement"] = row["arrangement"]
            if row["stage"] is not None:
                record["stage"] = row["stage"]
            if row["plan_version"]:
                record["planVersion"] = row["plan_version"]
            if row["event_count"]:
                record["eventCount"] = row["event_count"]
            if row["run_number"] and row["run_number"] != 1:
                record["run"] = row["run_number"]
            if row["completed_at"] is not None:
                record["completedAt"] = row["completed_at"]
            if row["result"] is not None:
                record["result"] = row["result"]
            step_states = json.loads(row["step_states_json"])
            if step_states:
                record["stepStates"] = step_states
            for k, v in extra.items():
                if k not in record:
                    record[k] = v
            items[row["id"]] = record

            if row["in_inbox"] and row["attention"] in sections:
                sections[row["attention"]].append(
                    Entry(
                        title=row["title"],
                        summary=row["summary"],
                        issue_url=row["issue_url"] or None,
                        work_path=row["work_path"] or None,
                        item_id=row["id"],
                        pinned=bool(row["pinned"]),
                    )
                )

        doc = InboxDocument({status: ordered_entries(entries) for status, entries in sections.items()})
        rev = meta.get("inbox_revision") or inbox_revision_for(doc)
        seq = int(meta.get("change_seq", "0"))
        return {"version": 1, "items": items}, doc, rev, seq
    except sqlite3.Error as exc:
        raise InboxError("工作流数据库无法解析，原文件已保留；请检查 agent-data/workflow/work.db。", code="invalid_state") from exc
    finally:
        conn.close()


def save_state_and_doc(
    db_path: Path,
    state: dict,
    doc: InboxDocument,
    *,
    expected_seq: int | None = None,
    expected_inbox_rev: str | None = None,
    operation_id: str | None = None,
    migrated_from_legacy_at: str | None = None,
) -> tuple[str, int]:
    """Atomically persist workflow state and Inbox ordering into SQLite."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        meta = _read_meta(conn)
        if operation_id and meta.get("last_op_id") == operation_id:
            rev = meta.get("inbox_revision") or inbox_revision_for(doc)
            seq = int(meta.get("change_seq", "0"))
            conn.execute("COMMIT")
            return rev, seq

        current_seq = int(meta.get("change_seq", "0"))
        current_rev = meta.get("inbox_revision")
        if expected_seq is not None and current_seq != expected_seq:
            conn.execute("ROLLBACK")
            raise StaleSourceError()
        if expected_inbox_rev is not None and current_rev is not None and current_rev != expected_inbox_rev:
            conn.execute("ROLLBACK")
            raise StaleSourceError()

        # Map inbox placement (attention, pinned, position) from the ordered InboxDocument.
        placement: dict[str, tuple[str, int, int]] = {}
        for status in SECTION_NAMES:
            ordered = ordered_entries(doc.sections[status])
            for position, entry in enumerate(ordered):
                if entry.item_id:
                    placement[entry.item_id] = (status, 1 if entry.pinned else 0, position)

        conn.execute("DELETE FROM items")
        rows_to_insert = []
        for item_id, record in state.get("items", {}).items():
            in_inbox = 1 if item_id in placement else 0
            if in_inbox:
                attention, pinned, position = placement[item_id]
            else:
                attention = record.get("attention") or "Waiting"
                pinned = 1 if record.get("pinned") else 0
                position = 0
            extra = {k: v for k, v in record.items() if k not in KNOWN_RECORD_KEYS}
            rows_to_insert.append(
                (
                    item_id,
                    record["name"],
                    record["title"],
                    record["summary"],
                    record.get("workPath") or "",
                    record.get("issueUrl") or "",
                    record.get("lifecycleStatus") or "planned",
                    in_inbox,
                    attention,
                    pinned,
                    position,
                    record.get("owner"),
                    record.get("arrangement") or "",
                    record.get("stage"),
                    int(record.get("planVersion") or 0),
                    int(record.get("eventCount") or 0),
                    int(record.get("run") or 1),
                    record.get("createdAt") or "",
                    record.get("completedAt"),
                    record.get("result"),
                    json.dumps(record.get("aliases") or [], ensure_ascii=False),
                    json.dumps(record.get("resources") or [], ensure_ascii=False),
                    json.dumps(record.get("closeoutPending") or [], ensure_ascii=False),
                    json.dumps(record.get("stepStates") or {}, ensure_ascii=False),
                    json.dumps(extra, ensure_ascii=False),
                )
            )

        conn.executemany(
            """
            INSERT INTO items (
                id, name, title, summary, work_path, issue_url,
                lifecycle_status, in_inbox, attention, pinned, position,
                owner, arrangement, stage, plan_version, event_count, run_number,
                created_at, completed_at, result,
                aliases_json, resources_json, closeout_pending_json,
                step_states_json, extra_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?
            )
            """,
            rows_to_insert,
        )

        new_rev = inbox_revision_for(doc)
        new_seq = current_seq + 1
        meta_updates = {
            "schema_version": SCHEMA_VERSION,
            "inbox_revision": new_rev,
            "change_seq": str(new_seq),
        }
        if operation_id:
            meta_updates["last_op_id"] = operation_id
        if migrated_from_legacy_at:
            meta_updates["migrated_from_legacy_at"] = migrated_from_legacy_at

        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            list(meta_updates.items()),
        )
        conn.execute("COMMIT")
        return new_rev, new_seq
    except (StaleSourceError, InboxError):
        raise
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise InboxError("无法写入工作流数据库；原数据已保留。", code="write_failed") from exc
    finally:
        conn.close()
