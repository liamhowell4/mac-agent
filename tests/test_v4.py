"""v4: ask_user terminal clarify, recipient disambiguation, error-forgiven clarify grading."""

import json

from tooleval.eval.grader import grade_task
from tooleval.eval.runner import Runner
from tooleval.eval.task import Expectation, Task
from tooleval.tools.adapters import normalize_args
from tooleval.tools.simulator import Simulator
from tooleval.types import Completion, ToolCall, ToolSchema


def _schema(name, read_only=False, props=None):
    return ToolSchema(name=name, description=name,
                      parameters={"type": "object", "properties": props or {}},
                      meta={"read_only": read_only})


def test_msg_send_ambiguous_recipient_errors():
    sim = Simulator([_schema("messages.send")])
    out = json.loads(sim.execute(ToolCall("messages.send", {"recipient": "Sarah", "body": "hi"})))
    assert out["status"] == "error" and "Sarah Chen" in out["message"]
    ok = json.loads(sim.execute(ToolCall("messages.send", {"recipient": "Daisy", "body": "hi"})))
    assert ok["status"] == "ok"


def test_music_search_returns_results():
    sim = Simulator([_schema("music.search", read_only=True)])
    out = json.loads(sim.execute(ToolCall("music.search", {"query": "chill"})))
    assert out["results"]


def test_clarify_forgives_runtime_rejected_mutation():
    task = Task(id="amb", tier="ambiguous", messages=[{"role": "user", "content": "text sarah"}],
                expect=Expectation(kind="clarify"))
    offered = {"messages.send": _schema("messages.send")}
    calls = [ToolCall("messages.send", {"recipient": "Sarah"})]
    assert grade_task(task, calls, offered, statuses=["error"]).passed
    assert not grade_task(task, calls, offered, statuses=["ok"]).passed


def test_scalar_to_array_coercion():
    schema = _schema("mail.forward", props={"to": {"type": "array"}})
    norm = normalize_args(ToolCall("mail.forward", {"to": "Daisy"}), schema)
    assert norm.arguments == {"to": ["Daisy"]}


class AskProvider:
    name = "fake:ask"

    def complete(self, messages, tools, constrained=False):
        return Completion(
            tool_calls=[ToolCall("assistant.ask_user",
                                 {"question": "Which Sarah?",
                                  "options": ["Sarah Chen", "Sarah Kim"]})],
            text=None, latency_s=0.01, prompt_tokens=5, completion_tokens=5)

    def unload(self):
        pass


class Passthrough:
    name = "passthrough"

    def select(self, query, catalog, k):
        return catalog


def test_ask_user_is_terminal_and_passes_clarify():
    catalog = [_schema("assistant.ask_user", read_only=True), _schema("messages.send")]
    r = Runner(AskProvider(), Passthrough(), catalog, cache_dir=None)
    task = Task(id="amb2", tier="ambiguous", messages=[{"role": "user", "content": "text sarah"}],
                expect=Expectation(kind="clarify"))
    rec = r.run_task(task)
    assert rec.turns == 1  # terminal — no loop continuation
    assert rec.grade.passed
    assert "Which Sarah?" in rec.trace[0]["text"]


def test_mail_read_returns_body():
    sim = Simulator([_schema("mail.read_message", read_only=True)])
    out = json.loads(sim.execute(ToolCall("mail.read_message", {"message_id": "m_002"})))
    assert "lunch" in out["message"]["body"].lower()
    err = json.loads(sim.execute(ToolCall("mail.read_message", {"message_id": "nope"})))
    assert err["status"] == "error"


def test_ollama_recovers_unparsed_content_tool_call():
    from tooleval.providers.ollama import OllamaProvider

    p = OllamaProvider("fake")
    p._post = lambda payload: {
        "message": {"content": ('Let me search.\n<tool_call>\n<function=mail.search>\n'
                                '<parameter=query>\ndaisy\n</parameter>\n</function>\n'
                                '</tool_call>'), "tool_calls": None},
        "_latency": 0.01, "prompt_eval_count": 1, "eval_count": 1,
    }
    tools = [_schema("mail.search"), _schema("mail.reply")]
    comp = p.complete([], tools)
    assert [c.name for c in comp.tool_calls] == ["mail.search"]
    assert comp.tool_calls[0].arguments == {"query": "daisy"}
    # junk names can't smuggle through
    p._post = lambda payload: {
        "message": {"content": '<tool_call>{"name": "rm.rf", "arguments": {}}</tool_call>',
                    "tool_calls": None},
        "_latency": 0.01, "prompt_eval_count": 1, "eval_count": 1,
    }
    assert p.complete([], tools).tool_calls == []


def test_provider_sampling_options_in_name_and_payload():
    from tooleval.providers.ollama import OllamaProvider

    p = OllamaProvider("m", extra_options={"temperature": 0.7, "top_k": 20})
    assert p.name == "ollama:m@temperature=0.7,top_k=20"
    opts = p._options()
    assert opts["temperature"] == 0.7 and opts["top_k"] == 20
    assert OllamaProvider("m").name == "ollama:m"


def test_ollama_recovers_call_from_thinking_when_content_empty():
    from tooleval.providers.ollama import OllamaProvider

    p = OllamaProvider("fake")
    p._post = lambda payload: {
        "message": {"content": "", "tool_calls": None,
                    "thinking": ('Now I will delete it.\n<tool_call>\n'
                                 '<function=calendar.delete_event>\n<parameter=event_id>\n'
                                 'evt_gym\n</parameter>\n</function>\n</tool_call>')},
        "_latency": 0.01, "prompt_eval_count": 1, "eval_count": 1,
    }
    tools = [_schema("calendar.delete_event")]
    comp = p.complete([], tools)
    assert [c.name for c in comp.tool_calls] == ["calendar.delete_event"]
    # non-empty content means thinking is real deliberation — must NOT be mined for calls
    p._post = lambda payload: {
        "message": {"content": "I decided not to act.", "tool_calls": None,
                    "thinking": '<tool_call>{"name": "calendar.delete_event", '
                                '"arguments": {}}</tool_call>'},
        "_latency": 0.01, "prompt_eval_count": 1, "eval_count": 1,
    }
    assert p.complete([], tools).tool_calls == []
