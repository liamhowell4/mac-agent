"""Retriever protocol — pluggable tool selection (the key scaffolding variable)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..types import ToolSchema


@runtime_checkable
class Retriever(Protocol):
    """Selects which tools to offer the model for a given query."""

    name: str

    def select(self, query: str, catalog: list[ToolSchema], k: int) -> list[ToolSchema]:
        """Return the subset of `catalog` to surface (≤ k for top-k retrievers)."""
        ...
