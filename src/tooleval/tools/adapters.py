"""Convert canonical tool schemas ↔ model-native format, and parse output back.

For the Ollama native /api/chat path, the canonical OpenAI function schema is already
accepted as-is, and tool calls come back structured — so adapting is light here. The
abstraction exists so MLX / raw-text providers (which need real parsing) slot in later.
"""

from __future__ import annotations

import json
from typing import Any

from ..types import ToolCall, ToolSchema


def to_ollama_tools(schemas: list[ToolSchema]) -> list[dict[str, Any]]:
    """Canonical → the `tools` array Ollama's /api/chat expects."""
    return [s.to_openai() for s in schemas]


def parse_tool_calls(raw_calls: list[dict[str, Any]] | None) -> list[ToolCall]:
    """Normalize Ollama tool_calls into ToolCall objects.

    Arguments may arrive as a dict (Ollama usually parses them) or a JSON string.
    """
    out: list[ToolCall] = []
    for call in raw_calls or []:
        fn = call.get("function", call)
        name = fn.get("name", "")
        args = fn.get("arguments", {})
        if isinstance(args, str):
            args = _safe_json(args)
        if not isinstance(args, dict):
            args = {"_raw": args}
        out.append(ToolCall(name=name, arguments=args))
    return out


def _safe_json(s: str) -> Any:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {"_unparsed": s}


def normalize_args(call: ToolCall, schema: ToolSchema | None) -> ToolCall:
    """Schema-aware argument coercion — the forgiving-runtime layer a real agent would ship.

    Models emit near-miss arg types (`["Alex"]` for a string recipient, `"30"` for an
    integer level); a production tool dispatcher coerces these rather than erroring, so
    the eval's simulated runtime does too. Only unambiguous coercions are applied:
      - single-element list → scalar when the schema expects a scalar type
      - numeric string → int/float when the schema expects integer/number
      - "true"/"false" string → bool when the schema expects boolean
    Anything else (unknown tool, unknown param, lossy casts) passes through untouched.
    """
    if schema is None or not isinstance(call.arguments, dict):
        return call
    props = schema.parameters.get("properties", {})
    out: dict[str, Any] = {}
    for key, val in call.arguments.items():
        typ = (props.get(key) or {}).get("type")
        if isinstance(val, list) and len(val) == 1 and typ in (
            "string", "integer", "number", "boolean",
        ):
            val = val[0]
        elif typ == "array" and isinstance(val, (str, int, float)):
            val = [val]  # mail.forward(to="Daisy") → to=["Daisy"]; the reverse coercion
        if typ == "integer" and isinstance(val, str):
            try:
                val = int(val)
            except ValueError:
                pass
        elif typ == "number" and isinstance(val, str):
            try:
                val = float(val)
            except ValueError:
                pass
        elif typ == "boolean" and isinstance(val, str) and val.lower() in ("true", "false"):
            val = val.lower() == "true"
        out[key] = val
    return ToolCall(name=call.name, arguments=out)
