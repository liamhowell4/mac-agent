"""`quake` — the terminal REPL for Quake-1.

Runs the agent session in-process (no daemon): read-only tools execute immediately,
mutations ask y/N (dangerous ones require typing `yes`), ask_user renders options.
"""

from __future__ import annotations

import argparse
import json
import sys

from tooleval.providers.ollama import assert_ollama_version

from . import DEFAULT_HOST
from .agent import AgentSession
from .events import (
    AssistantText,
    Done,
    Event,
    NeedsConfirmation,
    NeedsInput,
    ToolFinished,
    ToolStarted,
)
from .runtime import build_session

DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _print_event(ev: Event) -> None:
    if isinstance(ev, ToolStarted):
        args = json.dumps(ev.call.arguments)
        print(f"  {DIM}⏺ {ev.call.name} {args[:120]}{RESET}", flush=True)
    elif isinstance(ev, ToolFinished):
        mark = f"{GREEN}ok{RESET}" if ev.status == "ok" else f"{RED}error{RESET}"
        print(f"  {DIM}  └ {mark}{RESET}", flush=True)
        if ev.hint:
            print(f"  {YELLOW}  ⚠ {ev.hint}{RESET}", flush=True)
    elif isinstance(ev, AssistantText):
        print(ev.text, flush=True)


def _ask_confirmation(ev: NeedsConfirmation) -> bool:
    args = json.dumps(ev.call.arguments, indent=2)
    if ev.danger:
        print(f"  {RED}⚠ DANGEROUS: {ev.call.name}{RESET}\n{args}")
        return input(f"  type {RED}yes{RESET} to run: ").strip() == "yes"
    print(f"  {YELLOW}? {ev.call.name}{RESET} {args}")
    return input("  run this? [y/N] ").strip().lower() in ("y", "yes")


def _ask_input(ev: NeedsInput) -> str:
    print(f"  {YELLOW}? {ev.question}{RESET}")
    for i, opt in enumerate(ev.options, 1):
        print(f"    {i}. {opt}")
    raw = input("  > ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(ev.options):
        return ev.options[int(raw) - 1]
    return raw


def _run_until_done(session: AgentSession, events) -> None:
    while True:
        blocking = None
        for ev in events:
            if isinstance(ev, (NeedsConfirmation, NeedsInput)):
                blocking = ev
            elif isinstance(ev, Done):
                return
            else:
                _print_event(ev)
        if blocking is None:
            return
        if isinstance(blocking, NeedsConfirmation):
            events = session.resume(_ask_confirmation(blocking))
        else:
            events = session.resume(_ask_input(blocking))




def main() -> int:
    ap = argparse.ArgumentParser(prog="quake")
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--sim", action="store_true", help="use the simulator (no real actions)")
    ap.add_argument("--think", default="off", choices=("on", "off"),
                    help="override the model's reasoning channel")
    args = ap.parse_args()

    try:
        assert_ollama_version(args.host)
    except Exception as e:  # noqa: BLE001
        print(f"Ollama not reachable: {e}", file=sys.stderr)
        return 1

    session = build_session(args.model, args.host, sim=args.sim,
                            think=(args.think == "on"))

    from .warmup import warm  # noqa: PLC0415

    model_name = session.provider.model.split('/')[-1]
    print(f"{DIM}quake-1 — warming up {model_name}...{RESET}", flush=True)
    secs = warm(session.provider, session.catalog, system_prompt=session.system_prompt)
    mode = " (simulator)" if args.sim else ""
    print(f"ready in {secs:.1f}s{mode}. /new resets, /quit exits.")

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return 0
        if line == "/new":
            session.reset()
            print(f"{DIM}(new conversation){RESET}")
            continue
        try:
            _run_until_done(session, session.submit(line))
        except KeyboardInterrupt:
            session.cancel()
            print(f"\n{DIM}(cancelled){RESET}")
        except Exception as e:  # noqa: BLE001 — REPL must survive anything
            session.cancel()
            print(f"{RED}error: {e}{RESET}")


if __name__ == "__main__":
    raise SystemExit(main())
