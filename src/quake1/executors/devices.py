"""devices.* — Bluetooth via the `blueutil` CLI (brew install blueutil)."""

from __future__ import annotations

import re

from . import _util
from ._util import ToolError, unsupported

_MAC = re.compile(r"^[0-9a-f]{2}([:\-][0-9a-f]{2}){5}$", re.I)
_ADDR = re.compile(r"address: ([0-9a-fA-F:\-]+)")
_NAME = re.compile(r'name: "([^"]*)"')
MAX_RESULTS = 30


def _paired() -> list[dict]:
    try:
        out = _util.run_cmd(["blueutil", "--paired"])
    except ToolError as e:
        if "not installed" in str(e):
            raise ToolError("Bluetooth device control needs the `blueutil` CLI.",
                            hint="brew install blueutil") from e
        raise
    devices = []
    for line in out.splitlines()[:MAX_RESULTS]:
        m = _ADDR.search(line)
        if not m:
            continue
        name = _NAME.search(line)
        devices.append({
            "address": m.group(1),
            "name": name.group(1) if name else None,
            "connected": "not connected" not in line and "connected" in line,
        })
    return devices


def list_bluetooth(args: dict) -> dict:
    return {"devices": _paired()}


def connect_bluetooth(args: dict) -> dict:
    device = str(args.get("device") or "").strip()
    if not device:
        raise ToolError("a device name or address is required")
    if _MAC.match(device):
        addr = device
    else:
        matches = [d for d in _paired()
                   if d["name"] and device.lower() in d["name"].lower()]
        if not matches:
            raise ToolError(f"No paired Bluetooth device matching {device!r}")
        addr = matches[0]["address"]
    _util.run_cmd(["blueutil", "--connect", addr], timeout=30)
    return {"connected": addr}


HANDLERS = {
    "devices.list_bluetooth": list_bluetooth,
    "devices.connect_bluetooth": connect_bluetooth,
    "devices.airdrop": unsupported(
        "AirDrop has no automation API — sending requires the share-sheet UI"),
}
