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

import ast
import json
import re
import time
from typing import Any

from ..types import Completion, Msg, ToolCall, ToolSchema

# Qwen-style wrapper; body is either JSON ({"name":..,"arguments":..}) or, on Qwen3.5,
# XML-ish: <function=name><parameter=key>value</parameter>...</function>
_QWEN_TC = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_XML_FN = re.compile(r"<function=([\w.\-]+)>(.*?)</function>", re.S)
_XML_PARAM = re.compile(r"<parameter=([\w.\-]+)>\s*(.*?)\s*</parameter>", re.S)
# Gemma-style: ```tool_code\n{...}\n``` or ```json fenced tool call
_FENCE_TC = re.compile(r"```(?:tool_code|json)?\s*(\{.*?\})\s*```", re.S)
# LFM2-style: <|tool_call_start|>[name(kw=val, ...)]<|tool_call_end|> (Pythonic, or JSON)
_LFM_TC = re.compile(r"<\|tool_call_start\|>\s*(.*?)\s*<\|tool_call_end\|>", re.S)


def _parse_pythonic_calls(blob: str) -> list[ToolCall]:
    """Parse LFM2's Pythonic call list, e.g. ``[files.search(query="tax", limit=5)]``."""
    try:
        tree = ast.parse(blob.strip(), mode="eval")
    except SyntaxError:
        return []
    nodes = tree.body.elts if isinstance(tree.body, ast.List) else [tree.body]
    out: list[ToolCall] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)  # handles dotted names like files.search
        args: dict[str, Any] = {}
        for kw in node.keywords:
            if kw.arg is None:
                continue
            try:
                args[kw.arg] = ast.literal_eval(kw.value)
            except (ValueError, SyntaxError):
                args[kw.arg] = ast.unparse(kw.value)
        out.append(ToolCall(name=name, arguments=args))
    return out


def _coerce(value: str) -> Any:
    """Best-effort typing for XML parameter values ('30' → 30, 'true' → True)."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _parse_xml_calls(blob: str) -> list[ToolCall]:
    """Parse Qwen3.5's XML-ish format: <function=name><parameter=k>v</parameter>...</function>."""
    out: list[ToolCall] = []
    for name, body in _XML_FN.findall(blob):
        args = {k: _coerce(v) for k, v in _XML_PARAM.findall(body)}
        out.append(ToolCall(name=name, arguments=args))
    return out


def _from_obj(obj: Any) -> ToolCall | None:
    if not isinstance(obj, dict) or not obj.get("name"):
        return None
    args = obj.get("arguments", obj.get("parameters", {}))
    return ToolCall(name=obj["name"], arguments=args if isinstance(args, dict) else {})


def parse_text_tool_calls(text: str) -> list[ToolCall]:
    """Extract tool calls from raw model text (Qwen <tool_call>, LFM2, or fenced JSON)."""
    out: list[ToolCall] = []
    for blk in _LFM_TC.findall(text):
        try:  # some LFM2 checkpoints emit JSON inside the markers; most are Pythonic
            loaded = json.loads(blk)
            objs = loaded if isinstance(loaded, list) else [loaded]
            out.extend(c for c in (_from_obj(o) for o in objs) if c)
        except json.JSONDecodeError:
            out.extend(_parse_pythonic_calls(blk))
    if out:
        return out
    for blk in _QWEN_TC.findall(text):
        try:
            call = _from_obj(json.loads(blk))
            if call:
                out.append(call)
        except json.JSONDecodeError:
            out.extend(_parse_xml_calls(blk))
    if out:
        return out
    # bare XML calls (no <tool_call> wrapper), then fenced JSON, as last resorts
    out = _parse_xml_calls(text)
    for blk in _FENCE_TC.findall(text):
        try:
            obj = json.loads(blk)
        except json.JSONDecodeError:
            continue
        call = _from_obj(obj)
        if call:
            out.append(call)
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
        if constrained:
            # No grammar path is wired yet; silently running unconstrained would mislabel
            # results. Skip mlx × constrained cells in the config instead.
            raise NotImplementedError("MLX provider has no constrained-decoding support yet")
        self._ensure_loaded()
        from mlx_lm import generate  # noqa: PLC0415

        # Preserve turn order — moving tool results out of sequence scrambles multi-turn
        # chains. Try the tokenizer's native tool role first; fall back to a user-role
        # rendering IN PLACE if the chat template rejects the tool role.
        tool_schemas = [t.to_openai() for t in tools] if tools else None
        try:
            prompt = self._tokenizer.apply_chat_template(
                self._wire(messages, native_tool_role=True),
                tools=tool_schemas, add_generation_prompt=True, tokenize=False,
            )
        except Exception:  # noqa: BLE001 — template rejected the tool role; render in place
            prompt = self._tokenizer.apply_chat_template(
                self._wire(messages, native_tool_role=False),
                tools=tool_schemas, add_generation_prompt=True, tokenize=False,
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
            prompt_tokens=len(self._tokenizer.encode(prompt)),
            completion_tokens=len(self._tokenizer.encode(text)),
            raw={"text": text},
        )

    @staticmethod
    def _wire(messages: list[Msg], native_tool_role: bool) -> list[dict]:
        """Convert Msgs to chat-template dicts, preserving turn order.

        native_tool_role=True emits {"role": "tool", ...} entries (Qwen-style templates);
        False renders tool results as user turns in the same position (LFM/Gemma-style
        templates that reject the tool role).
        """
        wire: list[dict] = []
        for m in messages:
            if m.role == "tool":
                if native_tool_role:
                    wire.append({"role": "tool", "name": m.tool_name,
                                 "content": m.content or ""})
                else:
                    wire.append({"role": "user",
                                 "content": f"[result from {m.tool_name}]: {m.content}"})
            elif m.role == "assistant" and m.tool_calls:
                # keep the call visible in-history so the model can track chain state
                calls = "; ".join(f"{c.name}({json.dumps(c.arguments)})" for c in m.tool_calls)
                content = (m.content or "").strip()
                wire.append({"role": "assistant",
                             "content": (content + f"\n[called: {calls}]").strip()})
            else:
                wire.append({"role": m.role, "content": m.content or ""})
        return wire

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
