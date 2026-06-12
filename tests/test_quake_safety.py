"""Safety classification table."""

from quake1 import safety
from tooleval.types import ToolCall, ToolSchema


def _schema(name, read_only):
    return ToolSchema(name=name, description=name,
                      parameters={"type": "object", "properties": {}},
                      meta={"read_only": read_only})


def test_classification_table():
    cases = [
        ("calendar.list_events", True, safety.AUTO),
        ("calendar.delete_event", False, safety.CONFIRM),
        ("messages.send", False, safety.CONFIRM),
        ("shell.run_command", False, safety.DANGER),
        ("system.restart_or_shutdown", False, safety.DANGER),
        ("files.delete", False, safety.DANGER),
        ("files.compress", False, safety.DANGER),
    ]
    for name, ro, expected in cases:
        assert safety.classify(ToolCall(name, {}), _schema(name, ro)) == expected, name


def test_unknown_tool_fails_closed():
    assert safety.classify(ToolCall("nope.nope", {}), None) == safety.DANGER


def test_dangerous_wins_even_if_marked_read_only():
    # catalog mistakes must not soften the gate
    assert safety.classify(ToolCall("files.delete", {}), _schema("files.delete", True)) \
        == safety.DANGER


def test_allowlist_promotes_mutating_to_auto_but_never_dangerous():
    from tooleval.types import ToolCall

    send = _schema("messages.send", False)
    assert safety.classify(ToolCall("messages.send", {}), send, set()) == safety.CONFIRM
    assert safety.classify(ToolCall("messages.send", {}), send,
                           {"messages.send"}) == safety.AUTO
    rm = _schema("files.delete", False)
    assert safety.classify(ToolCall("files.delete", {}), rm,
                           {"files.delete"}) == safety.DANGER
