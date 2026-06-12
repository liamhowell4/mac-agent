"""AgentSession: pause/resume, confirmation flows, ask_user, history trimming."""

from __future__ import annotations

import json

from quake1.agent import AgentSession
from quake1.events import (
    AssistantText,
    Done,
    NeedsConfirmation,
    NeedsInput,
    ToolFinished,
    ToolStarted,
)
from tooleval.types import Completion, ToolCall, ToolSchema


def _schema(name, read_only=False, props=None):
    return ToolSchema(name=name, description=name,
                      parameters={"type": "object", "properties": props or {}},
                      meta={"read_only": read_only})


CATALOG = [
    _schema("calendar.list_events", read_only=True),
    _schema("calendar.delete_event", props={"event_id": {"type": "string"}}),
    _schema("system.set_volume", props={"level": {"type": "integer"}}),
    _schema("assistant.ask_user", read_only=True),
]


class ScriptedProvider:
    """Returns canned completions in order; records the messages it was called with."""

    name = "fake:scripted"

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def complete(self, messages, tools, constrained=False):
        self.seen.append(list(messages))
        calls, text = self.turns.pop(0)
        return Completion(tool_calls=calls, text=text, latency_s=0.0,
                          prompt_tokens=1, completion_tokens=1)


class EchoExecutor:
    def __init__(self):
        self.executed = []

    def execute(self, call):
        self.executed.append(call)
        return json.dumps({"status": "ok", "echo": call.name})


def _collect(it):
    return list(it)


def test_readonly_autoruns_and_done():
    p = ScriptedProvider([
        ([ToolCall("calendar.list_events", {})], None),
        ([], "You have 3 events."),
    ])
    ex = EchoExecutor()
    s = AgentSession(p, CATALOG, ex)
    evs = _collect(s.submit("what's on my calendar?"))
    kinds = [type(e).__name__ for e in evs]
    assert kinds == ["ToolStarted", "ToolFinished", "AssistantText", "Done"]
    assert ex.executed[0].name == "calendar.list_events"
    assert not s.blocked


def test_mutation_pauses_then_executes_on_approve():
    p = ScriptedProvider([
        ([ToolCall("system.set_volume", {"level": 30})], None),
        ([], "Done, volume is 30."),
    ])
    ex = EchoExecutor()
    s = AgentSession(p, CATALOG, ex)
    evs = _collect(s.submit("set volume to 30"))
    assert isinstance(evs[-1], NeedsConfirmation) and not evs[-1].danger
    assert s.blocked and not ex.executed
    evs2 = _collect(s.resume(True))
    assert any(isinstance(e, ToolStarted) for e in evs2)
    assert isinstance(evs2[-1], Done)
    assert ex.executed[0].arguments == {"level": 30}


def test_decline_feeds_error_so_model_can_replan():
    p = ScriptedProvider([
        ([ToolCall("calendar.delete_event", {"event_id": "evt_1"})], None),
        ([], "Okay, I won't delete it."),
    ])
    ex = EchoExecutor()
    s = AgentSession(p, CATALOG, ex)
    _collect(s.submit("delete my meeting"))
    evs = _collect(s.resume(False))
    assert not ex.executed  # never ran
    finished = next(e for e in evs if isinstance(e, ToolFinished))
    assert finished.status == "error"
    # the model saw the declined-result message
    last_call_msgs = p.seen[-1]
    assert any(m.role == "tool" and "declined" in (m.content or "") for m in last_call_msgs)


def test_confirmation_with_edited_arguments():
    p = ScriptedProvider([
        ([ToolCall("system.set_volume", {"level": 100})], None),
        ([], "Volume set."),
    ])
    ex = EchoExecutor()
    s = AgentSession(p, CATALOG, ex)
    _collect(s.submit("crank it"))
    _collect(s.resume({"level": "40"}))  # edited + string (normalize coerces)
    assert ex.executed[0].arguments == {"level": 40}


def test_ask_user_pauses_and_continues():
    p = ScriptedProvider([
        ([ToolCall("assistant.ask_user",
                   {"question": "Which event?", "options": ["Standup", "Gym"]})], None),
        ([ToolCall("calendar.list_events", {})], None),
        ([], "Cancelled the gym."),
    ])
    ex = EchoExecutor()
    s = AgentSession(p, CATALOG, ex)
    evs = _collect(s.submit("cancel my event"))
    ask = evs[-1]
    assert isinstance(ask, NeedsInput) and ask.options == ["Standup", "Gym"]
    evs2 = _collect(s.resume("Gym"))
    assert isinstance(evs2[-1], Done)
    # the answer entered the conversation as a user message
    assert any(m.role == "user" and m.content == "Gym" for m in p.seen[-1])


def test_turn_cap_yields_graceful_done():
    p = ScriptedProvider([([ToolCall("calendar.list_events", {})], None)] * 6)
    s = AgentSession(p, CATALOG, EchoExecutor(), turn_cap=6)
    evs = _collect(s.submit("loop forever"))
    assert isinstance(evs[-1], Done)
    assert "step limit" in (evs[-1].text or "")


def test_history_trim_keeps_system_and_recent_exchanges():
    p = ScriptedProvider([([], f"answer {i}") for i in range(5)])
    s = AgentSession(p, CATALOG, EchoExecutor(), max_history_exchanges=2)
    for i in range(5):
        _collect(s.submit(f"question {i}"))
    roles = [m.role for m in s.messages]
    assert roles[0] == "system"
    user_contents = [m.content for m in s.messages if m.role == "user"]
    assert user_contents == ["question 3", "question 4"]


def test_unknown_tool_fails_closed_as_danger():
    p = ScriptedProvider([
        ([ToolCall("evil.unknown", {})], None),
        ([], "ok"),
    ])
    s = AgentSession(p, CATALOG, EchoExecutor())
    evs = _collect(s.submit("do something weird"))
    assert isinstance(evs[-1], NeedsConfirmation) and evs[-1].danger


def test_assistant_text_alongside_done():
    p = ScriptedProvider([([], "Paris.")])
    s = AgentSession(p, CATALOG, EchoExecutor())
    evs = _collect(s.submit("capital of France?"))
    assert isinstance(evs[0], AssistantText) and evs[0].text == "Paris."
