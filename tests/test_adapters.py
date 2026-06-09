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
