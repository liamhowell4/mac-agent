"""Execution gating: read-only auto-runs, mutations confirm, dangerous always confirms.

The eval's headline finding was that over-calling/wrong-mutation is the trust-killer;
the runtime enforces what the model can't guarantee. Unknown tools fail closed.
"""

from __future__ import annotations

import json
from pathlib import Path

from tooleval.types import ToolCall, ToolSchema

ALLOWLIST_PATH = Path("~/Library/Application Support/Quake1/allow.json").expanduser()

DANGEROUS = {
    "shell.run_command",
    "system.restart_or_shutdown",
    "files.delete",
    "files.compress",
}

AUTO = "auto"
CONFIRM = "confirm"
DANGER = "danger"


def load_allowlist() -> set[str]:
    try:
        return set(json.loads(ALLOWLIST_PATH.read_text()))
    except (OSError, json.JSONDecodeError):
        return set()


def allow_always(tool: str) -> None:
    """Persist a user's 'always allow' for one tool. Dangerous tools can't be allowlisted."""
    if tool in DANGEROUS:
        return
    allowed = load_allowlist() | {tool}
    ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALLOWLIST_PATH.write_text(json.dumps(sorted(allowed)))


def classify(call: ToolCall, schema: ToolSchema | None,
             allowlist: set[str] | None = None) -> str:
    if call.name in DANGEROUS:
        return DANGER  # never auto, never allowlistable
    if schema is None:
        return DANGER  # not in the catalog — fail closed
    if schema.read_only:
        return AUTO
    if allowlist is not None and call.name in allowlist:
        return AUTO
    return CONFIRM
