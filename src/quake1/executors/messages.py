"""messages.* — Messages.app via AppleScript.

Sending is scriptable; *reading* the message database is not (Messages.app exposes no
chat/message elements, and chat.db needs Full Disk Access + SQL). Read-side tools are
honest unsupported stubs rather than fake empties.
"""

from __future__ import annotations

from pathlib import Path

from . import _util
from ._util import ToolError, as_quote, unsupported
from .contacts import resolve_handle

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


_NO_READ = ("Messages.app is not scriptable for reading — chat history lives in chat.db "
            "(needs Full Disk Access; not wired up in v1)")

HANDLERS = {
    "messages.send": send,
    "messages.reply": reply,
    "messages.send_attachment": send_attachment,
    "messages.read_thread": unsupported(_NO_READ),
    "messages.list_unread": unsupported(_NO_READ),
    "messages.search": unsupported(_NO_READ),
    "messages.mark_read": unsupported(_NO_READ),
    "messages.react": unsupported(
        "tapback reactions have no AppleScript or CLI API on macOS"),
}
