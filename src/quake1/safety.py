"""Execution gating: read-only auto-runs, mutations confirm, dangerous always confirms.

The eval's headline finding was that over-calling/wrong-mutation is the trust-killer;
the runtime enforces what the model can't guarantee. Unknown tools fail closed.
"""

from __future__ import annotations

from tooleval.types import ToolCall, ToolSchema

DANGEROUS = {
    "shell.run_command",
    "system.restart_or_shutdown",
    "files.delete",
    "files.compress",
}

AUTO = "auto"
CONFIRM = "confirm"
DANGER = "danger"


def classify(call: ToolCall, schema: ToolSchema | None) -> str:
    if call.name in DANGEROUS:
        return DANGER
    if schema is None:
        return DANGER  # not in the catalog — fail closed
    return AUTO if schema.read_only else CONFIRM
