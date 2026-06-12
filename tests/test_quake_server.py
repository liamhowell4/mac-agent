"""Daemon round-trip over a real Unix socket: query → tool events → confirm → done."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from quake1.server import Daemon
from tooleval.types import Completion, ToolCall, ToolSchema


def _schema(name, read_only=False):
    return ToolSchema(name=name, description=name,
                      parameters={"type": "object",
                                  "properties": {"level": {"type": "integer"}}},
                      meta={"read_only": read_only})


class ScriptedProvider:
    name = "fake"

    def __init__(self, turns):
        self.turns = list(turns)

    def complete(self, messages, tools, constrained=False):
        calls, text = self.turns.pop(0)
        return Completion(tool_calls=calls, text=text, latency_s=0,
                          prompt_tokens=1, completion_tokens=1)


class EchoExecutor:
    def execute(self, call):
        return json.dumps({"status": "ok", "echo": call.name})


def make_daemon(turns) -> Daemon:
    d = Daemon.__new__(Daemon)  # skip real model/executor construction
    from quake1.agent import AgentSession

    catalog = [_schema("system.set_volume"), _schema("calendar.list_events", read_only=True)]
    d.session = AgentSession(ScriptedProvider(turns), catalog, EchoExecutor())
    d.catalog = catalog
    d.last_activity = __import__("time").time()
    d.ready = True
    d._reply_q = asyncio.Queue()
    d._active_id = None
    return d


async def _drive(tmp_path, turns, script):
    # AF_UNIX paths are capped (~104 bytes); pytest tmp dirs are too deep
    import tempfile
    sock = Path(tempfile.mkdtemp(prefix="qk", dir="/tmp")) / "t.sock"
    daemon = make_daemon(turns)
    server = await asyncio.start_unix_server(daemon.handle, path=str(sock))
    reader, writer = await asyncio.open_unix_connection(str(sock))

    async def send(msg):
        writer.write((json.dumps(msg) + "\n").encode())
        await writer.drain()

    async def recv():
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        return json.loads(line)

    out = await script(send, recv)
    writer.close()
    server.close()
    return out


@pytest.mark.asyncio
async def test_query_confirm_done_roundtrip(tmp_path):
    turns = [
        ([ToolCall("system.set_volume", {"level": 30})], None),
        ([], "Volume is 30."),
    ]

    async def script(send, recv):
        assert (await recv())["type"] == "status"
        await send({"type": "query", "id": "q1", "text": "set volume to 30"})
        msg = await recv()
        assert msg["type"] == "confirm" and msg["call"]["name"] == "system.set_volume"
        await send({"type": "confirm_reply", "id": "q1", "approved": True,
                    "arguments": {"level": 25}})
        seen = []
        while True:
            m = await recv()
            seen.append(m["type"])
            if m["type"] == "done":
                return seen

    seen = await _drive(tmp_path, turns, script)
    assert "tool_started" in seen and "tool_finished" in seen and seen[-1] == "done"


@pytest.mark.asyncio
async def test_readonly_streams_without_confirm(tmp_path):
    turns = [
        ([ToolCall("calendar.list_events", {})], None),
        ([], "You have 2 events."),
    ]

    async def script(send, recv):
        await recv()  # status
        await send({"type": "query", "id": "q2", "text": "what's on today?"})
        types = []
        while True:
            m = await recv()
            types.append(m["type"])
            if m["type"] == "done":
                return types

    types = await _drive(tmp_path, turns, script)
    assert types == ["tool_started", "tool_finished", "text", "done"]


@pytest.mark.asyncio
async def test_ping_and_unknown(tmp_path):
    async def script(send, recv):
        await recv()
        await send({"type": "ping"})
        assert (await recv())["type"] == "pong"
        await send({"type": "wat"})
        assert (await recv())["type"] == "error"
        return True

    assert await _drive(tmp_path, [], script)
