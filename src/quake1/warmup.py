"""Model warmup: pin the model in memory and pre-bake the catalog prompt cache.

The ~13k-token system+catalog prefix costs 10s+ to prefill cold; Ollama caches the KV
for the previous request's prefix, so one dummy turn at startup means every real query
pays only its own tokens. The provider's keep_alive (set at construction) rides every
real request, so the pin renews itself with use; keep_warm covers long idle stretches.
"""

from __future__ import annotations

import time

from tooleval.types import Msg, ToolSchema

from . import KEEP_ALIVE


def warm(provider, catalog: list[ToolSchema], *, system_prompt: str) -> float:
    """Pin the model, then pre-bake the prompt cache. Best-effort; returns seconds."""
    t0 = time.perf_counter()
    try:
        keep_warm(provider)
        provider.complete(
            [Msg("system", system_prompt), Msg("user", "ping")], catalog, False
        )
    except Exception:  # noqa: BLE001 — the first real query just pays the cost instead
        pass
    return time.perf_counter() - t0


def keep_warm(provider) -> None:
    """Renew the residency pin (call periodically from the daemon)."""
    load = getattr(provider, "load", None)
    if load is not None:
        load(KEEP_ALIVE)
