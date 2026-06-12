from tooleval.tools.adapters import parse_tool_calls, to_ollama_tools
from tooleval.types import ToolSchema

T = ToolSchema(
    name="system.set_volume",
    description="Set volume",
    parameters={"type": "object", "properties": {"level": {"type": "integer"}},
                "required": ["level"]},
    meta={"domain": "system"},
)


def test_to_ollama_tools_shape():
    out = to_ollama_tools([T])
    assert out == [{
        "type": "function",
        "function": {
            "name": "system.set_volume",
            "description": "Set volume",
            "parameters": {"type": "object", "properties": {"level": {"type": "integer"}},
                           "required": ["level"]},
        },
    }]


def test_parse_tool_calls_ollama_format():
    # Ollama's actual /api/chat shape: {id, function:{index, name, arguments(dict)}}
    raw = [{"id": "call_x", "function": {"index": 0, "name": "system.set_volume",
                                         "arguments": {"level": 30}}}]
    calls = parse_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].name == "system.set_volume"
    assert calls[0].arguments == {"level": 30}


def test_parse_tool_calls_string_arguments():
    raw = [{"function": {"name": "x", "arguments": '{"a": 1}'}}]
    assert parse_tool_calls(raw)[0].arguments == {"a": 1}


def test_parse_tool_calls_bad_json_arguments():
    raw = [{"function": {"name": "x", "arguments": "{not json"}}]
    calls = parse_tool_calls(raw)
    assert calls[0].name == "x"
    assert "_unparsed" in calls[0].arguments


def test_parse_tool_calls_none_and_empty():
    assert parse_tool_calls(None) == []
    assert parse_tool_calls([]) == []


def test_text_from_message_thinking_fallback():
    # LFM2.5 template quirk: final post-tool-result reply lands in `thinking`
    from tooleval.providers.ollama import _text_from_message

    assert _text_from_message({"content": "real reply", "thinking": "hmm"}) == "real reply"
    assert _text_from_message({"content": "", "thinking": "Volume set to 30%."}) == (
        "Volume set to 30%."
    )
    assert _text_from_message({"content": "", "thinking": ""}) is None
    assert _text_from_message({}) is None


def test_normalize_args_coercions():
    from tooleval.tools.adapters import normalize_args
    from tooleval.types import ToolCall

    schema = ToolSchema(
        name="messages.send", description="send",
        parameters={"type": "object", "properties": {
            "recipient": {"type": "string"}, "level": {"type": "integer"},
            "loud": {"type": "boolean"}, "tags": {"type": "array"},
        }},
    )
    call = ToolCall("messages.send", {
        "recipient": ["Alex"], "level": "30", "loud": "true", "tags": ["a"], "extra": "x",
    })
    norm = normalize_args(call, schema)
    assert norm.arguments == {
        "recipient": "Alex", "level": 30, "loud": True, "tags": ["a"], "extra": "x",
    }
    # no schema → untouched
    assert normalize_args(call, None) is call
