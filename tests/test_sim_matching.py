"""Simulator search realism + grader clarify-with-lookup (harness-fit wave, 2026-06-10)."""

from tooleval.eval.grader import grade_task
from tooleval.eval.task import Expectation, Task
from tooleval.tools.simulator import Simulator, _matches
from tooleval.types import ToolCall, ToolSchema


def _schema(name: str, read_only: bool = False) -> ToolSchema:
    return ToolSchema(name=name, description=name,
                      parameters={"type": "object", "properties": {}},
                      meta={"read_only": read_only})


def test_token_or_matching():
    assert _matches("gym workout", "Gym")
    assert _matches("budget", "Q3 budget review")
    assert not _matches("dentist", "Gym")
    assert _matches("", "anything")  # empty query → no filter


def test_sim_calendar_query_finds_gym():
    sim = Simulator([_schema("calendar.list_events", read_only=True)])
    import json
    out = json.loads(sim.execute(ToolCall("calendar.list_events", {"query": "gym workout"})))
    assert [e["title"] for e in out["events"]] == ["Gym"]


def test_clarify_forgives_readonly_lookup():
    task = Task(id="amb", tier="ambiguous", messages=[{"role": "user", "content": "msg sarah"}],
                expect=Expectation(kind="clarify"))
    offered = {"contacts.find": _schema("contacts.find", read_only=True),
               "messages.send": _schema("messages.send")}
    lookup_then_ask = grade_task(task, [ToolCall("contacts.find", {"name": "Sarah"})], offered)
    assert lookup_then_ask.passed
    guessed = grade_task(task, [ToolCall("messages.send", {"recipient": "Sarah Chen"})], offered)
    assert not guessed.passed


def test_negative_stays_strict_on_readonly():
    task = Task(id="neg", tier="negative", messages=[{"role": "user", "content": "capital?"}],
                expect=Expectation(kind="no_call"))
    offered = {"web.search": _schema("web.search", read_only=True)}
    g = grade_task(task, [ToolCall("web.search", {"query": "capital of France"})], offered)
    assert not g.passed and g.overcalled


def test_sim_readonly_default_is_honest_and_recent_returns_files():
    import json
    sim = Simulator([_schema("files.recent", read_only=True),
                     _schema("network.list_wifi", read_only=True)])
    recent = json.loads(sim.execute(ToolCall("files.recent", {})))
    assert any("Q3_budget" in f for f in recent["files"])
    fallback = json.loads(sim.execute(ToolCall("network.list_wifi", {})))
    assert fallback["result"] is None and "no data" in fallback["note"]


def test_sim_list_dir_filters_by_path():
    import json
    sim = Simulator([_schema("files.list_dir", read_only=True)])
    out = json.loads(sim.execute(ToolCall("files.list_dir", {"path": "~/Downloads"})))
    assert out["files"] == ["~/Downloads/invoice_0425.pdf",
                           "~/Downloads/flight_itinerary.pdf"]


def test_grader_any_of_alternate_satisfies_step():
    from tooleval.eval.task import ExpectedCall
    task = Task(id="t", tier="chain", messages=[{"role": "user", "content": "trash invoice"}],
                expect=Expectation(kind="chain", calls=[
                    ExpectedCall(name="files.search", args={"query": "invoice"},
                                 arg_match={"query": "present"},
                                 any_of=["files.list_dir"]),
                    ExpectedCall(name="files.trash", args={"path": "*"}),
                ]))
    offered = {n: _schema(n) for n in ("files.search", "files.list_dir", "files.trash")}
    g = grade_task(task, [ToolCall("files.list_dir", {"path": "~/Downloads"}),
                          ToolCall("files.trash", {"path": "~/Downloads/invoice_0425.pdf"})],
                   offered)
    assert g.passed  # list_dir is a valid discovery method; its args aren't search args


def test_grader_contact_rule_accepts_resolved_recipient():
    from tooleval.eval.grader import match_value
    assert match_value("Daisy Wong", "Daisy", "contact")        # resolved full name
    assert match_value("daisy@example.com", "Daisy", "contact")  # resolved email
    assert match_value("+1-555-0120", "Daisy", "contact")        # resolved phone
    assert not match_value("Sarah Chen", "Daisy", "contact")     # wrong person


def test_embedding_domain_expansion_name_and_select():
    import numpy as np

    from tooleval.retrieval.embedding import EmbeddingRetriever

    r = EmbeddingRetriever("e5-test", expand_domains=2, cache_dir=None)
    assert r.name == "embedding:e5-test+dom2"
    cat = [ToolSchema(name=f"cal.t{i}", description="x",
                      parameters={"type": "object", "properties": {}},
                      meta={"domain": "calendar"}) for i in range(3)]
    cat += [ToolSchema(name="files.t", description="x",
                       parameters={"type": "object", "properties": {}},
                       meta={"domain": "files"})]
    r._names = [t.name for t in cat]
    r._matrix = np.eye(4, dtype=np.float32)
    r._embed = lambda texts: np.array([[1.0, 0.6, 0.0, 0.7]], dtype=np.float32)
    sel = [t.name for t in r.select("q", cat, 2)]
    # top-2 = cal.t0, files.t; expansion pulls the rest of the calendar domain
    assert sel[:2] == ["cal.t0", "files.t"]
    assert set(sel) == {"cal.t0", "files.t", "cal.t1", "cal.t2"}
