"""apps.* — app lifecycle via System Events + the `open` CLI."""

from __future__ import annotations

from . import _util
from ._util import ToolError, as_quote


def _name(args: dict) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("an app name is required")
    return name


def list_running(args: dict) -> dict:
    out = _util.run_osascript(
        'tell application "System Events" to return name of every application process '
        "whose background only is false",
        timeout=30,
    )
    return {"apps": [a.strip() for a in out.split(",") if a.strip()][:100]}


def open_(args: dict) -> dict:
    name = _name(args)
    _util.run_cmd(["open", "-a", name])
    return {"opened": name}


def quit_(args: dict) -> dict:
    name = _name(args)
    try:
        _util.run_osascript(f"tell application {as_quote(name)} to quit", timeout=30)
    except ToolError as e:
        if "isn't running" in str(e):  # already quit — that's the desired end state
            return {"quit": name, "was_running": False}
        raise
    return {"quit": name}


def switch_to(args: dict) -> dict:
    name = _name(args)
    _util.run_osascript(f"tell application {as_quote(name)} to activate", timeout=30)
    return {"focused": name}


HANDLERS = {
    "apps.list_running": list_running,
    "apps.open": open_,
    "apps.quit": quit_,
    "apps.switch_to": switch_to,
}
