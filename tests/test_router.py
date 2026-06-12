"""Router scaffold: act/answer/clarify first pass (decoding="router")."""

import json

from tooleval.eval.runner import Runner
from tooleval.eval.task import Task
from tooleval.types import Completion, ToolCall, ToolSchema


class FakeProvider:
    """Routes per a canned first response, then acts like a simple tool-caller."""

    name = "fake:model"

    def __init__(self, route: str):
        self.route = route
        self.calls = 0

    def complete(self, messages, tools, constrained=False):
        self.calls += 1
        if self.calls == 1 and any("routing classifier" in (m.content or "")
                                   for m in messages if m.role == "system"):
            return Completion(tool_calls=[], text=json.dumps({"route": self.route}),
                              latency_s=0.01, prompt_tokens=10, completion_tokens=5)
        if tools:
            return Completion(tool_calls=[ToolCall("system.set_volume", {"level": 30})],
                              text=None, latency_s=0.01, prompt_tokens=10, completion_tokens=5)
        return Completion(tool_calls=[], text="Paris.", latency_s=0.01,
                          prompt_tokens=10, completion_tokens=5)

    def unload(self):
        pass


class PassthroughRetriever:
    name = "passthrough"

    def select(self, query, catalog, k):
        return catalog


def _catalog():
    return [ToolSchema(name="system.set_volume", description="set volume",
                       parameters={"type": "object", "properties": {"level":
                                   {"type": "integer"}}})]


def _task(tier, kind, content):
    calls = ([{"name": "system.set_volume", "args": {"level": 30},
               "arg_match": {"level": "normalized"}}] if kind == "tool_call" else [])
    return Task.from_dict({"id": f"t_{tier}", "tier": tier,
                           "messages": [{"role": "user", "content": content}],
                           "expect": {"kind": kind, "calls": calls}})


def test_router_answer_blocks_tools():
    r = Runner(FakeProvider("answer"), PassthroughRetriever(), _catalog(),
               decoding="router", cache_dir=None)
    rec = r.run_task(_task("negative", "no_call", "What is the capital of France?"))
    assert rec.grade.passed and rec.predicted_calls == []
    assert rec.trace[0]["route"] == "answer"


def test_router_act_allows_tools():
    r = Runner(FakeProvider("act"), PassthroughRetriever(), _catalog(),
               decoding="router", cache_dir=None)
    rec = r.run_task(_task("single", "tool_call", "Set volume to 30"))
    assert rec.grade.passed
    assert rec.predicted_calls[0]["name"] == "system.set_volume"


def test_router_cell_label_and_fallback():
    r = Runner(FakeProvider("act"), PassthroughRetriever(), _catalog(),
               decoding="router", cache_dir=None)
    assert r.cell.decoding == "router"

    class BrokenProvider(FakeProvider):
        def complete(self, messages, tools, constrained=False):
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("router pass died")
            return super().complete(messages, tools, constrained)

    r2 = Runner(BrokenProvider("act"), PassthroughRetriever(), _catalog(),
                decoding="router", cache_dir=None)
    rec = r2.run_task(_task("single", "tool_call", "Set volume to 30"))
    assert rec.trace[0]["route"] == "act"  # fallback keeps the gate open


def test_router2_clarify_keeps_tools():
    r = Runner(FakeProvider("clarify"), PassthroughRetriever(), _catalog(),
               decoding="router2", cache_dir=None)
    rec = r.run_task(_task("single", "tool_call", "Set volume to 30"))
    assert rec.grade.passed  # misrouted-to-clarify single still acts under router2
    r2 = Runner(FakeProvider("answer"), PassthroughRetriever(), _catalog(),
                decoding="router2", cache_dir=None)
    rec2 = r2.run_task(_task("negative", "no_call", "What is the capital of France?"))
    assert rec2.grade.passed and rec2.predicted_calls == []
