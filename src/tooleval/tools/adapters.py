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
