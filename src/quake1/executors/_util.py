"""Shared plumbing for real executors: subprocess helpers, AppleScript, error mapping."""

from __future__ import annotations

import subprocess

OSASCRIPT_TIMEOUT = 20.0
CMD_TIMEOUT = 20.0

SETTINGS_PANES = {
    "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
    "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "files": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
}


class ToolError(Exception):
    """An expected, user-meaningful failure (bad input, TCC denial, app not running)."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


def as_quote(s: str) -> str:
    """Quote a string for safe embedding in AppleScript source."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def map_osascript_error(stderr: str) -> ToolError:
    low = stderr.lower()
    if "-1743" in stderr or "not authorized" in low or "not allowed" in low:
        app = _guess_app(stderr)
        return ToolError(
            f"macOS blocked automation{f' of {app}' if app else ''} — permission needed.",
            hint="System Settings > Privacy & Security > Automation "
                 f"({SETTINGS_PANES['automation']})",
        )
    if "-1728" in stderr:
        return ToolError(f"The item wasn't found: {stderr.strip()[:200]}")
    if "-600" in stderr or "isn't running" in low:
        return ToolError(f"The target app isn't running: {stderr.strip()[:200]}")
    return ToolError(f"AppleScript error: {stderr.strip()[:300]}")


def _guess_app(stderr: str) -> str | None:
    for app in ("Calendar", "Reminders", "Contacts", "Messages", "Mail", "Music",
                "Notes", "System Events", "Finder", "FaceTime"):
        if app.lower() in stderr.lower():
            return app
    return None


def run_osascript(script: str, timeout: float = OSASCRIPT_TIMEOUT) -> str:
    try:
        p = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"AppleScript timed out after {timeout:.0f}s") from e
    if p.returncode != 0:
        raise map_osascript_error(p.stderr or p.stdout)
    return p.stdout.strip()


def run_cmd(argv: list[str], timeout: float = CMD_TIMEOUT, input_: str | None = None) -> str:
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, input=input_,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"`{argv[0]}` timed out after {timeout:.0f}s") from e
    except FileNotFoundError as e:
        raise ToolError(f"`{argv[0]}` is not installed") from e
    if p.returncode != 0:
        raise ToolError(f"`{argv[0]}` failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout.strip()


def unsupported(reason: str):
    """Make an honest not-supported-yet handler (better than a lying empty result)."""

    def handler(args: dict) -> dict:
        raise ToolError(f"Not supported yet: {reason}")

    return handler
