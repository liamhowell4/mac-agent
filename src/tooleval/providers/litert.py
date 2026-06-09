"""LiteRT-LM provider scaffold (M4) — Gemma 4 via Google's on-device runtime.

Rationale: if the product ships Gemma on LiteRT-LM, the eval should test *that runtime*, since
the runtime affects tool-call formatting/parsing (the same reason the brief pins the Ollama
version for Gemma tool parsing). MTP gives faster decode, but the eval value is fidelity, not
speed (tool-call outputs are short).

This is a scaffold: it reuses the Gemma text-tool-call parser, but the actual decode call needs
the litert-lm runtime + a `.litertlm`/`.task` Gemma model, which must be installed/converted in
the user's environment. Wire `_generate` to the litert-lm Python API to activate.
"""

from __future__ import annotations

from ..types import Completion, Msg, ToolSchema
from .mlx import parse_text_tool_calls


class LiteRTProvider:
    def __init__(self, model_path: str, max_tokens: int = 2048, temperature: float = 0.0):
        self.model_path = model_path
        self.name = f"litert:{model_path}"
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._engine = None

    def _generate(self, prompt: str) -> str:
        # TODO: bind to the litert-lm Python API, e.g.:
        #   from litert_lm import Engine
        #   self._engine = self._engine or Engine(self.model_path)
        #   return self._engine.generate(prompt, max_tokens=self.max_tokens, temperature=...)
        raise NotImplementedError(
            "LiteRTProvider is a scaffold. Install the litert-lm runtime + a Gemma .litertlm "
            "model and implement _generate() against the litert-lm Python API to activate."
        )

    def complete(
        self, messages: list[Msg], tools: list[ToolSchema], constrained: bool = False
    ) -> Completion:
        prompt = _render_gemma_prompt(messages, tools)
        text = self._generate(prompt)
        calls = parse_text_tool_calls(text)
        return Completion(tool_calls=calls, text=None if calls else text, raw={"text": text})

    def unload(self) -> None:
        self._engine = None


def _render_gemma_prompt(messages: list[Msg], tools: list[ToolSchema]) -> str:
    """Minimal Gemma-style prompt with tools inline (placeholder for the real template)."""
    lines = []
    if tools:
        lines.append("Available tools:")
        for t in tools:
            lines.append(f"- {t.name}: {t.description}")
        lines.append("")
    for m in messages:
        if m.role == "tool":
            lines.append(f"[tool result from {m.tool_name}]: {m.content}")
        else:
            lines.append(f"{m.role}: {m.content or ''}")
    return "\n".join(lines)
