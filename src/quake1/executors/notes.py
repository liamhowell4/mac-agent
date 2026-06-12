"""notes.* — Notes.app via AppleScript (the catalog's lone distractor domain)."""

from __future__ import annotations

from . import _util
from ._util import ToolError, as_quote


def create(args: dict) -> dict:
    title = str(args.get("title") or "").strip()
    body = str(args.get("body") or "").strip()
    if not (title or body):
        raise ToolError("a title or body is required")
    script = (
        'tell application "Notes" to make new note with properties '
        f"{{name:{as_quote(title or 'New Note')}, body:{as_quote(body)}}}"
    )
    _util.run_osascript(script, timeout=30)
    return {"created": title or "New Note"}


HANDLERS = {
    "notes.create": create,
}
