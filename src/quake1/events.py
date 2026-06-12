"""Events emitted by the agent session — the contract between the loop and any UI.

NeedsConfirmation and NeedsInput are BLOCKING: the session suspends after yielding one
and must be resumed with the user's decision (AgentSession.resume).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tooleval.types import ToolCall


@dataclass
class Event:
    pass


@dataclass
class ToolStarted(Event):
    call: ToolCall


@dataclass
class ToolFinished(Event):
    call: ToolCall
    status: str  # "ok" | "error"
    hint: str | None = None  # e.g. the System Settings pane for a TCC denial


@dataclass
class NeedsConfirmation(Event):
    """Blocking. Resume with True, False, or a dict of edited arguments."""

    call: ToolCall
    danger: bool
    schema: dict[str, Any] = field(default_factory=dict)  # OpenAI parameters schema


@dataclass
class NeedsInput(Event):
    """Blocking. Resume with the user's answer string (assistant.ask_user)."""

    question: str
    options: list[str] = field(default_factory=list)


@dataclass
class AssistantText(Event):
    text: str


@dataclass
class Done(Event):
    text: str | None
