"""Orchestrates the model × scaffold × task run via a real agentic loop.

The loop: model emits tool calls → simulator executes against seeded state → results are
fed back → repeat until the model emits no call (natural stop, and the win condition for
negative/ambiguous) or hits the turn cap (cap-hit = fail). We grade the call, not side effects.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..providers.base import Provider
from ..retrieval.base import Retriever
from ..tools.adapters import normalize_args
from ..tools.simulator import Simulator
from ..types import Completion, Msg, ToolCall, ToolSchema
from .grader import GradeResult, grade_task
from .task import Task

DEFAULT_TURN_CAP = 6
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant on a Mac with access to tools. "
    "Call a tool only when it is the right way to fulfill the user's request. "
    "If no tool applies, answer directly. If the request is ambiguous, ask a clarifying "
    "question instead of guessing. When you have what you need, stop calling tools and reply."
)

# Prompt registry — config `prompts:` entries are labels into this map. The label is part
# of the cell identity (and thus the result-cache key), so two prompts never share cache.
PROMPTS = {
    "default": DEFAULT_SYSTEM_PROMPT,
    "restraint": (
        "You are a helpful assistant on a Mac with access to tools. "
        "Only call a tool when the request clearly needs one; answer chit-chat and "
        "known facts directly, without tools. If the request is underspecified, or an "
        "entity is ambiguous or unknown, ask a clarifying question instead of guessing. "
        "After a tool result, continue any remaining steps of the request before replying."
    ),
    # Worked examples — show, don't tell. Targets the same failure portrait as tool_first
    # but by demonstration; costs prompt tokens (fine: LFM is fast).
    "fewshot": (
        "You are a helpful assistant on a Mac with access to tools. Call a tool only when "
        "it is the right way to fulfill the request. Follow the style of these examples:\n\n"
        'Example 1 — known fact, NO tool:\nUser: "What\'s the capital of Japan?"\n'
        'Assistant: "Tokyo." (answered from knowledge — no tool call, no web search)\n\n'
        "Example 2 — multi-step request, complete EVERY step:\n"
        'User: "Find the report and email it to Sam."\n'
        "Assistant: calls files.search(query=\"report\"); after seeing the result, calls "
        'mail.send(to="Sam", ...); only then replies "Sent!". Never stop after the first '
        "call while steps remain.\n\n"
        "Example 3 — ambiguous, ask first:\n"
        'User: "Delete the event."\nAssistant: "You have three events today — which one '
        'should I delete?" (no mutating call until it\'s clear)'
    ),
    # Targets the lookup-shy failure portrait (LFM2.5 wave, 2026-06-10): chains stopping
    # after the read step, web.search on known facts, and clarifying instead of looking up.
    "tool_first": (
        "You are a helpful assistant on a Mac with access to tools. "
        "Answer chit-chat and facts you already know directly, without tools — never "
        "search the web for things you know. When a request needs action but is missing "
        "a detail (which event, which contact, which file), first use a read-only tool "
        "(list/search/find) to look it up; only ask the user when a lookup cannot "
        "resolve it, e.g. several contacts genuinely match. After each tool result, "
        "continue with the remaining steps until the whole request is done, then reply "
        "briefly."
    ),
}

# Router scaffold (decoding="router"): a cheap first pass classifies the request as
# act / answer / clarify BEFORE any tools are offered. answer/clarify requests then run
# tool-less (over-calling becomes structurally impossible); act requests run the normal
# agentic loop. This is shippable product architecture, not an eval-only trick.
ROUTER_PROMPT = (
    "You are a routing classifier for a Mac assistant. Read the user's request and pick:\n"
    '- "act": it asks for an action or device/personal information on this Mac that needs '
    "a tool (calendar, mail, messages, contacts, files, system settings, music, web, apps, "
    "reminders...).\n"
    '- "answer": it is chit-chat, opinion, math, or general knowledge the assistant can '
    "answer directly without any tool.\n"
    '- "clarify": it needs an action, but a crucial detail is missing or ambiguous and no '
    "tool lookup could resolve it.\n"
    'Reply with ONLY a JSON object: {"route": "act" | "answer" | "clarify"}'
)

_ROUTE_RE = re.compile(r'"route"\s*:\s*"(act|answer|clarify)"')

# Bump when interaction semantics change (simulator behavior, arg normalization) so stale
# cached interactions can't mix with new ones. Grader-only changes do NOT need a bump —
# grades are recomputed from cached interactions on every run.
# v3 (2026-06-10): sim handlers for dead-end read-only tools + honest default + list_dir
# path filter; task rewrite (chain_lookup_email). Grader any_of/contact rules shipped with
# it but wouldn't alone have required a bump.
# v4 (2026-06-11): assistant.ask_user tool (terminal clarify), messages.send recipient
# disambiguation, music.search seeded results, scalar→array coercion, per-call sim status.
# v5 (2026-06-11): mail.read_message handler + seeded bodies (reply/forward chains stalled
# on "no data"), Ollama provider recovers tool calls left unparsed in content.
HARNESS_VERSION = 5


@dataclass
class Cell:
    model: str
    retrieval: str
    decoding: str  # "constrained" | "unconstrained"
    prompt: str = "default"

    def label(self) -> str:
        return f"{self.model} | {self.retrieval} | {self.decoding} | {self.prompt}"


@dataclass
class RunRecord:
    task_id: str
    tier: str
    grade: GradeResult
    predicted_calls: list[dict] = field(default_factory=list)
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    turns: int = 0
    retrieval_recall: bool | None = None
    hit_turn_limit: bool = False
    error: str | None = None
    offered: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    cached: bool = False


def _catalog_hash(catalog: list[ToolSchema]) -> str:
    names = ",".join(sorted(t.name for t in catalog))
    return hashlib.sha256(names.encode()).hexdigest()[:12]


def _cache_key(cell: Cell, task: Task, seed: int, k: int, cat_hash: str) -> str:
    blob = json.dumps(
        {"cell": asdict(cell), "task": task.id, "seed": seed, "k": k, "cat": cat_hash,
         "v": HARNESS_VERSION},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


class Runner:
    def __init__(
        self,
        provider: Provider,
        retriever: Retriever,
        catalog: list[ToolSchema],
        *,
        seed: int = 42,
        k: int = 8,
        turn_cap: int = DEFAULT_TURN_CAP,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        prompt_label: str = "default",
        constrained: bool = False,
        decoding: str | None = None,
        cache_dir: Path | None = Path("results/cache"),
    ):
        self.provider = provider
        self.retriever = retriever
        self.catalog = catalog
        self.seed = seed
        self.k = k
        self.turn_cap = turn_cap
        self.system_prompt = system_prompt
        self.prompt_label = prompt_label
        # `decoding` supersedes the older constrained flag; both kept for compat
        self.decoding = decoding or ("constrained" if constrained else "unconstrained")
        self.constrained = self.decoding == "constrained"
        self.cache_dir = cache_dir
        self._cat_hash = _catalog_hash(catalog)
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def cell(self) -> Cell:
        return Cell(
            model=self.provider.name,
            retrieval=self.retriever.name,
            decoding=self.decoding,
            prompt=self.prompt_label,
        )

    def run_task(self, task: Task) -> RunRecord:
        offered = self.retriever.select(task.query, self.catalog, self.k)
        offered_names = [t.name for t in offered]
        offered_by_name = {t.name: t for t in offered}

        gold = {c.name for c in task.expect.calls}
        recall = (all(g in offered_by_name for g in gold) if gold else None)

        interaction = self._load_or_run(task, offered, offered_names)

        predicted = [ToolCall(c["name"], c.get("arguments", {})) for c in interaction["predicted"]]
        statuses = [c.get("status", "ok") for c in interaction["predicted"]]
        grade = grade_task(task, predicted, offered_by_name, statuses=statuses)

        return RunRecord(
            task_id=task.id,
            tier=task.tier,
            grade=grade,
            predicted_calls=interaction["predicted"],
            latency_s=interaction["latency_s"],
            prompt_tokens=interaction["prompt_tokens"],
            completion_tokens=interaction["completion_tokens"],
            turns=interaction["turns"],
            retrieval_recall=recall,
            hit_turn_limit=interaction["hit_turn_limit"],
            error=interaction.get("error"),
            offered=offered_names,
            trace=interaction["trace"],
            cached=interaction.get("cached", False),
        )

    def _load_or_run(self, task: Task, offered: list[ToolSchema], offered_names: list[str]) -> dict:
        key = _cache_key(self.cell, task, self.seed, self.k, self._cat_hash)
        cache_fp = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cache_fp and cache_fp.exists():
            data = json.loads(cache_fp.read_text())
            data["cached"] = True
            return data

        data = self._run_loop(task, offered, offered_names)
        if cache_fp:
            cache_fp.write_text(json.dumps(data))
        return data

    def _route(self, messages: list[Msg]) -> tuple[str, Completion | None]:
        """Router first pass: classify act/answer/clarify with no tools offered.

        Robust to junk output — anything unparseable falls back to "act" (the open gate),
        so a flaky router can only ever cost extra restraint, never block real work.
        """
        probe = [Msg("system", ROUTER_PROMPT)] + [m for m in messages if m.role != "system"]
        try:
            comp = self.provider.complete(probe, [], False)
        except Exception:  # noqa: BLE001 — router failure must not kill the task
            return "act", None
        m = _ROUTE_RE.search(comp.text or "")
        if m:
            return m.group(1), comp
        low = (comp.text or "").lower()
        for r in ("clarify", "answer", "act"):
            if r in low:
                return r, comp
        return "act", comp

    def _run_loop(self, task: Task, offered: list[ToolSchema], offered_names: list[str]) -> dict:
        sim = Simulator(self.catalog, seed=self.seed)
        offered_by_name = {t.name: t for t in offered}
        messages: list[Msg] = [Msg("system", self.system_prompt)]
        messages += [Msg(m["role"], m.get("content", "")) for m in task.messages]

        predicted: list[dict] = []
        trace: list[dict] = []
        latency = 0.0
        ptoks = ctoks = 0
        hit_limit = False
        error: str | None = None
        turns = 0

        if self.decoding in ("router", "router2"):
            route, rcomp = self._route(messages)
            if rcomp is not None:
                latency += rcomp.latency_s
                ptoks += rcomp.prompt_tokens
                ctoks += rcomp.completion_tokens
            trace.append({"turn": 0, "route": route})
            # router: answer AND clarify run tool-less. Measured fatal: LFM routes most
            # well-specified singles/chains to clarify (tool_sel 0.147). router2 gates
            # only the answer route — the part the classifier gets ~95% right — and lets
            # clarify keep tools (the grader permits read-only lookups before asking).
            blocked = ("answer",) if self.decoding == "router2" else ("answer", "clarify")
            if route in blocked:
                offered = []

        for turn in range(self.turn_cap):
            turns = turn + 1
            try:
                comp = self.provider.complete(messages, offered, self.constrained)
            except Exception as e:  # noqa: BLE001 — one bad task must not abort the matrix
                error = f"{type(e).__name__}: {e}"
                trace.append({"turn": turns, "error": error})
                break
            latency += comp.latency_s
            ptoks += comp.prompt_tokens
            ctoks += comp.completion_tokens

            # The forgiving-runtime layer: coerce near-miss arg types before the simulator
            # and grader see them (a shipped dispatcher would do the same).
            calls = [normalize_args(c, offered_by_name.get(c.name)) for c in comp.tool_calls]

            turn_log: dict[str, Any] = {
                "turn": turns,
                "text": comp.text,
                "tool_calls": [c.to_dict() for c in calls],
            }

            if not calls:
                trace.append(turn_log)
                break

            # assistant.ask_user is terminal: the question IS the reply (the product would
            # render it and wait). Recorded but never simulated — there is no fake user.
            ask = next((c for c in calls if c.name == "assistant.ask_user"), None)
            if ask is not None:
                predicted.append({**ask.to_dict(), "status": "ok"})
                q = ask.arguments.get("question", "")
                opts = ask.arguments.get("options") or []
                turn_log["text"] = comp.text or (q + (f" Options: {opts}" if opts else ""))
                trace.append(turn_log)
                break

            messages.append(Msg("assistant", comp.text, tool_calls=calls))
            results = []
            for call in calls:
                result = sim.execute(call)
                try:
                    status = json.loads(result).get("status", "ok")
                except (json.JSONDecodeError, AttributeError):
                    status = "ok"
                # status travels with the call so the grader can forgive mutating
                # attempts the runtime rejected (no side effect happened)
                predicted.append({**call.to_dict(), "status": status})
                results.append({"call": call.to_dict(), "result": result})
                messages.append(Msg("tool", result, tool_name=call.name))
            turn_log["results"] = results
            trace.append(turn_log)
        else:
            hit_limit = True

        return {
            "predicted": predicted,
            "trace": trace,
            "latency_s": round(latency, 4),
            "prompt_tokens": ptoks,
            "completion_tokens": ctoks,
            "turns": turns,
            "hit_turn_limit": hit_limit,
            "error": error,
            "offered": offered_names,
            "cached": False,
        }
