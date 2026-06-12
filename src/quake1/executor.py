"""The real tool executor — same contract as the eval's Simulator: execute(ToolCall) -> str.

Handlers return plain dicts; the envelope ({"status": ...}) and error mapping live here,
so every handler failure becomes model-readable JSON instead of a crash.
"""

from __future__ import annotations

import json

from tooleval.types import ToolCall, ToolSchema

from .executors import ALL_HANDLERS
from .executors._util import ToolError


class Executor:
    def __init__(self, catalog: list[ToolSchema]):
        self._handlers = ALL_HANDLERS
        self._schema = {t.name: t for t in catalog}

    def execute(self, call: ToolCall) -> str:
        if call.name not in self._schema:
            return _err(f"Unknown tool: {call.name}")
        handler = self._handlers.get(call.name)
        if handler is None:
            return _err(f"{call.name} is not supported yet on this Mac.")
        try:
            out = handler(call.arguments if isinstance(call.arguments, dict) else {})
        except ToolError as e:
            return _err(str(e), hint=e.hint)
        except Exception as e:  # noqa: BLE001 — handler bugs must not kill the session
            return _err(f"{type(e).__name__}: {e}")
        if "status" in out:
            return json.dumps(out)
        return json.dumps({"status": "ok", **out})


def _err(message: str, hint: str | None = None) -> str:
    body: dict = {"status": "error", "message": message}
    if hint:
        body["hint"] = hint
    return json.dumps(body)
