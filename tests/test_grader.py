from tooleval.eval.grader import (
    grade_task,
    match_value,
    schema_valid,
)
from tooleval.eval.task import Task
from tooleval.types import ToolCall, ToolSchema

VOL = ToolSchema(
    name="system.set_volume",
    description="Set volume",
    parameters={
        "type": "object",
        "properties": {"level": {"type": "integer"}},
        "required": ["level"],
    },
    meta={"read_only": False},
)
OFFERED = {"system.set_volume": VOL}


def _task(kind, calls, tier="single"):
    return Task.from_dict({
        "id": "t", "tier": tier,
        "messages": [{"role": "user", "content": "x"}],
        "expect": {"kind": kind, "calls": calls},
    })


def test_match_value_rules():
    assert match_value("15:30", "15:30", "exact")
    assert match_value("3:30 PM", "15:30", "normalized")  # time normalization
    assert match_value("Safari", "safari", "normalized")
    assert match_value("anything", "*", "exact")          # wildcard
    assert match_value("whatever", "x", "semantic")        # deferred → not failed


def test_schema_valid_catches_wrong_type_and_missing_required():
    assert schema_valid({"level": 30}, VOL.parameters)
    assert not schema_valid({"level": "loud"}, VOL.parameters)
    assert not schema_valid({}, VOL.parameters)


def test_single_pass():
    task = _task("tool_call", [
        {"name": "system.set_volume", "args": {"level": 30}, "arg_match": {"level": "exact"}}])
    g = grade_task(task, [ToolCall("system.set_volume", {"level": 30})], OFFERED)
    assert g.selection_ok and g.arg_valid and g.arg_correct and g.passed


def test_single_wrong_value_fails_correctness():
    task = _task("tool_call", [
        {"name": "system.set_volume", "args": {"level": 30}, "arg_match": {"level": "exact"}}])
    g = grade_task(task, [ToolCall("system.set_volume", {"level": 99})], OFFERED)
    assert g.selection_ok and not g.arg_correct and not g.passed


def test_negative_abstain_passes():
    task = _task("no_call", [], tier="negative")
    g = grade_task(task, [], OFFERED)
    assert g.abstained and not g.overcalled and g.passed


def test_negative_overcall_fails():
    task = _task("no_call", [], tier="negative")
    g = grade_task(task, [ToolCall("system.set_volume", {"level": 30})], OFFERED)
    assert not g.abstained and g.overcalled and not g.passed


def test_chain_ordered_subsequence():
    task = _task("chain", [
        {"name": "calendar.list_events", "args": {}},
        {"name": "calendar.update_event", "args": {}}], tier="chain")
    # correct order (with an extra interleaved call) still matches as a subsequence
    preds = [
        ToolCall("calendar.list_events", {}),
        ToolCall("calendar.update_event", {}),
    ]
    g = grade_task(task, preds, OFFERED)
    assert g.chain_complete and g.passed
    # wrong order fails
    g2 = grade_task(task, list(reversed(preds)), OFFERED)
    assert not g2.chain_complete
