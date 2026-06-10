from tooleval.providers.mlx import MLXProvider, parse_text_tool_calls
from tooleval.types import Msg, ToolCall


def test_wire_preserves_turn_order():
    # regression: tool results must stay in conversation position, not move to the end
    msgs = [
        Msg("system", "sys"),
        Msg("user", "do two things"),
        Msg("assistant", None, tool_calls=[ToolCall("a.first", {})]),
        Msg("tool", '{"ok":1}', tool_name="a.first"),
        Msg("assistant", None, tool_calls=[ToolCall("b.second", {})]),
        Msg("tool", '{"ok":2}', tool_name="b.second"),
    ]
    wire = MLXProvider._wire(msgs, native_tool_role=True)
    roles = [w["role"] for w in wire]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "tool"]
    # assistant turns keep their call visible for chain-state tracking
    assert "a.first" in wire[2]["content"]

    folded = MLXProvider._wire(msgs, native_tool_role=False)
    roles = [w["role"] for w in folded]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    assert "result from a.first" in folded[3]["content"]


def test_parse_qwen_tool_call():
    text = ('Sure.\n<tool_call>\n'
            '{"name": "system.set_volume", "arguments": {"level": 30}}\n</tool_call>')
    calls = parse_text_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "system.set_volume"
    assert calls[0].arguments == {"level": 30}


def test_parse_fenced_tool_call():
    text = '```json\n{"name": "calendar.list_events", "arguments": {}}\n```'
    calls = parse_text_tool_calls(text)
    assert calls[0].name == "calendar.list_events"


def test_parse_no_tool_call():
    assert parse_text_tool_calls("Just a plain answer, no tools.") == []


def test_parse_qwen35_xml_tool_call():
    # the format OptiQ actually emits (caught by the smoke test)
    text = ("thinking...\n</think>\n\n<tool_call>\n<function=system.set_volume>\n"
            "<parameter=level>\n30\n</parameter>\n</function>\n</tool_call>")
    calls = parse_text_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "system.set_volume"
    assert calls[0].arguments == {"level": 30}  # coerced to int


def test_parse_qwen35_xml_string_param_and_bare():
    text = ('<function=mail.send>\n<parameter=to>\nbob@x.com\n</parameter>\n'
            '<parameter=subject>\nhi there\n</parameter>\n</function>')
    calls = parse_text_tool_calls(text)
    assert calls[0].name == "mail.send"
    assert calls[0].arguments == {"to": "bob@x.com", "subject": "hi there"}


def test_parse_lfm_pythonic_calls():
    text = ('I will search.\n<|tool_call_start|>'
            '[files.search(query="tax docs", limit=5)]'
            '<|tool_call_end|>')
    calls = parse_text_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "files.search"
    assert calls[0].arguments == {"query": "tax docs", "limit": 5}


def test_parse_lfm_json_variant_and_junk():
    text = ('<|tool_call_start|>'
            '{"name": "calendar.list_events", "arguments": {"date": "today"}}'
            '<|tool_call_end|>')
    calls = parse_text_tool_calls(text)
    assert calls[0].name == "calendar.list_events"
    assert calls[0].arguments == {"date": "today"}
    assert parse_text_tool_calls("<|tool_call_start|>not a call<|tool_call_end|>") == []


def test_parse_parameters_alias_and_bad_json():
    text = ('<tool_call>{"name": "x", "parameters": {"a": 1}}</tool_call>'
            '<tool_call>{bad}</tool_call>')
    calls = parse_text_tool_calls(text)
    assert len(calls) == 1 and calls[0].arguments == {"a": 1}
