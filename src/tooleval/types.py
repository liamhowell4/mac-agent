"""Core dataclasses shared across the harness.

Kept dependency-free and pure so they can be reused by a future fine-tune pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    """A normalized tool call: a tool name plus its argument dict."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}


@dataclass
class Msg:
    """A chat message. `role` ∈ {system, user, assistant, tool}.

    Assistant turns may carry `tool_calls`; tool-result turns carry `tool_name` + content.
    """

    role: str
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str | None = None  # for role == "tool": which tool produced this result

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_name is not None:
            d["tool_name"] = self.tool_name
        return d


@dataclass
class Completion:
    """One model completion (a single provider turn)."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSchema:
    """A canonical (OpenAI function-calling) tool schema plus harness metadata."""

    name: str
    description: str
    parameters: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.meta.get("domain", "other")

    @property
    def read_only(self) -> bool:
        return bool(self.meta.get("read_only", False))

    @property
    def distractor(self) -> bool:
        return bool(self.meta.get("distractor", False))

    @property
    def held_out(self) -> bool:
        return bool(self.meta.get("held_out", False))

    def to_openai(self) -> dict[str, Any]:
        """The canonical function-calling representation passed to providers."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
