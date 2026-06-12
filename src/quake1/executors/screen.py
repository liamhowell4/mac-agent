"""screen.* — screencapture CLI. A silent screencapture run with no output file means
the Screen Recording TCC permission is missing, so we check and hint."""

from __future__ import annotations

import time
from pathlib import Path

from . import _util
from ._util import SETTINGS_PANES, ToolError, unsupported


def _file_exists(path: str) -> bool:  # separated for testability
    return Path(path).exists()


def _dest(args: dict) -> str:
    raw = args.get("save_path") or f"~/Desktop/quake_screenshot_{int(time.time())}.png"
    return str(Path(str(raw)).expanduser())


def _check_written(path: str) -> dict:
    if not _file_exists(path):
        raise ToolError(
            "Screenshot file was not created — Screen Recording permission is "
            "likely missing.",
            hint="System Settings > Privacy & Security > Screen Recording "
                 f"({SETTINGS_PANES['screen']})",
        )
    return {"saved": path}


def screenshot(args: dict) -> dict:
    path = _dest(args)
    _util.run_cmd(["screencapture", "-x", path])
    return _check_written(path)


def capture_region(args: dict) -> dict:
    try:
        x, y, w, h = (int(args[k]) for k in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError("integer x, y, width, and height are required") from e
    path = _dest(args)
    _util.run_cmd(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", path])
    return _check_written(path)


HANDLERS = {
    "screen.screenshot": screenshot,
    "screen.capture_region": capture_region,
    "screen.start_recording": unsupported(
        "screen recording needs an interactive stop signal; "
        "no fire-and-forget API fits a tool call"),
    "screen.ocr": unsupported(
        "on-screen OCR (Vision framework over a capture) isn't wired up in v1"),
}
