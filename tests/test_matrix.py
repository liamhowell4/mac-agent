from tooleval.config import expand_cells
from tooleval.providers.ollama import (
    _constrained_wire,
    _parse_constrained_decision,
    _parse_version,
    _render_tools,
)
from tooleval.types import Msg, ToolCall, ToolSchema

VOL = ToolSchema(
    name="system.set_volume",
    description="Set the system volume.",
    parameters={"type": "object", "properties": {"level": {"type": "integer"}},
                "required": ["level"]},
)


def test_expand_cells_is_full_product():
    cfg = {
        "models": [{"provider": "ollama", "model": "a"}, {"provider": "ollama", "model": "b"}],
        "retrieval": [{"kind": "passthrough"}, {"kind": "embedding", "k": 8}],
        "decoding": ["unconstrained", "constrained"],
        "prompts": ["default"],
    }
    cells = expand_cells(cfg)
    assert len(cells) == 2 * 2 * 2 * 1


def test_expand_cells_defaults():
    cfg = {"models": [{"provider": "ollama", "model": "a"}], "retrieval": [{"kind": "passthrough"}]}
    cells = expand_cells(cfg)
    assert len(cells) == 1  # decoding/prompts default to single values


def test_parse_version_ordering():
    assert _parse_version("0.30.6") > _parse_version("0.20.2")
    assert _parse_version("0.19.0") < _parse_version("0.20.2")
    assert _parse_version("0.20.2") == (0, 20, 2)


def test_render_tools_marks_required():
    text = _render_tools([VOL])
    assert "system.set_volume(level*)" in text


def test_parse_constrained_decision_handles_junk():
    # the bug that crashed cell 8: model returned a bare int under `format`
    assert _parse_constrained_decision("8") == ([], "8")
    assert _parse_constrained_decision("not json") == ([], "not json")
    assert _parse_constrained_decision('[1,2,3]') == ([], "[1,2,3]")
    # abstain: tool_name null → no calls, text returned
    calls, text = _parse_constrained_decision('{"tool_name": null, "response_text": "hi"}')
    assert calls == [] and text == "hi"
    # valid call
    calls, text = _parse_constrained_decision(
        '{"tool_name": "system.set_volume", "arguments": {"level": 30}}')
    assert len(calls) == 1 and calls[0].name == "system.set_volume"
    assert calls[0].arguments == {"level": 30}
    # call with non-dict arguments → coerced to empty dict (no crash)
    calls, _ = _parse_constrained_decision('{"tool_name": "x", "arguments": 5}')
    assert calls[0].arguments == {}


def test_constrained_wire_injects_catalog_and_flattens():
    msgs = [
        Msg("system", "sys prompt"),
        Msg("user", "set volume to 30"),
        Msg("assistant", None, tool_calls=[ToolCall("system.set_volume", {"level": 30})]),
        Msg("tool", '{"status":"ok"}', tool_name="system.set_volume"),
    ]
    wire = _constrained_wire(msgs, [VOL])
    roles = [w["role"] for w in wire]
    # original system kept, tool catalog injected, tool-result flattened to a user turn
    assert roles[0] == "system"
    assert any("Available tools" in w["content"] for w in wire)
    assert any(w["role"] == "user" and "result from system.set_volume" in w["content"]
               for w in wire)
    # the assistant tool-call turn is flattened to text, not structured tool_calls
    assert any(w["role"] == "assistant" and "called:" in w["content"] for w in wire)
