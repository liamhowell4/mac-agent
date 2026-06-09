"""Ollama provider — native /api/chat tool path (better tool parsing than /v1 for these models)."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..tools.adapters import parse_tool_calls, to_ollama_tools
from ..types import Completion, Msg, ToolCall, ToolSchema

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


def list_loaded(host: str = "http://localhost:11434") -> list[str]:
    """Names of models currently resident in Ollama (via /api/ps)."""
    try:
        resp = httpx.get(f"{host.rstrip('/')}/api/ps", timeout=10.0)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except httpx.HTTPError:
        return []


def unload_all(host: str = "http://localhost:11434") -> list[str]:
    """Evict every loaded model (including the embed model) — full cleanup after a run."""
    host = host.rstrip("/")
    names = list_loaded(host)
    for name in names:
        try:
            httpx.post(f"{host}/api/generate", json={"model": name, "keep_alive": 0}, timeout=30.0)
        except httpx.HTTPError:
            pass
    return names


class OllamaProvider:
    """Completes chat turns via Ollama's native tool-calling endpoint."""

    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        seed: int = 42,
        temperature: float = 0.0,
        timeout: float = 120.0,
        num_ctx: int = 16384,
        num_predict: int = 2048,
    ):
        self.model = model
        self.name = f"ollama:{model}"
        self.host = host.rstrip("/")
        self.seed = seed
        self.temperature = temperature
        self.timeout = timeout
        # Cap the context window so the KV cache fits 16GB. Our largest prompt (103-tool
        # passthrough) is ~13k tokens; 16384 fits every cell and stops Gemma from allocating
        # KV for its huge default window (which swapped a 16GB Mac at ~17.7GB resident).
        self.num_ctx = num_ctx
        # Cap output tokens so a model can't run away in `format` mode (Gemma did exactly this
        # under constrained decoding, generating until it hung the run). Tool calls / clarify
        # replies are short; 2048 is ample.
        self.num_predict = num_predict

    def _options(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "seed": self.seed,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }

    def complete(
        self,
        messages: list[Msg],
        tools: list[ToolSchema],
        constrained: bool = False,
    ) -> Completion:
        if constrained:
            return self._complete_constrained(messages, tools)
        return self._complete_native(messages, tools)

    def _complete_native(self, messages: list[Msg], tools: list[ToolSchema]) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "stream": False,
            "options": self._options(),
        }
        if tools:
            payload["tools"] = to_ollama_tools(tools)
        data = self._post(payload)
        msg = data.get("message", {})
        return Completion(
            tool_calls=parse_tool_calls(msg.get("tool_calls")),
            text=msg.get("content") or None,
            latency_s=data["_latency"],
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(data.get("eval_count", 0) or 0),
            raw=data,
        )

    def _complete_constrained(self, messages: list[Msg], tools: list[ToolSchema]) -> Completion:
        """Args-only constrained mode: force a JSON decision via Ollama `format`.

        The model picks tool_name ∈ offered (or null to abstain) and emits an arguments
        object — output is always schema-valid JSON, and a hallucinated tool name is
        impossible. Per-tool argument *typing* is checked by the grader (arg_validity), not
        the grammar; a tighter per-tool grammar can come with the MLX provider in M4.
        """
        names = [t.name for t in tools]
        schema = {
            "type": "object",
            "properties": {
                "tool_name": {"type": ["string", "null"], "enum": [*names, None]},
                "arguments": {"type": "object"},
                "response_text": {"type": ["string", "null"]},
            },
            "required": ["tool_name"],
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _constrained_wire(messages, tools),
            "stream": False,
            "format": schema,
            "options": self._options(),
        }
        data = self._post(payload)
        content = data.get("message", {}).get("content") or "{}"
        calls, text = _parse_constrained_decision(content)
        return Completion(
            tool_calls=calls,
            text=text,
            latency_s=data["_latency"],
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(data.get("eval_count", 0) or 0),
            raw=data,
        )

    def unload(self) -> None:
        """Evict this model from memory (keep_alive=0) so models don't co-reside on 16GB."""
        try:
            httpx.post(
                f"{self.host}/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=30.0,
            )
        except httpx.HTTPError:
            pass  # best-effort; a failed unload shouldn't abort the run

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        resp = httpx.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
        latency = time.perf_counter() - t0
        resp.raise_for_status()
        data = resp.json()
        data["_latency"] = latency
        return data


def _parse_constrained_decision(content: str) -> tuple[list[ToolCall], str | None]:
    """Parse a constrained-mode JSON decision into (tool_calls, text). Robust to junk output.

    The model is supposed to emit {tool_name, arguments, response_text}, but under `format`
    it sometimes returns a bare int/string/list — those are treated as a no-call text reply.
    """
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        return [], content or None
    if not isinstance(decision, dict):
        return [], content or None
    tool_name = decision.get("tool_name")
    text = decision.get("response_text")
    if tool_name:
        args = decision.get("arguments")
        args = args if isinstance(args, dict) else {}
        return [ToolCall(name=str(tool_name), arguments=args)], None
    return [], text or None


def _render_tools(tools: list[ToolSchema]) -> str:
    lines = []
    for t in tools:
        props = t.parameters.get("properties", {})
        req = set(t.parameters.get("required", []))
        args = ", ".join(f"{k}{'*' if k in req else ''}" for k in props)
        lines.append(f"- {t.name}({args}): {t.description}")
    return "\n".join(lines)


def _constrained_wire(messages: list[Msg], tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Wire messages for constrained mode: tools described in-prompt, calls/results flattened."""
    catalog_msg = {
        "role": "system",
        "content": (
            "Available tools (use the exact name; * marks a required argument):\n"
            + _render_tools(tools)
            + '\n\nReply with a JSON object. To use a tool, set "tool_name" to its exact name '
            'and "arguments" to an object of its arguments. To answer without a tool (or to ask '
            'a clarifying question), set "tool_name" to null and put your reply in '
            '"response_text". Call a tool only when it is the right way to fulfill the request.'
        ),
    }
    wire: list[dict[str, Any]] = []
    inserted = False
    for m in messages:
        if m.role == "system":
            wire.append({"role": "system", "content": m.content or ""})
            continue
        if not inserted:
            wire.append(catalog_msg)
            inserted = True
        if m.role == "assistant" and m.tool_calls:
            calls = "; ".join(f"{c.name}({json.dumps(c.arguments)})" for c in m.tool_calls)
            wire.append({"role": "assistant", "content": f"(called: {calls})"})
        elif m.role == "tool":
            wire.append({"role": "user", "content": f"[result from {m.tool_name}]: {m.content}"})
        else:
            wire.append({"role": m.role, "content": m.content or ""})
    if not inserted:
        wire.append(catalog_msg)
    return wire


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
