"""system.* — osascript + system CLIs."""

from __future__ import annotations

from ._util import ToolError, run_cmd, run_osascript


def _level(args: dict, key: str = "level") -> int:
    try:
        v = int(args[key])
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError(f"an integer {key} (0-100) is required") from e
    return max(0, min(100, v))


def get_battery(args: dict) -> dict:
    out = run_cmd(["pmset", "-g", "batt"])
    pct = next((tok.rstrip(";%") for tok in out.split() if tok.rstrip(";").endswith("%")), None)
    return {"battery": int(pct) if pct and pct.isdigit() else None,
            "charging": "AC Power" in out}


def get_volume(args: dict) -> dict:
    out = run_osascript("output volume of (get volume settings)")
    return {"volume": int(out) if out.isdigit() else out}


def set_volume(args: dict) -> dict:
    lvl = _level(args)
    run_osascript(f"set volume output volume {lvl}")
    return {"volume": lvl}


def set_brightness(args: dict) -> dict:
    import shutil  # noqa: PLC0415

    lvl = _level(args)
    if shutil.which("brightness"):  # no guaranteed-failing spawn when CLI absent
        run_cmd(["brightness", str(lvl / 100)])
        return {"brightness": lvl}
    run_osascript(
        'tell application "System Events" to tell every desktop to set brightness '
        f"to {lvl / 100}"
    )
    return {"brightness": lvl}


def toggle_dark_mode(args: dict) -> dict:
    enabled = args.get("enabled")
    target = "not dark mode" if enabled is None else ("true" if enabled else "false")
    out = run_osascript(
        'tell application "System Events" to tell appearance preferences to set dark mode '
        f"to {target}"
    )
    return {"dark_mode": out or enabled}


def toggle_dnd(args: dict) -> dict:
    enabled = bool(args.get("enabled", True))
    # macOS exposes Focus via Shortcuts; require a user shortcut named "Toggle DND"
    name = "Toggle DND"
    try:
        run_cmd(["shortcuts", "run", name], timeout=15)
        return {"dnd": enabled, "via": f"Shortcuts:{name}"}
    except ToolError as e:
        raise ToolError(
            "Do Not Disturb needs a Shortcuts shortcut named 'Toggle DND' "
            "(macOS has no public DND API).",
            hint="Create it once in the Shortcuts app: Set Focus > Do Not Disturb",
        ) from e


def set_focus_mode(args: dict) -> dict:
    mode = str(args.get("mode") or "").strip()
    if not mode:
        raise ToolError("a focus mode name is required")
    name = f"Set Focus {mode.title()}"
    try:
        run_cmd(["shortcuts", "run", name], timeout=15)
        return {"focus": mode, "via": f"Shortcuts:{name}"}
    except ToolError as e:
        raise ToolError(
            f"Focus modes need a Shortcuts shortcut named {name!r}.",
            hint="Create it once in the Shortcuts app: Set Focus action",
        ) from e


def toggle_wifi(args: dict) -> dict:
    enabled = bool(args.get("enabled", True))
    run_cmd(["networksetup", "-setairportpower", "en0", "on" if enabled else "off"])
    return {"wifi": enabled}


def toggle_bluetooth(args: dict) -> dict:
    enabled = bool(args.get("enabled", True))
    try:
        run_cmd(["blueutil", "--power", "1" if enabled else "0"])
        return {"bluetooth": enabled}
    except ToolError as e:
        raise ToolError(
            "Bluetooth control needs the `blueutil` CLI.",
            hint="brew install blueutil",
        ) from e


def caffeinate(args: dict) -> dict:
    import subprocess  # noqa: PLC0415

    enabled = bool(args.get("enabled", True))
    if not enabled:
        run_cmd(["pkill", "-x", "caffeinate"])
        return {"caffeinate": False}
    minutes = int(args.get("duration_minutes") or 60)
    subprocess.Popen(["caffeinate", "-d", "-t", str(minutes * 60)],
                     start_new_session=True)
    return {"caffeinate": True, "minutes": minutes}


def lock_screen(args: dict) -> dict:
    # CGSession -suspend needs no Accessibility permission (keystroke simulation would)
    run_cmd(["/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
             "-suspend"])
    return {"locked": True}


def sleep(args: dict) -> dict:
    run_cmd(["pmset", "sleepnow"])
    return {"sleeping": True}


def restart_or_shutdown(args: dict) -> dict:
    action = str(args.get("action") or "").lower()
    if action not in ("restart", "shutdown", "shut down"):
        raise ToolError("action must be 'restart' or 'shutdown'")
    verb = "restart" if action == "restart" else "shut down"
    run_osascript(f'tell application "System Events" to {verb}')
    return {"action": verb}


HANDLERS = {
    "system.get_battery": get_battery,
    "system.get_volume": get_volume,
    "system.set_volume": set_volume,
    "system.set_brightness": set_brightness,
    "system.toggle_dark_mode": toggle_dark_mode,
    "system.toggle_dnd": toggle_dnd,
    "system.set_focus_mode": set_focus_mode,
    "system.toggle_wifi": toggle_wifi,
    "system.toggle_bluetooth": toggle_bluetooth,
    "system.caffeinate": caffeinate,
    "system.lock_screen": lock_screen,
    "system.sleep": sleep,
    "system.restart_or_shutdown": restart_or_shutdown,
}