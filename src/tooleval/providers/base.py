"""Provider protocol — the pluggable model-adapter interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..types import Completion, Msg, ToolSchema


@runtime_checkable
class Provider(Protocol):
    """A model backend that completes a chat turn, optionally emitting tool calls."""

    name: str

    def complete(
        self,
        messages: list[Msg],
        tools: list[ToolSchema],
        constrained: bool = False,
    ) -> Completion:
        """Run one completion.

        `constrained=True` requests schema-valid JSON *arguments* when the model chooses
        to call — it must NOT force a call (abstention stays possible). Full constrained
        decoding lands in M2; M1 providers may treat it as best-effort.
        """
        ...
