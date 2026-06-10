"""Smoke-test an MLX model: one tool-call task + one negative, print raw text + parsed calls.

Usage: python scripts/smoke_mlx.py <hf-model-id>
Downloads the model on first run (HF cache). Exits 0 iff the tool task parses to >=1 call
and the negative produces no call.
"""

from __future__ import annotations

import sys

from tooleval.providers.mlx import MLXProvider
from tooleval.tools.catalog import load_catalog
from tooleval.types import Msg

SYSTEM = (
    "You are a helpful assistant on a Mac with access to tools. "
    "Call a tool only when it is the right way to fulfill the user's request. "
    "If no tool applies, answer directly."
)


def main() -> int:
    model = sys.argv[1]
    catalog = load_catalog("data/tools/catalog.json")
    vol = [t for t in catalog if t.name == "system.set_volume"]
    assert vol, "system.set_volume missing from catalog"
    tools = vol + [t for t in catalog if t.name.startswith("calendar.")][:7]
    provider = MLXProvider(model)

    ok = True
    for label, query, want_call in [
        ("tool-call", "Set the volume to 30 percent.", True),
        ("negative", "What's the capital of France?", False),
    ]:
        comp = provider.complete([Msg("system", SYSTEM), Msg("user", query)], tools)
        raw = (comp.raw or {}).get("text", "")
        calls = [c.to_dict() for c in comp.tool_calls]
        got_call = bool(calls)
        status = "OK" if got_call == want_call else "MISMATCH"
        if got_call != want_call:
            ok = False
        print(f"\n[{label}] {status}  ({comp.latency_s:.1f}s)")
        print(f"  raw   : {raw[:400]!r}")
        print(f"  parsed: {calls}")
    provider.unload()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
