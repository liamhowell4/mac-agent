"""Programmatic graders — small, pure functions (reused downstream by a fine-tune pipeline).

Programmatic first; the LLM judge (M3) only rates the *quality* of clarify/no_call text.
See docs/build-brief.md §8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from ..types import ToolCall, ToolSchema
from .task import ExpectedCall, Task


@dataclass
class GradeResult:
    tier: str
    kind: str
    n_calls: int
    selection_ok: bool | None = None
    arg_valid: bool | None = None
    arg_correct: bool | None = None
    abstained: bool | None = None
    overcalled: bool | None = None
    chain_complete: bool | None = None
    needs_judge: bool = False
    passed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


# ---- argument matching -------------------------------------------------------

_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.I)


def _to_24h(s: str) -> str:
    m = _TIME_RE.match(s.strip().lower().replace(" ", ""))
    if not m:
        return s.strip().lower()
    hour = int(m.group(1))
    minute = m.group(2) or "00"
    ap = m.group(3)
    if ap == "pm" and hour < 12:
        hour += 12
    elif ap == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def _normalize(v: Any) -> str:
    s = str(v).strip().lower()
    t = _to_24h(s)
    if t != s:  # looked like a time
        return t
    return re.sub(r"[\s_\-]+", "", s)


def _contact_fields(expected_name: str) -> set[str]:
    """All identifiers (name/email/phone/id) of seeded contacts whose name contains
    `expected_name` — deterministic, since the sim state is fixed."""
    from ..tools.simulator import SimState  # noqa: PLC0415 — avoid import cycle at module load

    want = _normalize(expected_name)
    out: set[str] = set()
    for c in SimState.seeded().contacts:
        if want in _normalize(c["name"]):
            out.update(_normalize(v) for v in c.values())
    return out


def match_value(predicted: Any, expected: Any, rule: str) -> bool:
    if expected == "*" or rule == "present":
        return True  # presence handled by caller; value unchecked
    if rule == "exact":
        return predicted == expected or str(predicted) == str(expected)
    if rule == "normalized":
        return _normalize(predicted) == _normalize(expected)
    if rule == "contact":
        # A model that resolves "Daisy" → "Daisy Wong" / her email / her phone via
        # contacts.find is MORE correct than one passing the literal string through.
        pred = _normalize(predicted)
        return (_normalize(expected) in pred) or pred in _contact_fields(str(expected))
    return True  # "semantic" → deferred to judge, not failed programmatically


def match_call_args(predicted_args: dict, expected: ExpectedCall) -> tuple[bool, bool]:
    """Returns (args_ok, needs_judge) for one matched call."""
    needs_judge = False
    for key, exp_val in expected.args.items():
        rule = expected.arg_match.get(key, "present")
        if key not in predicted_args:
            return False, needs_judge
        if rule == "semantic":
            needs_judge = True
            continue
        if not match_value(predicted_args[key], exp_val, rule):
            return False, needs_judge
    return True, needs_judge


def schema_valid(args: dict, parameters: dict) -> bool:
    try:
        jsonschema.validate(instance=args, schema=parameters)
        return True
    except jsonschema.ValidationError:
        return False
    except jsonschema.SchemaError:
        return True  # a broken schema isn't the model's fault; don't penalize


# ---- ordered subsequence matching for tool_call / chain ----------------------

def _match_in_order(
    predicted: list[ToolCall], expected: list[ExpectedCall]
) -> list[tuple[ExpectedCall, ToolCall | None]]:
    """Greedily match each expected call to the next same-named predicted call."""
    pi = 0
    out: list[tuple[ExpectedCall, ToolCall | None]] = []
    for exp in expected:
        found: ToolCall | None = None
        accepted = {exp.name, *exp.any_of}
        while pi < len(predicted):
            if predicted[pi].name in accepted:
                found = predicted[pi]
                pi += 1
                break
            pi += 1
        out.append((exp, found))
    return out


# ---- top-level grader --------------------------------------------------------

def grade_task(
    task: Task,
    predicted_calls: list[ToolCall],
    offered_by_name: dict[str, ToolSchema],
    statuses: list[str] | None = None,
) -> GradeResult:
    kind = task.expect.kind
    n = len(predicted_calls)
    statuses = statuses or ["ok"] * n

    if kind in ("no_call", "clarify"):
        if kind == "clarify":
            # Read-only lookups are the *ideal* path to discovering ambiguity (find two
            # Sarahs → ask which); only a mutating call means the model guessed. A mutating
            # attempt the runtime REJECTED (status=error, e.g. ambiguous-recipient
            # disambiguation) caused no side effect — try, get blocked, then ask is good
            # product behavior. Negatives stay strict: any call at all is an overcall.
            mutating = [
                c for c, st in zip(predicted_calls, statuses, strict=False)
                if st != "error"
                and not (offered_by_name.get(c.name) and offered_by_name[c.name].read_only)
            ]
            abstained = len(mutating) == 0
        else:
            abstained = n == 0
        return GradeResult(
            tier=task.tier, kind=kind, n_calls=n,
            abstained=abstained,
            overcalled=(not abstained) if task.tier == "negative" else None,
            needs_judge=(kind == "clarify"),
            passed=abstained,  # quality of the question/answer confirmed by judge in M3
            detail={"predicted": [c.to_dict() for c in predicted_calls]},
        )

    # tool_call (single) and chain — ordered subsequence
    matched = _match_in_order(predicted_calls, task.expect.calls)
    found_all = all(found is not None for _, found in matched)

    arg_valid = True
    arg_correct = True
    needs_judge = False
    for exp, found in matched:
        if found is None:
            arg_correct = False
            continue
        schema = offered_by_name.get(found.name)
        if schema is not None and not schema_valid(found.arguments, schema.parameters):
            arg_valid = False
        if found.name != exp.name:
            continue  # an any_of alternate; exp.args were written for the primary tool
        ok, nj = match_call_args(found.arguments, exp)
        needs_judge |= nj
        if not ok:
            arg_correct = False

    chain_complete = found_all and arg_correct if kind == "chain" else None
    passed = found_all and arg_valid and arg_correct
    return GradeResult(
        tier=task.tier, kind=kind, n_calls=n,
        selection_ok=found_all,
        arg_valid=arg_valid,
        arg_correct=arg_correct,
        chain_complete=chain_complete,
        needs_judge=needs_judge,
        passed=passed,
        detail={
            "predicted": [c.to_dict() for c in predicted_calls],
            "expected": [{"name": e.name, "args": e.args} for e in task.expect.calls],
        },
    )
