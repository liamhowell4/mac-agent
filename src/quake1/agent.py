"""The Quake-1 agent session — a pausable agentic loop over a real (or simulated) executor.

Adapted from tooleval's Runner._run_loop, minus eval concepts (no Task, grading, cache).
Differences that make it a product loop:
  - conversation persists across requests (with history trimming for the 16k context)
  - assistant.ask_user PAUSES the loop (there is a real user to answer), then continues
  - mutating calls PAUSE for confirmation; the answer may carry edited arguments
  - executor is anything with `execute(ToolCall) -> str` (Simulator in tests, real macOS
    executors in production)

The pause mechanism is a plain generator: the loop yields Events; blocking events
(NeedsConfirmation, NeedsInput) suspend it until AgentSession.resume() sends the answer.
This works synchronously for the CLI and via asyncio.to_thread for the daemon.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
from typing import Any, Protocol

from tooleval.eval.runner import PROMPTS
from tooleval.tools.adapters import normalize_args
from tooleval.types import Msg, ToolCall, ToolSchema

from . import safety
from .events import (
    AssistantText,
    Done,
    Event,
    NeedsConfirmation,
    NeedsInput,
    ToolFinished,
    ToolStarted,
)

DEFAULT_TURN_CAP = 6
MAX_RESULT_CHARS = 1000  # tool-result JSON appended to history is truncated to this
ASK_USER = "assistant.ask_user"


class SupportsExecute(Protocol):
    def execute(self, call: ToolCall) -> str: ...


class SupportsComplete(Protocol):
    def complete(self, messages, tools, constrained: bool = False): ...


class AgentSession:
    def __init__(
        self,
        provider: SupportsComplete,
        catalog: list[ToolSchema],
        executor: SupportsExecute,
        *,
        system_prompt: str = PROMPTS["fewshot"],
        turn_cap: int = DEFAULT_TURN_CAP,
        max_history_exchanges: int = 2,
    ):
        self.provider = provider
        self.catalog = catalog
        self.executor = executor
        self.system_prompt = system_prompt
        self.turn_cap = turn_cap
        self.max_history_exchanges = max_history_exchanges
        self._by_name = {t.name: t for t in catalog}
        self._gen: Generator[Event, Any, None] | None = None
        self.blocked = False
        self.messages: list[Msg] = [Msg("system", system_prompt)]

    # ---- public API ----------------------------------------------------------

    def submit(self, user_text: str) -> Iterator[Event]:
        """Start handling a user request. Yields events until Done or a blocking event."""
        if self.blocked:
            raise RuntimeError("session is awaiting resume(); cannot submit")
        self._trim_history()
        self._gen = self._loop(user_text)
        return self._drive(None)

    def resume(self, decision: bool | str | dict) -> Iterator[Event]:
        """Answer the pending blocking event.

        bool  -> NeedsConfirmation approve/decline
        dict  -> NeedsConfirmation approve with edited arguments
        str   -> NeedsInput answer
        """
        if not self.blocked or self._gen is None:
            raise RuntimeError("nothing to resume")
        return self._drive(decision)

    def cancel(self) -> None:
        if self._gen is not None:
            self._gen.close()
        self._gen = None
        self.blocked = False

    def reset(self) -> None:
        self.cancel()
        self.messages = [Msg("system", self.system_prompt)]

    # ---- internals -----------------------------------------------------------

    def _drive(self, send_value: Any) -> Iterator[Event]:
        assert self._gen is not None
        self.blocked = False
        try:
            ev = self._gen.send(send_value)
            while True:
                yield ev
                if isinstance(ev, (NeedsConfirmation, NeedsInput)):
                    self.blocked = True
                    return
                ev = next(self._gen)
        except StopIteration:
            self._gen = None

    def _loop(self, user_text: str) -> Generator[Event, Any, None]:
        self.messages.append(Msg("user", user_text))

        for _turn in range(self.turn_cap):
            comp = self.provider.complete(self.messages, self.catalog, False)
            calls = [normalize_args(c, self._by_name.get(c.name)) for c in comp.tool_calls]

            if not calls:
                text = (comp.text or "").strip() or "(no response)"
                self.messages.append(Msg("assistant", text))
                yield AssistantText(text)
                yield Done(text)
                return

            ask = next((c for c in calls if c.name == ASK_USER), None)
            if ask is not None:
                question = str(ask.arguments.get("question", "")).strip() or "Could you clarify?"
                options = [str(o) for o in (ask.arguments.get("options") or [])]
                answer = yield NeedsInput(question, options)
                self.messages.append(Msg("assistant", question))
                self.messages.append(Msg("user", str(answer)))
                continue

            self.messages.append(Msg("assistant", comp.text, tool_calls=calls))
            for call in calls:
                schema = self._by_name.get(call.name)
                action = safety.classify(call, schema)
                if action != safety.AUTO:
                    decision = yield NeedsConfirmation(
                        call,
                        danger=(action == safety.DANGER),
                        schema=(schema.parameters if schema else {}),
                    )
                    if isinstance(decision, dict):
                        call = ToolCall(call.name, decision)
                        call = normalize_args(call, schema)
                    elif not decision:
                        declined = {"status": "error", "message": "User declined this action."}
                        self.messages.append(
                            Msg("tool", json.dumps(declined), tool_name=call.name))
                        yield ToolFinished(call, "error")
                        continue

                yield ToolStarted(call)
                raw = self.executor.execute(call)
                status, hint = _parse_result(raw)
                self.messages.append(Msg("tool", _truncate(raw), tool_name=call.name))
                yield ToolFinished(call, status, hint)

        text = "I hit my step limit before finishing — want me to keep going?"
        self.messages.append(Msg("assistant", text))
        yield AssistantText(text)
        yield Done(text)

    def _trim_history(self) -> None:
        """Keep system prompt + the last N user-initiated exchanges.

        The 104-tool catalog occupies ~13k of the 16k context; history must stay small.
        """
        # called before the new user message is appended — keep N-1 prior exchanges so
        # the incoming one makes max_history_exchanges total
        keep = self.max_history_exchanges - 1
        user_idxs = [i for i, m in enumerate(self.messages) if m.role == "user"]
        if len(user_idxs) <= keep:
            return
        cut = user_idxs[-keep] if keep else len(self.messages)
        self.messages = [self.messages[0]] + self.messages[cut:]


def _parse_result(raw: str) -> tuple[str, str | None]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "ok", None
    if not isinstance(data, dict):
        return "ok", None
    return str(data.get("status", "ok")), data.get("hint")


def _truncate(raw: str, limit: int = MAX_RESULT_CHARS) -> str:
    # plain marker — appending fake JSON closers produces malformed JSON more often
    # than it repairs it; the model handles a visibly truncated blob fine
    return raw if len(raw) <= limit else raw[:limit] + " …[truncated]"
