from tooleval.providers.mlx import parse_text_tool_calls


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


def test_parse_parameters_alias_and_bad_json():
    text = ('<tool_call>{"name": "x", "parameters": {"a": 1}}</tool_call>'
            '<tool_call>{bad}</tool_call>')
    calls = parse_text_tool_calls(text)
    assert len(calls) == 1 and calls[0].arguments == {"a": 1}
