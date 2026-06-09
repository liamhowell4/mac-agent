"""Ollama provider — native /api/chat tool path (better tool parsing than /v1 for these models)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..tools.adapters import parse_tool_calls, to_ollama_tools
from ..types import Completion, Msg, ToolSchema

MIN_OLLAMA_VERSION = (0, 20, 2)  # Gemma 4 tool-call parsing requires >= 0.20.2


class OllamaVersionError(RuntimeError):
    pass


def _parse_version(v: str) -> tuple[int, ...]:
    nums: list[int] = []
    for part in v.strip().split("."):
        digits = "".join(c for c in part if c.isdigit())
        nums.append(int(digits) if digits else 0)
    return tuple(nums)


def assert_ollama_version(host: str = "http://localhost:11434") -> str:
    """Fail loud if the Ollama *server* is too old — otherwise we'd benchmark an integration bug."""
    try:
        resp = httpx.get(f"{host}/api/version", timeout=5.0)
        resp.raise_for_status()
        version = resp.json().get("version", "0")
    except httpx.HTTPError as e:
        raise OllamaVersionError(f"Could not reach Ollama at {host}: {e}") from e
    if _parse_version(version) < MIN_OLLAMA_VERSION:
        want = ".".join(map(str, MIN_OLLAMA_VERSION))
        raise OllamaVersionError(
            f"Ollama server {version} < {want} — Gemma 4 tool parsing is broken below this. "
            "Upgrade Ollama, or point at an OpenAI-compatible endpoint."
        )
    return version


class OllamaProvider:
    """Completes chat turns via Ollama's native tool-calling endpoint."""

    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        seed: int = 42,
        temperature: float = 0.0,
        timeout: float = 300.0,
    ):
        self.model = model
        self.name = f"ollama:{model}"
        self.host = host.rstrip("/")
        self.seed = seed
        self.temperature = temperature
        self.timeout = timeout

    def complete(
        self,
        messages: list[Msg],
        tools: list[ToolSchema],
        constrained: bool = False,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "stream": False,
            "options": {"temperature": self.temperature, "seed": self.seed},
        }
        if tools:
            payload["tools"] = to_ollama_tools(tools)
        # NOTE: real args-only constrained decoding is wired in M2. M1 runs unconstrained.

        t0 = time.perf_counter()
        resp = httpx.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
        latency = time.perf_counter() - t0
        resp.raise_for_status()
        data = resp.json()

        msg = data.get("message", {})
        return Completion(
            tool_calls=parse_tool_calls(msg.get("tool_calls")),
            text=msg.get("content") or None,
            latency_s=latency,
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(data.get("eval_count", 0) or 0),
            raw=data,
        )


def _to_wire(m: Msg) -> dict[str, Any]:
    """Msg → Ollama /api/chat message format."""
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or "",
            "tool_calls": [
                {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls
            ],
        }
    if m.role == "tool":
        wire = {"role": "tool", "content": m.content or ""}
        if m.tool_name:
            wire["tool_name"] = m.tool_name
        return wire
    return {"role": m.role, "content": m.content or ""}
