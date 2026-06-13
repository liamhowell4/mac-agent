"""Read-only access to the Messages database (~/Library/Messages/chat.db).

Messages.app exposes no AppleScript for reading history, so the only honest path is
SQL over chat.db — which needs **Full Disk Access granted to the daemon's binary**
(sys.executable), not to Quake.app. FDA is keyed to the image performing the read, and
is *not* inherited from the parent the way an Automation grant's responsible-process is.

We open the database read-only and never write to it. Recent macOS stores message text
in the `attributedBody` typedstream blob rather than the `text` column, so we decode it.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from ._util import ToolError

DB_PATH = Path("~/Library/Messages/chat.db").expanduser()
# Apple's Cocoa epoch (2001-01-01 UTC) offset from the Unix epoch, in seconds.
_APPLE_EPOCH = 978307200
_SCAN_CAP = 20000  # search scans the most recent N messages (bounds decode cost)


def _fda_error() -> ToolError:
    return ToolError(
        "Can't read Messages history — the Quake daemon needs Full Disk Access.",
        hint=(f"Grant Full Disk Access to this binary: {os.path.realpath(sys.executable)} "
              "— a symlink/venv shim won't do; TCC keys FDA to the resolved image. System Settings "
              "> Privacy & Security > Full Disk Access "
              "(x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles), "
              "then restart Quake."),
    )


def _connect() -> sqlite3.Connection:
    """Open chat.db read-only, mapping a TCC denial to a clear FDA error."""
    if not DB_PATH.exists():
        raise ToolError(f"Messages database not found at {DB_PATH}")
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0)
        conn.execute("SELECT 1 FROM message LIMIT 1")  # forces the open; surfaces TCC denial
    except sqlite3.OperationalError as first:
        low = str(first).lower()
        if "unable to open" in low or "authorization" in low or "permission" in low:
            raise _fda_error() from first
        # A locking/shared-memory issue (Messages is writing): re-open ignoring the WAL.
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True, timeout=5.0)
            conn.execute("SELECT 1 FROM message LIMIT 1")
        except sqlite3.OperationalError as second:
            raise _fda_error() from second
    conn.row_factory = sqlite3.Row
    return conn


def _apple_to_iso(raw: int | None) -> str | None:
    """Convert a chat.db timestamp (ns since 2001, or seconds on old DBs) to local ISO."""
    if not raw:
        return None
    secs = raw / 1e9 if raw > 1e11 else raw
    dt = datetime.fromtimestamp(secs + _APPLE_EPOCH, tz=UTC).astimezone()
    return dt.isoformat(timespec="seconds")


def _attributed_body_text(blob: bytes | None) -> str:
    """Best-effort extraction of message text from an NSAttributedString typedstream blob."""
    if not blob:
        return ""
    marker = blob.find(b"NSString")
    if marker == -1:
        return ""
    plus = blob.find(b"\x2b", marker)  # '+' frames the length-prefixed UTF-8 payload
    if plus == -1 or plus + 1 >= len(blob):
        return ""
    i = plus + 1
    n = blob[i]
    if n == 0x81:  # next 2 bytes are a little-endian length
        length = int.from_bytes(blob[i + 1:i + 3], "little")
        start = i + 3
    elif n == 0x82:  # next 4 bytes are a little-endian length
        length = int.from_bytes(blob[i + 1:i + 5], "little")
        start = i + 5
    else:
        length = n
        start = i + 1
    return blob[start:start + length].decode("utf-8", errors="replace").strip()


def _text_of(row: sqlite3.Row) -> str:
    txt = row["text"]
    if txt:
        return txt.strip()
    return _attributed_body_text(row["attributedBody"])


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _matching_chat_ids(conn: sqlite3.Connection, contact: str, resolved: str | None) -> list[int]:
    """Chat ROWIDs whose handle, identifier, or display name matches `contact`.

    Phone numbers match on their last 10 digits so "+1 555…" and "5555…" coincide.
    """
    targets = {t.strip().lower() for t in (contact, resolved) if t and t.strip()}
    tail = next((_digits(t)[-10:] for t in targets if len(_digits(t)) >= 10), None)

    handle_ids: list[int] = []
    for hid, ident in conn.execute("SELECT ROWID, id FROM handle"):
        low = (ident or "").lower()
        if low in targets or (tail and _digits(ident)[-10:] == tail):
            handle_ids.append(hid)

    chat_ids: set[int] = set()
    if handle_ids:
        marks = ",".join("?" * len(handle_ids))
        chat_ids.update(r[0] for r in conn.execute(
            f"SELECT DISTINCT chat_id FROM chat_handle_join WHERE handle_id IN ({marks})",
            handle_ids))
    like = f"%{contact.strip()}%"
    chat_ids.update(r[0] for r in conn.execute(
        "SELECT ROWID FROM chat WHERE chat_identifier LIKE ? OR display_name LIKE ?",
        (like, like)))
    return sorted(chat_ids)


def read_thread(contact: str, resolved: str | None, limit: int) -> list[dict]:
    with _connect() as conn:
        chat_ids = _matching_chat_ids(conn, contact, resolved)
        if not chat_ids:
            raise ToolError(f"No conversation found for {contact!r}")
        marks = ",".join("?" * len(chat_ids))
        rows = conn.execute(
            f"""SELECT m.text, m.attributedBody, m.is_from_me, m.date, h.id AS handle
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE cmj.chat_id IN ({marks})
                ORDER BY m.date DESC LIMIT ?""",
            (*chat_ids, limit)).fetchall()
    messages = [
        {"from": "me" if r["is_from_me"] else (r["handle"] or contact),
         "text": _text_of(r),
         "date": _apple_to_iso(r["date"])}
        for r in reversed(rows)  # oldest-first for readability
    ]
    return [m for m in messages if m["text"]]


def list_unread(limit: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT c.chat_identifier, c.display_name,
                      COUNT(*) AS unread, MAX(m.date) AS last
               FROM message m
               JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
               JOIN chat c ON c.ROWID = cmj.chat_id
               WHERE m.is_from_me = 0 AND m.is_read = 0
               GROUP BY c.ROWID
               ORDER BY last DESC LIMIT ?""",
            (limit,)).fetchall()
    return [
        {"contact": r["display_name"] or r["chat_identifier"],
         "unread_count": r["unread"],
         "last_message_at": _apple_to_iso(r["last"])}
        for r in rows
    ]


def search(query: str, contact: str | None, resolved: str | None, limit: int) -> list[dict]:
    """Scan the most recent messages (decoding bodies) for a case-insensitive substring."""
    needle = query.strip().lower()
    with _connect() as conn:
        chat_filter, params = "", []
        if contact:
            chat_ids = _matching_chat_ids(conn, contact, resolved)
            if not chat_ids:
                return []
            marks = ",".join("?" * len(chat_ids))
            chat_filter = f"AND cmj.chat_id IN ({marks})"
            params = list(chat_ids)
        rows = conn.execute(
            f"""SELECT m.text, m.attributedBody, m.is_from_me, m.date, h.id AS handle
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE 1=1 {chat_filter}
                ORDER BY m.date DESC LIMIT ?""",
            (*params, _SCAN_CAP)).fetchall()
    hits = []
    for r in rows:
        text = _text_of(r)
        if needle in text.lower():
            hits.append({"from": "me" if r["is_from_me"] else (r["handle"] or "?"),
                         "text": text, "date": _apple_to_iso(r["date"])})
            if len(hits) >= limit:
                break
    return hits
