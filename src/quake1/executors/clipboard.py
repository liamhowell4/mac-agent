"""clipboard.* — pbpaste/pbcopy. macOS keeps no clipboard history (honest stub)."""

from __future__ import annotations

from . import _util
from ._util import ToolError, unsupported


def get(args: dict) -> dict:
    return {"text": _util.run_cmd(["pbpaste"])[:8000]}


def set_(args: dict) -> dict:
    text = args.get("text")
    if text is None:
        raise ToolError("text to copy is required")
    _util.run_cmd(["pbcopy"], input_=str(text))
    return {"copied_chars": len(str(text))}


HANDLERS = {
    "clipboard.get": get,
    "clipboard.set": set_,
    "clipboard.history": unsupported(
        "macOS keeps only the current pasteboard — there is no clipboard history API"),
}
