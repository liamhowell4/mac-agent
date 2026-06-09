"""Task + Expectation dataclasses with JSON (de)serialization.

Kept decoupled from the runner so a future fine-tune pipeline can reuse the exact format.
See docs/build-brief.md §7.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ExpectKind = Literal["tool_call", "no_call", "clarify", "chain"]
ArgMatchRule = Literal["exact", "normalized", "present", "semantic"]


@dataclass
class ExpectedCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    # per-field rule; "*" value means any/unchecked. Default to "present" for unlisted keys.
    arg_match: dict[str, ArgMatchRule] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> ExpectedCall:
        return cls(name=d["name"], args=d.get("args", {}), arg_match=d.get("arg_match", {}))


@dataclass
class Expectation:
    kind: ExpectKind
    calls: list[ExpectedCall] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Expectation:
        return cls(kind=d["kind"], calls=[ExpectedCall.from_dict(c) for c in d.get("calls", [])])


@dataclass
class Task:
    id: str
    tier: str  # single | chain | ambiguous | negative  (a reporting label; `expect` drives grading)
    messages: list[dict[str, Any]]
    expect: Expectation
    catalog: list[str] = field(default_factory=list)  # gold-relevant tools (for recall/scoping)
    notes: str = ""

    @property
    def query(self) -> str:
        """Last user message — used as the retrieval query."""
        for m in reversed(self.messages):
            if m.get("role") == "user":
                return m.get("content", "")
        return self.messages[-1].get("content", "") if self.messages else ""

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        return cls(
            id=d["id"],
            tier=d["tier"],
            messages=d["messages"],
            expect=Expectation.from_dict(d["expect"]),
            catalog=d.get("catalog", []),
            notes=d.get("notes", ""),
        )


def load_tasks(pattern: str) -> list[Task]:
    """Load tasks from a glob. Each file is a JSON list of task objects (or a single task)."""
    tasks: list[Task] = []
    for fp in sorted(glob.glob(pattern)):
        data = json.loads(Path(fp).read_text())
        items = data if isinstance(data, list) else [data]
        tasks.extend(Task.from_dict(item) for item in items)
    _assert_unique_ids(tasks)
    return tasks


def _assert_unique_ids(tasks: list[Task]) -> None:
    seen: set[str] = set()
    for t in tasks:
        if t.id in seen:
            raise ValueError(f"Duplicate task id: {t.id}")
        seen.add(t.id)
