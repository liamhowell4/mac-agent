"""calls.* — FaceTime / phone handoff via URL schemes (best-effort; macOS prompts
the user to confirm before placing the call)."""

from __future__ import annotations

import re
import urllib.parse

from . import _util
from ._util import ToolError
from .contacts import resolve_handle


def _handle(args: dict) -> str:
    contact = str(args.get("contact") or "").strip()
    if not contact:
        raise ToolError("a contact name or number is required")
    return resolve_handle(contact)


def facetime(args: dict) -> dict:
    handle = _handle(args)
    _util.run_cmd(["open", "facetime://" + urllib.parse.quote(handle, safe="+@.")])
    return {"calling": handle, "via": "facetime"}


def phone(args: dict) -> dict:
    handle = _handle(args)
    if "@" in handle:  # email handles can't dial tel:// — fall back to FaceTime audio
        _util.run_cmd(
            ["open", "facetime-audio://" + urllib.parse.quote(handle, safe="+@.")])
        return {"calling": handle, "via": "facetime-audio"}
    number = re.sub(r"[^\d+]", "", handle)
    if not number:
        raise ToolError(f"{handle!r} doesn't look like a dialable number")
    _util.run_cmd(["open", f"tel://{number}"])
    return {"calling": number, "via": "tel-handoff"}


HANDLERS = {
    "calls.facetime": facetime,
    "calls.phone": phone,
}
