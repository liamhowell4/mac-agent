"""Metric definitions & aggregation. See docs/build-brief.md §9.

Aggregated per cell (model × retrieval × decoding × prompt), overall and per tier.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import RunRecord


def _rate(vals: list[bool]) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    idx = min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1))))
    return round(xs[idx], 4)


def _block(records: list[RunRecord]) -> dict:
    return {
        "n": len(records),
        "tool_selection_accuracy": _rate([r.grade.selection_ok for r in records]),
        "arg_validity_rate": _rate([r.grade.arg_valid for r in records]),
        "arg_correctness_rate": _rate([r.grade.arg_correct for r in records]),
        "chain_completion_rate": _rate([r.grade.chain_complete for r in records]),
        "overcall_rate": _rate([r.grade.overcalled for r in records]),
        "clarify_rate": _rate(
            [r.grade.passed for r in records if r.grade.kind == "clarify"]
        ),
        "retrieval_recall": _rate([r.retrieval_recall for r in records]),
        "task_pass_rate": _rate([r.grade.passed for r in records]),
        "latency_s_p50": _percentile([r.latency_s for r in records], 50),
        "latency_s_p95": _percentile([r.latency_s for r in records], 95),
        "completion_tokens_mean": (
            round(sum(r.completion_tokens for r in records) / len(records), 1)
            if records else None
        ),
        "turn_limit_hits": sum(1 for r in records if r.hit_turn_limit),
    }


def aggregate(records: list[RunRecord]) -> dict:
    """Overall + per-tier metric blocks for one cell."""
    by_tier: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        by_tier[r.tier].append(r)
    return {
        "overall": _block(records),
        "per_tier": {tier: _block(rs) for tier, rs in sorted(by_tier.items())},
    }
