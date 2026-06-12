"""quake-daemon — NDJSON over a Unix domain socket, for the SwiftUI app.

One client at a time, one AgentSession. Blocking provider/executor steps run in a worker
thread; events stream to the client as they happen. Blocking events (confirm/ask) hold
the session until the matching reply arrives.

Protocol (every message is one JSON line with a "type"):
  C->S  {"type":"query","id":...,"text":...}
        {"type":"confirm_reply","id":...,"approved":bool,"arguments":{...}?}
        {"type":"ask_reply","id":...,"answer":...}
        {"type":"reset"} {"type":"ping"} {"type":"cancel","id":...}
  S->C  {"type":"status","state":"warming"|"ready"}
        {"type":"tool_started","id":...,"call":{...}}
        {"type":"tool_finished","id":...,"call":{...},"status":...,"hint":...?}
        {"type":"confirm","id":...,"call":{...},"danger":bool,"schema":{...}}
        {"type":"ask","id":...,"question":...,"options":[...]}
        {"type":"text","id":...,"text":...}
        {"type":"done","id":...,"text":...?}
        {"type":"pong"} {"type":"error","id":...?,"message":...}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from . import __version__
from .agent import AgentSession
from .events import (
    AssistantText,
    Done,
    NeedsConfirmation,
    NeedsInput,
    ToolFinished,
    ToolStarted,
)
from .runtime import build_session

APP_DIR = Path("~/Library/Application Support/Quake1").expanduser()
SOCKET_PATH = APP_DIR / "quake1.sock"
INFO_PATH = APP_DIR / "daemon.json"
IDLE_RESET_S = 300  # fresh conversation if the user has been away > 5 min


def _to_wire(ev, qid: str) -> dict:
    """Event -> protocol message. One mapping, used for every server push."""
    if isinstance(ev, ToolStarted):
        return {"type": "tool_started", "id": qid, "call": ev.call.to_dict()}
    if isinstance(ev, ToolFinished):
        msg = {"type": "tool_finished", "id": qid, "call": ev.call.to_dict(),
               "status": ev.status}
        if ev.hint:
            msg["hint"] = ev.hint
        return msg
    if isinstance(ev, NeedsConfirmation):
        return {"type": "confirm", "id": qid, "call": ev.call.to_dict(),
                "danger": ev.danger, "schema": ev.schema}
    if isinstance(ev, NeedsInput):
        return {"type": "ask", "id": qid, "question": ev.question, "options": ev.options}
    if isinstance(ev, AssistantText):
        return {"type": "text", "id": qid, "text": ev.text}
    if isinstance(ev, Done):
        return {"type": "done", "id": qid, "text": ev.text}
    return {"type": "error", "id": qid, "message": f"unknown event {type(ev).__name__}"}


class Daemon:
    def __init__(self, session: AgentSession | None = None):
        self.session = session or build_session()
        self.catalog = self.session.catalog
        self.last_activity = time.time()
        self.ready = False
        self._reply_q: asyncio.Queue[Any] = asyncio.Queue()

    # ---- session driving (worker thread per step) -----------------------------

    async def _stream(self, send, qid: str, events_fn) -> None:
        """Drive the session in a thread, pushing each event AS IT IS YIELDED.

        Buffering until the step finishes would hold every tool_started behind
        multi-second model turns — the panel would look dead. A queue bridges the
        worker thread to the event loop per event; only blocking decisions round-trip.
        """
        loop = asyncio.get_running_loop()
        bridge: asyncio.Queue = asyncio.Queue()
        _END = object()

        def run_step(fn):
            try:
                for ev in fn():
                    loop.call_soon_threadsafe(bridge.put_nowait, ev)
            finally:
                loop.call_soon_threadsafe(bridge.put_nowait, _END)

        step = events_fn
        last_text: str | None = None
        while True:
            blocking = None
            done = False
            worker = loop.run_in_executor(None, run_step, step)
            while True:
                ev = await bridge.get()
                if ev is _END:
                    break
                if isinstance(ev, Done) and ev.text and ev.text == last_text:
                    ev = Done(None)  # client already rendered this text — don't duplicate
                if isinstance(ev, AssistantText):
                    last_text = ev.text
                await send(_to_wire(ev, qid))
                if isinstance(ev, (NeedsConfirmation, NeedsInput)):
                    blocking = ev
                elif isinstance(ev, Done):
                    done = True
            await worker
            if done or blocking is None:
                return
            decision = await self._reply_q.get()
            if decision is None:  # cancelled while awaiting the user's decision
                self.session.cancel()
                await send({"type": "done", "id": qid, "text": None})
                return

            def step(d=decision):
                return self.session.resume(d)

    # ---- connection handling ---------------------------------------------------

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        out_q: asyncio.Queue[dict] = asyncio.Queue()

        async def send(msg: dict) -> None:
            await out_q.put(msg)

        async def writer_task():
            while True:
                msg = await out_q.get()
                writer.write((json.dumps(msg) + "\n").encode())
                await writer.drain()

        wtask = asyncio.create_task(writer_task())
        await send({"type": "status", "state": "ready" if self.ready else "warming"})
        stream_task: asyncio.Task | None = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    await send({"type": "error", "message": "bad json"})
                    continue
                mtype = msg.get("type")
                if mtype == "ping":
                    await send({"type": "pong"})
                elif mtype == "reset":
                    if stream_task:
                        stream_task.cancel()
                    self.session.reset()
                    self.last_activity = time.time()
                elif mtype == "cancel":
                    # single teardown path: if the session is parked on a decision the
                    # sentinel unblocks _stream (which cancels + sends done); otherwise
                    # cancel the task mid-inference and clean up here.
                    if self.session.blocked:
                        await self._reply_q.put(None)
                    elif stream_task and not stream_task.done():
                        stream_task.cancel()
                        self.session.cancel()
                        await send({"type": "done", "id": msg.get("id"), "text": None})
                elif mtype == "query":
                    if stream_task and not stream_task.done():
                        await send({"type": "error", "id": msg.get("id"),
                                    "message": "busy with a previous query"})
                        continue
                    if time.time() - self.last_activity > IDLE_RESET_S:
                        self.session.reset()
                    self.last_activity = time.time()
                    qid = str(msg.get("id") or "q")
                    text = str(msg.get("text") or "").strip()
                    if not text:
                        continue
                    stream_task = asyncio.create_task(self._run_query(send, qid, text))
                elif mtype == "confirm_reply":
                    self.last_activity = time.time()
                    args = msg.get("arguments")
                    if msg.get("approved") and isinstance(args, dict):
                        await self._reply_q.put(args)
                    else:
                        await self._reply_q.put(bool(msg.get("approved")))
                elif mtype == "ask_reply":
                    self.last_activity = time.time()
                    await self._reply_q.put(str(msg.get("answer") or ""))
                else:
                    await send({"type": "error", "message": f"unknown type {mtype!r}"})
        finally:
            if stream_task:
                stream_task.cancel()
            wtask.cancel()
            with contextlib.suppress(Exception):
                writer.close()

    async def _run_query(self, send, qid: str, text: str) -> None:
        try:
            await self._stream(send, qid, lambda: self.session.submit(text))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — surface, don't die
            self.session.cancel()
            await send({"type": "error", "id": qid, "message": f"{type(e).__name__}: {e}"})
            await send({"type": "done", "id": qid, "text": None})

    # ---- lifecycle ---------------------------------------------------------------

    async def warmup(self) -> None:
        from .warmup import warm  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: warm(self.session.provider, self.catalog,
                               system_prompt=self.session.system_prompt))
        self.ready = True

    async def keep_warm_loop(self) -> None:
        from .warmup import keep_warm  # noqa: PLC0415

        while True:
            await asyncio.sleep(1800)
            await asyncio.get_running_loop().run_in_executor(
                None, keep_warm, self.session.provider)


async def _socket_in_use(socket_path: Path) -> bool:
    """True if a live daemon already serves this socket (stale files connect-refuse)."""
    try:
        _, w = await asyncio.wait_for(
            asyncio.open_unix_connection(str(socket_path)), timeout=1.0)
        w.close()
        return True
    except (TimeoutError, OSError):
        return False


async def amain(daemon: Daemon | None = None, socket_path: Path = SOCKET_PATH) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists() and await _socket_in_use(socket_path):
        # single-instance: another daemon is serving — the app may spawn eagerly
        return
    daemon = daemon or Daemon()
    socket_path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(daemon.handle, path=str(socket_path))
    os.chmod(socket_path, 0o600)
    INFO_PATH.write_text(json.dumps(
        {"pid": os.getpid(), "version": __version__, "started": int(time.time())}))
    warm_task = asyncio.create_task(daemon.warmup())
    keep_task = asyncio.create_task(daemon.keep_warm_loop())
    try:
        async with server:
            await server.serve_forever()
    finally:
        warm_task.cancel()
        keep_task.cancel()
        socket_path.unlink(missing_ok=True)
        INFO_PATH.unlink(missing_ok=True)


def main() -> int:
    import signal
    import sys

    # SIGTERM must clean the socket up like ctrl-c does, or the next client
    # connects to a stale file
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        asyncio.run(amain())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        SOCKET_PATH.unlink(missing_ok=True)
        INFO_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
