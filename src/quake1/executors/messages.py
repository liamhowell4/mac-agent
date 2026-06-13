"""messages.* — sending via AppleScript, reading via chat.db.

Sending is scriptable; *reading* is not (Messages.app exposes no chat/message elements),
so read-side tools query the SQLite database in `_chatdb` (needs Full Disk Access on the
daemon's binary). `mark_read` stays an honest stub: it'd be a *write* to the live database
Messages owns, which risks corruption and has no safe AppleScript path.
"""

from __future__ import annotations

from pathlib import Path

from . import _chatdb, _util
from ._util import ToolError, as_quote, unsupported
from .contacts import looks_like_handle, resolve_handle

_ACCOUNT = '(1st account whose service type = iMessage)'


def _send_script(payload: str, handle: str) -> str:
    return (f'tell application "Messages" to send {payload} '
            f"to participant {as_quote(handle)} of {_ACCOUNT}")


def _send(args: dict, recipient_key: str = "recipient") -> dict:
    body = str(args.get("body") or "").strip()
    if not body:
        raise ToolError("a message body is required")
    handle = resolve_handle(str(args.get(recipient_key) or ""))
    _util.run_osascript(_send_script(as_quote(body), handle), timeout=30)
    return {"sent_to": handle, "body": body}


def send(args: dict) -> dict:
    return _send(args, "recipient")


def reply(args: dict) -> dict:
    # iMessage threads are per-contact; replying == sending to that contact
    return _send(args, "contact")


def send_attachment(args: dict) -> dict:
    path = Path(str(args.get("file_path") or "")).expanduser()
    if not str(args.get("file_path") or "").strip():
        raise ToolError("file_path is required")
    if not path.exists():
        raise ToolError(f"No such file: {path}")
    handle = resolve_handle(str(args.get("recipient") or ""))
    if args.get("body"):
        _util.run_osascript(_send_script(as_quote(str(args["body"])), handle), timeout=30)
    _util.run_osascript(
        _send_script(f"POSIX file {as_quote(str(path))}", handle), timeout=30)
    return {"sent_to": handle, "attachment": str(path)}


def _soft_resolve(contact: str) -> str | None:
    """Resolve a name to a handle for DB matching, but tolerate misses (the chat
    identifier / display name may still match by substring)."""
    c = str(contact or "").strip()
    if not c:
        return None
    if looks_like_handle(c):
        return c
    try:
        return resolve_handle(c)
    except ToolError:
        return None


def read_thread(args: dict) -> dict:
    contact = str(args.get("contact") or "").strip()
    if not contact:
        raise ToolError("a contact or thread is required")
    limit = max(1, min(int(args.get("limit") or 50), 500))
    return {"messages": _chatdb.read_thread(contact, _soft_resolve(contact), limit)}


def list_unread(args: dict) -> dict:
    limit = max(1, min(int(args.get("limit") or 20), 200))
    return {"conversations": _chatdb.list_unread(limit)}


def search(args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("a search query is required")
    contact = str(args.get("contact") or "").strip() or None
    limit = max(1, min(int(args.get("limit") or 50), 500))
    resolved = _soft_resolve(contact) if contact else None
    return {"matches": _chatdb.search(query, contact, resolved, limit)}


HANDLERS = {
    "messages.send": send,
    "messages.reply": reply,
    "messages.send_attachment": send_attachment,
    "messages.read_thread": read_thread,
    "messages.list_unread": list_unread,
    "messages.search": search,
    "messages.mark_read": unsupported(
        "marking read requires writing to the chat.db Messages owns — unsafe and has no "
        "AppleScript path"),
    "messages.react": unsupported(
        "tapback reactions have no AppleScript or CLI API on macOS"),
}
