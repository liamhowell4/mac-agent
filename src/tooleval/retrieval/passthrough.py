"""Passthrough retriever — returns the full catalog.

The deliberately-unfair baseline: it quantifies how much retrieval is worth by showing
how hard a small model degrades when handed all ~100 tools at once. `k` is ignored.
"""

from __future__ import annotations

from ..types import ToolSchema


class PassthroughRetriever:
    name = "passthrough"

    def select(self, query: str, catalog: list[ToolSchema], k: int) -> list[ToolSchema]:
        return list(catalog)
