"""MLX provider (M4) — runs HF MLX models via mlx_lm, parsing tool calls from raw text.

This is the raw-text provider path: tools are rendered into the prompt via the model's chat
template (with `tools=`), the model generates freely, and we parse tool calls back out of the
text. Qwen emits ``<tool_call>{...}</tool_call>`` blocks; Gemma uses ```tool_code fences. Both
are handled below.

Lazy-imports mlx_lm so the rest of the harness runs without it installed. To use:
    uv pip install mlx_lm
Targets the HF MLX models the user flagged, e.g.:
    mlx-community/Qwen3.5-4B-OptiQ-4bit                                  (clean baseline)
    Jackrong/MLX-Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-v2-8bit  (distillation cell)

Constrained decoding via grammar is not yet wired here (best-effort unconstrained for now);
a per-tool grammar can be added with mlx_lm's logit processors.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ..types import Completion, Msg, ToolCall, ToolSchema

# Qwen-style: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
_QWEN_TC = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
# Gemma-style: ```tool_code\n{...}\n``` or ```json fenced tool call
_FENCE_TC = re.compile(r"```(?:tool_code|json)?\s*(\{.*?\})\s*```", re.S)


def parse_text_tool_calls(text: str) -> list[ToolCall]:
    """Extract tool calls from raw model text (Qwen <tool_call> or fenced JSON)."""
    out: list[ToolCall] = []
    blocks = _QWEN_TC.findall(text) or _FENCE_TC.findall(text)
    for blk in blocks:
        try:
            obj = json.loads(blk)
        except json.JSONDecodeError:
            continue
        name = obj.get("name")
        if not name:
            continue
        args = obj.get("arguments", obj.get("parameters", {}))
        out.append(ToolCall(name=name, arguments=args if isinstance(args, dict) else {}))
    return out


class MLXProvider:
    """mlx_lm generate + raw-text tool-call parsing. Lazy-loads the model on first use."""

    def __init__(
        self,
        model: str,
        seed: int = 42,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ):
        self.model = model
        self.name = f"mlx:{model}"
        self.seed = seed
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from mlx_lm import load  # noqa: PLC0415 — lazy so the harness imports without mlx_lm
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "mlx_lm not installed — `uv pip install mlx_lm` to use the MLX provider."
            ) from e
        self._model, self._tokenizer = load(self.model)

    def complete(
        self,
        messages: list[Msg],
        tools: list[ToolSchema],
        constrained: bool = False,
    ) -> Completion:
        self._ensure_loaded()
        from mlx_lm import generate  # noqa: PLC0415

        wire = [{"role": m.role, "content": m.content or ""} for m in messages if m.role != "tool"]
        # tool results: fold into a user turn (mlx chat templates vary in tool-role support)
        for m in messages:
            if m.role == "tool":
                wire.append(
                    {"role": "user", "content": f"[result from {m.tool_name}]: {m.content}"}
                )
        tool_schemas = [t.to_openai() for t in tools] if tools else None
        prompt = self._tokenizer.apply_chat_template(
            wire, tools=tool_schemas, add_generation_prompt=True, tokenize=False
        )

        t0 = time.perf_counter()
        text = generate(
            self._model, self._tokenizer, prompt=prompt,
            max_tokens=self.max_tokens, verbose=False,
        )
        latency = time.perf_counter() - t0

        calls = parse_text_tool_calls(text)
        return Completion(
            tool_calls=calls,
            text=None if calls else text.strip() or None,
            latency_s=latency,
            prompt_tokens=0,
            completion_tokens=0,
            raw={"text": text},
        )

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        try:
            import mlx.core as mx  # noqa: PLC0415
            mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass


def _opts_doc() -> dict[str, Any]:  # kept for symmetry/inspection
    return {"temperature": 0.0}
