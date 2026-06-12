"""Model warmup: pin the model in memory and pre-bake the catalog prompt cache.

The ~13k-token system+catalog prefix costs 10s+ to prefill cold; Ollama caches the KV
for the previous request's prefix, so one dummy turn at startup means every real query
pays only its own tokens. keep_alive pins the weights between requests.
"""

from __future__ import annotations

import time

import httpx

from tooleval.types import Msg, ToolSchema

KEEP_ALIVE = "60m"


def warm(provider, catalog: list[ToolSchema], *, system_prompt: str) -> float:
    """Load the model with a long keep_alive, then pre-bake the prompt cache.

    Returns elapsed seconds. Failures are non-fatal — the first real query just
    pays the cost instead.
    """
    t0 = time.perf_counter()
    try:
        httpx.post(
            f"{provider.host}/api/chat",
            json={"model": provider.model, "messages": [], "keep_alive": KEEP_ALIVE},
            timeout=120.0,
        )
        provider.complete(
            [Msg("system", system_prompt), Msg("user", "ping")], catalog, False
        )
    except Exception:  # noqa: BLE001 — warmup is best-effort
        pass
    return time.perf_counter() - t0


def keep_warm(provider) -> None:
    """Re-ping keep_alive (call periodically, e.g. every 30 min, from the daemon)."""
    try:
        httpx.post(
            f"{provider.host}/api/chat",
            json={"model": provider.model, "messages": [], "keep_alive": KEEP_ALIVE},
            timeout=30.0,
        )
    except Exception:  # noqa: BLE001
        pass
