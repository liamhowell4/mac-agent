"""network.* — networksetup/ipconfig CLIs; public IP via api.ipify.org."""

from __future__ import annotations

import httpx

from . import _util
from ._util import ToolError, unsupported

WIFI_IF = "en0"


def wifi_status(args: dict) -> dict:
    out = _util.run_cmd(["networksetup", "-getairportnetwork", WIFI_IF])
    ssid = None
    if ":" in out and "not associated" not in out.lower():
        ssid = out.split(":", 1)[1].strip()
    status: dict = {"connected": ssid is not None, "ssid": ssid}
    try:  # best-effort local IP enrichment
        status["local_ip"] = _util.run_cmd(["ipconfig", "getifaddr", WIFI_IF])
    except ToolError:
        pass
    return status


def get_ip(args: dict) -> dict:
    scope = str(args.get("scope") or "local").lower()
    if scope == "local":
        return {"scope": "local", "ip": _util.run_cmd(["ipconfig", "getifaddr", WIFI_IF])}
    if scope == "public":
        try:
            r = httpx.get("https://api.ipify.org", timeout=10)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ToolError(f"couldn't reach api.ipify.org: {e}") from e
        return {"scope": "public", "ip": r.text.strip()}
    raise ToolError("scope must be 'local' or 'public'")


def connect_wifi(args: dict) -> dict:
    ssid = str(args.get("ssid") or "").strip()
    if not ssid:
        raise ToolError("an ssid is required")
    argv = ["networksetup", "-setairportnetwork", WIFI_IF, ssid]
    if args.get("password"):
        argv.append(str(args["password"]))
    out = _util.run_cmd(argv, timeout=30)
    if "failed" in out.lower() or "could not" in out.lower():
        raise ToolError(f"couldn't join {ssid!r}: {out[:200]}")
    return {"connected": ssid}


HANDLERS = {
    "network.wifi_status": wifi_status,
    "network.list_wifi": unsupported(
        "scanning for networks needs the `airport` CLI, removed in modern macOS"),
    "network.get_ip": get_ip,
    "network.connect_wifi": connect_wifi,
}
