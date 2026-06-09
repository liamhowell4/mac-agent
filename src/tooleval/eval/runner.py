"""Orchestrates the model × scaffold × task run via a real agentic loop.

The loop: model emits tool calls → simulator executes against seeded state → results are
fed back → repeat until the model emits no call (natural stop, and the win condition for
negative/ambiguous) or hits the turn cap (cap-hit = fail). We grade the call, not side effects.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..providers.base import Provider
from ..retrieval.base import Retriever
from ..tools.simulator import Simulator
from ..types import Msg, ToolCall, ToolSchema
from .grader import GradeResult, grade_task
from .task import Task

DEFAULT_TURN_CAP = 6
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant on a Mac with access to tools. "
    "Call a tool only when it is the right way to fulfill the user's request. "
    "If no tool applies, answer directly. If the request is ambiguous, ask a clarifying "
    "question instead of guessing. When you have what you need, stop calling tools and reply."
)


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
        {"cell": asdict(cell), "task": task.id, "seed": seed, "k": k, "cat": cat_hash},
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
        constrained: bool = False,
        cache_dir: Path | None = Path("results/cache"),
    ):
        self.provider = provider
        self.retriever = retriever
        self.catalog = catalog
        self.seed = seed
        self.k = k
        self.turn_cap = turn_cap
        self.system_prompt = system_prompt
        self.constrained = constrained
        self.cache_dir = cache_dir
        self._cat_hash = _catalog_hash(catalog)
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def cell(self) -> Cell:
        return Cell(
            model=self.provider.name,
            retrieval=self.retriever.name,
            decoding="constrained" if self.constrained else "unconstrained",
            prompt="default",
        )

    def run_task(self, task: Task) -> RunRecord:
        offered = self.retriever.select(task.query, self.catalog, self.k)
        offered_names = [t.name for t in offered]
        offered_by_name = {t.name: t for t in offered}

        gold = {c.name for c in task.expect.calls}
        recall = (all(g in offered_by_name for g in gold) if gold else None)

        interaction = self._load_or_run(task, offered, offered_names)

        predicted = [ToolCall(c["name"], c.get("arguments", {})) for c in interaction["predicted"]]
        grade = grade_task(task, predicted, offered_by_name)

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

    def _run_loop(self, task: Task, offered: list[ToolSchema], offered_names: list[str]) -> dict:
        sim = Simulator(self.catalog, seed=self.seed)
        messages: list[Msg] = [Msg("system", self.system_prompt)]
        messages += [Msg(m["role"], m.get("content", "")) for m in task.messages]

        predicted: list[dict] = []
        trace: list[dict] = []
        latency = 0.0
        ptoks = ctoks = 0
        hit_limit = False
        error: str | None = None
        turns = 0

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

            turn_log: dict[str, Any] = {
                "turn": turns,
                "text": comp.text,
                "tool_calls": [c.to_dict() for c in comp.tool_calls],
            }

            if not comp.tool_calls:
                trace.append(turn_log)
                break

            messages.append(Msg("assistant", comp.text, tool_calls=comp.tool_calls))
            results = []
            for call in comp.tool_calls:
                predicted.append(call.to_dict())
                result = sim.execute(call)
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
